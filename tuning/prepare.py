"""Reshape a recorded activity into the kind of input the converter actually gets.

A real FIT is barometric elevation at 1 Hz, roughly a point every three metres.
A real uploaded GPX is a planned route: DEM elevation rounded to whole metres,
a point every 10-50 m. Those two produce visibly different gradients through
calculate_gradient's 70 m window, so tuning against the raw FIT would calibrate
the model for an input shape it never sees in production.

So the recorded track is resampled, its elevation quantized, and the result
serialized to GPX and fed back through `gpx_reader.parse_gpx_bytes` — the real
production entry point. That last step is deliberate: `distance_from_start` in
this project is cumulative *3D* distance (gpxpy's `distance_3d`, so gradients
are rise over slope-distance, not rise over run), and rather than reimplement
that convention and hope it matches, the harness just uses it.

Resampling keeps real recorded points rather than interpolating between them,
so every reference timestamp is a value the athlete actually produced.
"""

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from xml.sax.saxutils import escape

from gpx2fit.core.gpx_reader import parse_gpx_bytes
from gpx2fit.core.models import SportType, Track, TrackPoint
from tuning.fit_reader import ReferenceActivity, UnreadableActivity


# Default point spacing for the resampled track, in metres — mid-range for a
# planned GPX, and comfortably under calculate_gradient's 70 m window so the
# smoothing still has several points to work with.
DEFAULT_SPACING_M = 15.0

# DEM elevation in a planned GPX is whole metres; this reproduces that rounding.
DEFAULT_ELEVATION_STEP_M = 1.0

# A leg slower than this, sustained for at least the minimum below, is the
# athlete standing still rather than moving slowly, even if the watch never
# auto-paused. 0.25 m/s is 0.9 km/h — below any real walking pace, including a
# steep scramble.
DEFAULT_STANDING_SPEED_MPS = 0.25
DEFAULT_STANDING_MIN_SECONDS = 25.0

# A gap longer than this between consecutive records, outside a timer pause, is
# a GPS dropout rather than normal recording. Also, relative to the file's own
# cadence (see _dropout_threshold): watches record at 1 Hz but "smart
# recording" can be far sparser, and a fixed threshold would either miss real
# dropouts in the dense files or reject the sparse ones wholesale.
DEFAULT_MAX_GAP_SECONDS = 30.0
_DROPOUT_CADENCE_FACTOR = 10.0

# Faster than this is a GPS teleport, not a runner.
DEFAULT_MAX_SPEED_MPS = 10.0

# Below this a leg counts as taking no time at all — see _leg_speeds.
_MIN_LEG_SECONDS = 1e-6

# Rejection floors.
_MIN_POINTS = 50
_MIN_DISTANCE_M = 500.0
_MIN_MOVING_SECONDS = 120.0
_MAX_SENTINEL_ELEVATION_SHARE = 0.5
_MAX_DROPOUT_SHARE = 0.1
_MAX_SPIKE_SHARE = 0.02


@dataclass
class PreparedActivity:
    """One activity, reshaped into a model input plus the reference it's judged against.

    Attributes:
        name: Identifier for reports.
        sport: Which sport's constants the model should use.
        track: The production-shaped track, straight out of `parse_gpx_bytes`,
            with `distance_from_start` set and every timestamp None — exactly
            what `combine()` expects to be handed.
        reference_elapsed: The real moving-time seconds since the start, one
            per point of `track`, strictly increasing and starting at 0.0.
        gpx_bytes: The synthesized GPX the track was parsed from, so the same
            input can be dropped into the browser GUI for an eyeball check.
        source_points: How many recorded points the activity started with.
        excised_seconds: Total time removed as stops (timer pauses plus
            detected standing).
        notes: Quality observations that didn't warrant rejecting the file.
            Surfaced in reports so a suspect activity stays visible.
    """
    name: str
    sport: SportType
    track: Track
    reference_elapsed: list[float]
    gpx_bytes: bytes
    source_points: int
    excised_seconds: float
    notes: list[str] = field(default_factory=list)

    @property
    def moving_seconds(self) -> float:
        """The activity's total moving time, which is what the model has to distribute."""
        return self.reference_elapsed[-1]

    @property
    def distance_m(self) -> float:
        """Total route distance on the model's own 3D basis."""
        return self.track.total_distance


def _leg_speeds(points: list[TrackPoint], elapsed: list[float]) -> list[float]:
    """Per-leg speed in m/s, 0.0 where no time passed.

    The epsilon matters: removing a standing run collapses its legs to zero
    duration, and float subtraction can leave a picosecond behind. Dividing a
    few metres by that would manufacture a GPS spike out of a stop.
    """
    return [
        (later.distance_from_start - earlier.distance_from_start) / (late_t - early_t)
        if late_t - early_t > _MIN_LEG_SECONDS else 0.0
        for earlier, later, early_t, late_t in zip(points, points[1:], elapsed, elapsed[1:])
    ]


def _pause_seconds_before(pauses: list[tuple[datetime, datetime]], moment: datetime) -> float:
    """Total timer-pause time that has elapsed by `moment`.

    A moment inside a pause counts only the part of it already served, so a
    record written mid-pause still lands at the pause's own start in moving time.
    """
    total = 0.0
    for stop, resume in pauses:
        if moment >= resume:
            total += (resume - stop).total_seconds()
        elif moment > stop:
            total += (moment - stop).total_seconds()
    return total


def _detect_standing_runs(
    points: list,
    elapsed: list[float],
    standing_speed_mps: float,
    standing_min_seconds: float,
) -> list[tuple[int, int]]:
    """Find runs of consecutive legs the athlete spent standing still.

    Returns (first_leg_index, last_leg_index) inclusive for each run whose legs
    are all below `standing_speed_mps` and which lasts at least
    `standing_min_seconds`. Short slow patches — a gate, a road crossing, a
    steep step — fall below the duration floor and are left alone, since they
    are part of how the route actually paces.
    """
    speeds = _leg_speeds(points, elapsed)
    runs: list[tuple[int, int]] = []
    start: int | None = None

    for index, speed in enumerate(speeds):
        if speed < standing_speed_mps:
            if start is None:
                start = index
            continue
        if start is not None:
            if elapsed[index] - elapsed[start] >= standing_min_seconds:
                runs.append((start, index - 1))
            start = None

    if start is not None and elapsed[-1] - elapsed[start] >= standing_min_seconds:
        runs.append((start, len(speeds) - 1))

    return runs


def _moving_elapsed(activity: ReferenceActivity, standing_speed_mps: float, standing_min_seconds: float
                    ) -> tuple[list[float], float, list[str]]:
    """Seconds of *moving* time at each recorded point, with every stop removed.

    Timer pauses come straight from the device's own events. Standing that the
    watch never auto-paused is detected and removed the same way, so a lunch
    break is never charged to the surrounding legs' pace. What's left is a pure
    moving-time curve, which is what the pacing model predicts: stops are a
    separate, already-working mechanism and not what this harness measures.

    Returns:
        The per-point moving elapsed seconds, the total time removed, and any
        notes worth carrying into the report.
    """
    points = activity.track.points
    notes: list[str] = []

    raw_start = points[0].timestamp
    assert raw_start is not None  # read_reference_activity guarantees every point has one.

    # Timer pauses first.
    elapsed = []
    for point in points:
        assert point.timestamp is not None
        wall_seconds = (point.timestamp - raw_start).total_seconds()
        elapsed.append(wall_seconds - _pause_seconds_before(activity.pauses, point.timestamp))
    excised = activity.paused_seconds

    # Then standing the device never paused for.
    runs = _detect_standing_runs(points, elapsed, standing_speed_mps, standing_min_seconds)
    if runs:
        standing_seconds = sum(elapsed[end + 1] - elapsed[start] for start, end in runs)
        shifted = list(elapsed)
        removed_so_far = 0.0
        run_index = 0
        for index in range(1, len(elapsed)):
            if run_index < len(runs) and runs[run_index][0] < index <= runs[run_index][1] + 1:
                removed_so_far += elapsed[index] - elapsed[index - 1]
                if index == runs[run_index][1] + 1:
                    run_index += 1
            shifted[index] = elapsed[index] - removed_so_far
        elapsed = shifted
        excised += standing_seconds
        notes.append(
            f"removed {len(runs)} unpaused standing stretch(es) totalling {standing_seconds / 60:.1f} min"
        )

    if activity.paused_seconds > 0:
        notes.append(f"removed {len(activity.pauses)} timer pause(s) totalling {activity.paused_seconds / 60:.1f} min")

    return elapsed, excised, notes


def _dropout_threshold(elapsed: list[float], max_gap_seconds: float) -> float:
    """How long a gap has to be, in this file, to count as a recording dropout.

    Whichever is larger of the absolute floor and a multiple of the file's own
    median leg time. A 1 Hz watch and one using smart recording produce very
    different normal cadences, and judging both against one fixed number would
    either miss real dropouts in the dense file or throw out the sparse one
    entirely.
    """
    legs = [later - earlier for earlier, later in zip(elapsed, elapsed[1:]) if later > earlier]
    if not legs:
        return max_gap_seconds
    return max(max_gap_seconds, _DROPOUT_CADENCE_FACTOR * statistics.median(legs))


def _check_quality(
    activity: ReferenceActivity,
    elapsed: list[float],
    max_gap_seconds: float,
    max_speed_mps: float,
) -> list[str]:
    """Reject unusable activities; return notes for the merely suspect ones.

    Every rejection says which file and which measurement failed, so a dropped
    corpus file can be diagnosed rather than silently disappearing.

    Raises:
        UnreadableActivity: If the activity is too short, has no usable
            elevation, or is too full of dropouts or GPS spikes to trust.
    """
    name = activity.name
    points = activity.track.points
    notes: list[str] = []

    if len(points) < _MIN_POINTS:
        raise UnreadableActivity(f"{name}: only {len(points)} points, need at least {_MIN_POINTS}.")

    distance = points[-1].distance_from_start
    if distance < _MIN_DISTANCE_M:
        raise UnreadableActivity(f"{name}: only {distance:.0f} m long, need at least {_MIN_DISTANCE_M:.0f} m.")

    if elapsed[-1] < _MIN_MOVING_SECONDS:
        raise UnreadableActivity(
            f"{name}: only {elapsed[-1]:.0f} s of moving time after stops were removed, "
            f"need at least {_MIN_MOVING_SECONDS:.0f} s."
        )

    # Elevation: 0.0 is this project's "no data" sentinel (see gpx_reader).
    sentinel = sum(1 for point in points if point.elevation == 0.0)
    if sentinel > _MAX_SENTINEL_ELEVATION_SHARE * len(points):
        raise UnreadableActivity(
            f"{name}: {sentinel}/{len(points)} points have no elevation, so gradients can't be computed."
        )
    elevations = {round(point.elevation) for point in points}
    if len(elevations) < 2:
        raise UnreadableActivity(f"{name}: elevation never changes, so there is no gradient to model.")

    # Dropouts: a long gap in *moving* time, i.e. one the timer didn't explain.
    threshold = _dropout_threshold(elapsed, max_gap_seconds)
    dropout_seconds = sum(
        later - earlier
        for earlier, later in zip(elapsed, elapsed[1:])
        if later - earlier > threshold
    )
    if dropout_seconds > _MAX_DROPOUT_SHARE * elapsed[-1]:
        raise UnreadableActivity(
            f"{name}: {dropout_seconds / 60:.1f} min of recording gaps out of "
            f"{elapsed[-1] / 60:.1f} min moving — too much missing data to pace against."
        )
    if dropout_seconds > 0:
        notes.append(f"{dropout_seconds / 60:.1f} min of recording gaps")

    # GPS spikes.
    speeds = _leg_speeds(points, elapsed)
    spikes = sum(1 for speed in speeds if speed > max_speed_mps)
    if spikes > _MAX_SPIKE_SHARE * len(speeds):
        raise UnreadableActivity(
            f"{name}: {spikes}/{len(speeds)} legs exceed {max_speed_mps:.0f} m/s — the GPS trace is unusable."
        )
    if spikes:
        notes.append(f"{spikes} leg(s) above {max_speed_mps:.0f} m/s")

    # Cross-check against the device's own summary. A mismatch doesn't
    # invalidate the trace (a footpod measures distance differently from GPS),
    # but it's worth seeing in the report.
    if activity.totals.distance_m:
        drift = abs(distance - activity.totals.distance_m) / activity.totals.distance_m
        if drift > 0.1:
            notes.append(
                f"extracted distance {distance / 1000:.2f} km differs "
                f"{drift * 100:.0f}% from the device's {activity.totals.distance_m / 1000:.2f} km"
            )

    return notes


def _resample_indexes(points: list, spacing_m: float, lo: int, hi: int) -> list[int]:
    """Indexes of the points to keep, roughly every `spacing_m` between `lo` and `hi` inclusive.

    Selection, never interpolation: each kept index is a real recorded point
    that keeps its own real timestamp, so no reference timing is ever invented.
    The first and last of the range are always kept so the span is exact.
    """
    kept = [lo]
    last_distance = points[lo].distance_from_start
    for index in range(lo + 1, hi):
        if points[index].distance_from_start - last_distance >= spacing_m:
            kept.append(index)
            last_distance = points[index].distance_from_start
    kept.append(hi)
    return kept


def _to_gpx_bytes(points: list, elevation_step_m: float, name: str) -> bytes:
    """Serialize points as a GPX track, with elevation quantized like DEM data.

    Deliberately emits no <time> elements: a planned route doesn't have them,
    and `parse_gpx_bytes` ignores them anyway — the reference timing is carried
    separately, not smuggled through the file the model sees.
    """
    segments = "\n".join(
        f'<trkpt lat="{point.lat:.7f}" lon="{point.lon:.7f}">'
        f"<ele>{round(point.elevation / elevation_step_m) * elevation_step_m:.1f}</ele></trkpt>"
        for point in points
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="gpx2fit-tuning" xmlns="https://www.topografix.com/GPX/1/1">\n'
        f"<trk><name>{escape(name)}</name><trkseg>\n{segments}\n</trkseg></trk>\n</gpx>\n"
    )
    return document.encode("utf-8")


def prepare_reference(
    activity: ReferenceActivity,
    spacing_m: float = DEFAULT_SPACING_M,
    elevation_step_m: float = DEFAULT_ELEVATION_STEP_M,
    trim_km: tuple[float, float] | None = None,
    standing_speed_mps: float = DEFAULT_STANDING_SPEED_MPS,
    standing_min_seconds: float = DEFAULT_STANDING_MIN_SECONDS,
    max_gap_seconds: float = DEFAULT_MAX_GAP_SECONDS,
    max_speed_mps: float = DEFAULT_MAX_SPEED_MPS,
) -> PreparedActivity:
    """Turn a recorded activity into a production-shaped model input plus its reference timing.

    Args:
        activity: The decoded activity, from `fit_reader.read_reference_activity`.
        spacing_m: Target point spacing for the resampled track.
        elevation_step_m: Elevation is rounded to a multiple of this, emulating
            the whole-metre DEM data a planned GPX carries.
        trim_km: Optional (start, end) distance range in kilometres, so only
            the usable part of an activity is kept — an interval session's
            steady portion, or a route before the athlete got lost.
        standing_speed_mps: Legs slower than this are candidates for stop removal.
        standing_min_seconds: How long a slow run must last to count as a stop.
        max_gap_seconds: Gaps longer than this count as recording dropouts.
        max_speed_mps: Legs faster than this count as GPS spikes.

    Returns:
        The prepared activity.
    Raises:
        UnreadableActivity: If a quality gate rejects it, if `trim_km` selects
            too little of the route, or if the surviving reference timing
            doesn't strictly increase.
    """
    points = activity.track.points
    elapsed, excised_seconds, notes = _moving_elapsed(activity, standing_speed_mps, standing_min_seconds)
    notes = _check_quality(activity, elapsed, max_gap_seconds, max_speed_mps) + notes

    if activity.teleports_dropped:
        notes.insert(0, f"dropped {activity.teleports_dropped} point(s) at an unreachable position")
    if activity.nonmonotonic_dropped:
        notes.insert(0, f"dropped {activity.nonmonotonic_dropped} point(s) with a backward timestamp")
    if activity.sport_overridden:
        notes.insert(0, f"sport forced to {activity.sport.value} (the file declared {activity.fit_sport})")

    lo, hi = 0, len(points) - 1
    if trim_km is not None:
        start_m, end_m = trim_km[0] * 1000, trim_km[1] * 1000
        inside = [i for i, point in enumerate(points) if start_m <= point.distance_from_start <= end_m]
        if len(inside) < _MIN_POINTS:
            raise UnreadableActivity(
                f"{activity.name}: --trim {trim_km[0]}:{trim_km[1]} keeps only {len(inside)} points, "
                f"need at least {_MIN_POINTS}."
            )
        lo, hi = inside[0], inside[-1]
        notes.append(f"trimmed to {trim_km[0]:.2f}-{trim_km[1]:.2f} km of the recorded route")

    kept = _resample_indexes(points, spacing_m, lo, hi)
    kept_points = [points[index] for index in kept]
    reference = [elapsed[index] - elapsed[kept[0]] for index in kept]

    # Resampling can't create time when none passed, but a dropout or a
    # trailing standing run can leave two kept points sharing a moment. The
    # comparison divides by leg time, so those have to go.
    deduped_points = [kept_points[0]]
    deduped_reference = [reference[0]]
    for point, seconds in zip(kept_points[1:], reference[1:]):
        if seconds > deduped_reference[-1]:
            deduped_points.append(point)
            deduped_reference.append(seconds)
    if len(deduped_points) < len(kept_points):
        notes.append(f"dropped {len(kept_points) - len(deduped_points)} point(s) with no elapsed time")

    if len(deduped_points) < _MIN_POINTS:
        raise UnreadableActivity(
            f"{activity.name}: only {len(deduped_points)} points survived preparation, "
            f"need at least {_MIN_POINTS}."
        )

    gpx_bytes = _to_gpx_bytes(deduped_points, elevation_step_m, activity.name)
    track = parse_gpx_bytes(gpx_bytes, device="gpx2fit-tuning")
    track.sport = activity.sport
    track.activity_name = activity.name

    return PreparedActivity(
        name=activity.name,
        sport=activity.sport,
        track=track,
        reference_elapsed=deduped_reference,
        gpx_bytes=gpx_bytes,
        source_points=len(points),
        excised_seconds=excised_seconds,
        notes=notes,
    )
