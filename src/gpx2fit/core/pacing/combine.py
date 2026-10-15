"""Fit per-leg pacing speeds to known anchor timestamps and stamp them onto a track."""

import math
import statistics
from bisect import bisect_left, bisect_right
from datetime import timedelta

from gpx2fit.core.models import Anchor, AnchorSource, InputError, ModeAStop, SportType, Track, TrackPoint
from gpx2fit.core.pacing.acceleration import MAX_ACCELERATION_MPS2, limit_acceleration
from gpx2fit.core.pacing.curve_selection import (
    DEFAULT_SMOOTHNESS,
    SMOOTHNESS_SCALES,
    resolve_curve_shape,
    resolve_max_speed_ratio,
    resolve_tobler_weight,
)
from gpx2fit.core.pacing.gradient import CurveShape, blended_speeds_from_gradients, calculate_gradient
from gpx2fit.core.pacing.variation import pace_variation, route_noise


# A surface multiplier can never drag a leg's speed below this fraction of
# its gradient-modeled speed — see _base_speeds.
_MIN_SURFACE_MULTIPLIER = 0.05

# Relative speeds are floored here before compression — see _pace_segment.
# Any positive value works: the compression bounds the leg to the segment's
# median / max_speed_ratio however small it is.
_MIN_RELATIVE_SPEED = 1e-9

# _limit_leg_times stops once a round grows its rescale factor by less than
# this share, which leaves the limit broken by at most about twice that.
_LIMIT_FACTOR_TOLERANCE = 1e-12
# Safety net on its rounds; a real segment settles in a handful.
_MAX_LIMIT_ROUNDS = 100

# A segment whose terrain-adjusted pace is this many times faster or slower
# than the whole workout's is reported (see _implausible_pace_warnings).
IMPLAUSIBLE_PACE_RATIO = 2.5

# Segments shorter than this are never judged: anchor times are entered to the
# minute, which on a short segment is a big share of its time.
PLAUSIBILITY_MIN_SEGMENT_M = 300.0

# Anchor times that leave a segment's moving time at a higher average speed
# than this are refused (see _pace_segment). It is far past any speed on foot,
# so it only catches a mistyped time, never a fast athlete. It also keeps the
# legs well inside what a FIT record's speed can hold (65.5 m/s, 236 km/h),
# which would otherwise fail in fit_encoder as a traceback. Like the pace
# warnings, it skips segments under PLAUSIBILITY_MIN_SEGMENT_M: a photo taken a
# second after the start but matched 50 m along is GPS error, not a mistake.
MAX_PLAUSIBLE_SPEED_KMH = 60.0

# Human-readable label for each Anchor.source, used only to make InputError
# messages (contradictory anchor/stop times) point at a concrete, recognizable
# entry rather than an opaque distance/timestamp pair.
_ANCHOR_SOURCE_LABELS: dict[AnchorSource, str] = {
    "start": "start",
    "end": "end",
    "user": "anchor",
    "photo": "photo anchor",
    "stop_arrival": "stop arrival",
    "stop_departure": "stop departure",
}

def _describe_anchor(anchor: Anchor) -> str:
    """Human-readable description of an anchor for InputError messages, e.g. "photo anchor at 5.30 km (2026-09-13 14:02)"."""
    label = _ANCHOR_SOURCE_LABELS[anchor.source]
    if anchor.source in ("start", "end"):
        # Its place is implied, and "the start at 0.00 km" only adds noise.
        return f"{label} ({anchor.timestamp:%Y-%m-%d %H:%M})"
    return f"{label} at {anchor.distance_from_start / 1000:.2f} km ({anchor.timestamp:%Y-%m-%d %H:%M})"


def _total_stop_seconds(anchors: list[Anchor], mode_a_stops: list[ModeAStop]) -> float:
    """Total time this workout spends stopped rather than moving.

    A Mode A stop contributes its duration directly; a Mode B stop arrives as
    two anchors at the same distance with different timestamps. `anchors` must
    be sorted by ascending distance_from_start.
    """
    seconds = sum(stop.duration.total_seconds() for stop in mode_a_stops)
    for previous, current in zip(anchors, anchors[1:]):
        if current.distance_from_start == previous.distance_from_start:
            seconds += (current.timestamp - previous.timestamp).total_seconds()
    return seconds


def _compress_speed_toward_typical(speed: float, typical_speed: float, max_ratio: float) -> float:
    """Pull `speed` toward `typical_speed`, asymptotically bounded to within `max_ratio` of it either way.

    Works in log-space so the bound is symmetric for speeding up and slowing
    down: `tanh` maps a ratio near 1 (log-ratio near 0) to itself almost
    unchanged, and maps arbitrarily large or small ratios to a value that
    gets closer and closer to, but never reaches, `max_ratio` (or its
    reciprocal). This is deliberately not a hard min/max clamp which could
    produce constant-speed plateaus.

    Both speeds must be > 0 and `max_ratio` > 1.
    """
    log_limit = math.log(max_ratio)
    log_ratio = math.log(speed / typical_speed)
    compressed_log_ratio = log_limit * math.tanh(log_ratio / log_limit)
    return typical_speed * math.exp(compressed_log_ratio)


def _limit_leg_times(
    leg_distances: list[float],
    leg_seconds: list[float],
    active_time: float,
    max_acceleration: float,
) -> list[float]:
    """Leg times whose speeds respect `max_acceleration` and still sum to `active_time`.

    Acceleration needs absolute speeds, which a segment only has once its
    modeled times are scaled to the anchors. Limiting only slows legs, so it
    lengthens the total, and the rescale that puts the total back speeds every
    leg up by one factor k ≥ 1. That steepens every change of speed by k², so
    the limit is applied at max_acceleration / k² instead, which the rescale
    brings back to exactly max_acceleration. k depends on what the limit
    slowed, so it is found by fixed-point iteration from k = 1. Each round
    limits the original speeds again, and k only grows. Ramps are a tiny
    share of a segment, so k is barely above 1 and this settles in a few
    rounds. A segment the limit doesn't touch keeps its times bit for bit.
    """
    speeds = [distance / seconds if seconds > 0 else 0.0 for distance, seconds in zip(leg_distances, leg_seconds)]
    limited = limit_acceleration(leg_distances, speeds, max_acceleration)
    if limited == speeds:
        return leg_seconds

    factor = 1.0
    for _ in range(_MAX_LIMIT_ROUNDS):
        limited_seconds = [
            seconds if new == old else distance / new
            for distance, seconds, new, old in zip(leg_distances, leg_seconds, limited, speeds)
        ]
        new_factor = sum(limited_seconds) / active_time
        converged = new_factor <= factor * (1 + _LIMIT_FACTOR_TOLERANCE)
        factor = new_factor
        if converged:
            break
        limited = limit_acceleration(leg_distances, speeds, max_acceleration / (factor * factor))
    return [seconds / factor for seconds in limited_seconds]


def _base_speeds(
    gradients: list[float],
    tobler_weight: float,
    curve_shape: CurveShape,
    multipliers: list[float] | None,
) -> list[float]:
    """Each leg's modeled speed from its terrain alone: the blended curve times its surface multiplier.

    This is what the pace variation judges steadiness on and what every
    segment is paced from, so it is built once, here, for both.

    Args:
        gradients: Per-leg gradients, from calculate_gradient.
        tobler_weight: The workout's blend of the two curves (see
            pacing.curve_selection.resolve_tobler_weight).
        curve_shape: Both curves' exponents (see
            pacing.curve_selection.resolve_curve_shape).
        multipliers: Per-leg surface multipliers, one per gradient, or None
            for none. Each is floored at _MIN_SURFACE_MULTIPLIER, so a surface
            can slow a leg but never stop it.
    Returns:
        One relative speed per leg, 1.0 on flat ground of a neutral surface.
    """
    speeds = blended_speeds_from_gradients(gradients, tobler_weight, curve_shape)
    if multipliers is None:
        return speeds
    return [speed * max(multiplier, _MIN_SURFACE_MULTIPLIER) for speed, multiplier in zip(speeds, multipliers)]


def _pace_segment(
    segment_points: list[TrackPoint],
    segment_speeds: list[float],
    start_anchor: Anchor,
    end_anchor: Anchor,
    max_speed_ratio: float,
    segment_mode_a_stops: list[ModeAStop],
    max_acceleration: float | None,
) -> tuple[float, float] | None:
    """Assign timestamps to one anchor-to-anchor segment's points, in place.

    Compresses each leg's relative speed toward the segment's own median leg
    speed (see _compress_speed_toward_typical).
    Then scales the modeled per-leg times so the segment's total *active* time
    (anchor-to-anchor duration minus any Mode A stop durations in this segment)
    matches exactly. Ramps any change of speed steeper than `max_acceleration`
    (see _limit_leg_times), and stamps each point's timestamp accordingly. Each Mode A
    stop's duration is then added, at its own point, to every later timestamp
    in the segment.

    Args:
        segment_points: Points from start_anchor to end_anchor inclusive, in
            route order. A Mode A stop in this segment must already appear as a
            duplicated, zero-distance point pair (see pacing.stops
            expand_track_with_stops) — the first of the pair becomes its
            arrival, the second its departure.
        segment_speeds: This segment's per-leg relative speeds, one per leg,
            sliced from the whole track's (_base_speeds times any pace
            variation), so neither the gradient window nor the variation is
            cut off at this segment's anchors. Compressed here, so the bound
            holds for the variation too.
        start_anchor: Anchor at the beginning of this segment.
        end_anchor: Anchor at the end of this segment.
        max_speed_ratio: Soft bound on how far a leg's speed may swing from
            the segment's median, resolved once for the whole workout by
            pacing.curve_selection.resolve_max_speed_ratio. Must be > 1.
        segment_mode_a_stops: This segment's Mode A stops, sorted ascending
            by distance_from_start.
        max_acceleration: The steepest change of speed between two legs, in
            m/s² (see pacing.acceleration). None leaves every change instant.
            Stops still start and end instantly either way.

    Returns:
        (active_seconds, modeled_time) for a segment it paced, or None when
        it bailed out early (see Note). modeled_time is in flat-equivalent
        metres, since the curves are normalized to 1.0 on the flat, so
        active_seconds / modeled_time is the segment's terrain-adjusted pace
        (see _implausible_pace_warnings).

    Note:
        Beyond the two boundary points (always stamped from the anchors
        themselves), leaves the segment's interior untouched if it has fewer
        than 2 points or if the modeled time comes out zero (e.g. every leg has
        zero real distance) — those points keep whatever timestamp they had,
        typically None. See combine()'s docstring for why that's safe.

    Raises:
        InputError: If end_anchor's timestamp isn't strictly later than
            start_anchor's — anchors are sorted by ascending distance before
            reaching here, so this means two anchors' timestamps contradict
            their distance order (e.g. a photo anchor built from a capture
            time that doesn't actually fall within the activity). Also
            raised if this segment's Mode A stop durations alone consume
            the entire anchor-to-anchor time budget, or leave too little to
            cover the segment below MAX_PLAUSIBLE_SPEED_KMH (judged from
            PLAUSIBILITY_MIN_SEGMENT_M up). All are
            user-fixable (bad anchor/stop times), not bugs.
    """
    if len(segment_points) < 2:
        return None

    # Stamped unconditionally, before any of the degenerate-model checks below can bail out early.
    segment_points[0].timestamp = start_anchor.timestamp
    segment_points[-1].timestamp = end_anchor.timestamp

    anchor_total_time = (end_anchor.timestamp - start_anchor.timestamp).total_seconds()
    if anchor_total_time <= 0:
        raise InputError(
            f"The {_describe_anchor(end_anchor)} isn't later in time than the {_describe_anchor(start_anchor)}, "
            "even though it's further along the route. Anchor and stop times must increase in the same order as "
            "their distance along the route — check the times you entered for these two points and fix whichever "
            "one is wrong."
        )

    # A curve can underflow to 0.0 on an absurd gradient.
    # Floored, the compression below lifts it
    # to the slowest speed the bound allows instead.
    speeds = [max(speed, _MIN_RELATIVE_SPEED) for speed in segment_speeds]

    # Pull each leg's speed toward this segment's own typical pace.
    typical_speed = statistics.median(speeds)
    speeds = [_compress_speed_toward_typical(speed, typical_speed, max_speed_ratio) for speed in speeds]

    leg_distances = [
        curr.distance_from_start - prev.distance_from_start
        for prev, curr in zip(segment_points, segment_points[1:])
    ]
    modeled_leg_times = [distance / speed for distance, speed in zip(leg_distances, speeds)]
    modeled_total_time = sum(modeled_leg_times)
    if modeled_total_time <= 0:
        return None

    total_stop_seconds = sum(s.duration.total_seconds() for s in segment_mode_a_stops)
    active_time = anchor_total_time - total_stop_seconds
    if active_time <= 0:
        raise InputError(
            f"The stop(s) between the {_describe_anchor(start_anchor)} and the {_describe_anchor(end_anchor)} "
            "take longer than the time available between those two points. Shorten the stop duration(s), or "
            "adjust the anchor/stop times so there's enough time for them."
        )

    distance = segment_points[-1].distance_from_start - segment_points[0].distance_from_start
    speed_kmh = distance / active_time * 3.6
    if distance >= PLAUSIBILITY_MIN_SEGMENT_M and speed_kmh > MAX_PLAUSIBLE_SPEED_KMH:
        after_stops = ", after the stops between them," if segment_mode_a_stops else ""
        raise InputError(
            f"The times of the {_describe_anchor(start_anchor)} and the {_describe_anchor(end_anchor)} leave "
            f"{distance / 1000:.2f} km to cover at {speed_kmh:.0f} km/h{after_stops}, which is faster than anyone "
            "moves on foot. Check the times you entered for these two points (a mistyped duration, say) and fix "
            "whichever one is wrong."
        )

    scale = active_time / modeled_total_time
    leg_seconds = [modeled_leg_time * scale for modeled_leg_time in modeled_leg_times]
    if max_acceleration is not None:
        leg_seconds = _limit_leg_times(leg_distances, leg_seconds, active_time, max_acceleration)

    elapsed = 0.0
    extra = 0.0
    stop_index = 0
    for index, seconds in enumerate(leg_seconds, start=1):
        elapsed += seconds
        point_timestamp = start_anchor.timestamp + timedelta(seconds=elapsed + extra)

        if (
            stop_index < len(segment_mode_a_stops)
            and leg_distances[index - 1] == 0.0
            and segment_points[index - 1].distance_from_start == segment_mode_a_stops[stop_index].distance_from_start
        ):
            duration_seconds = segment_mode_a_stops[stop_index].duration.total_seconds()
            point_timestamp += timedelta(seconds=duration_seconds)
            extra += duration_seconds
            stop_index += 1

        segment_points[index].timestamp = point_timestamp

    return active_time, modeled_total_time


def _implausible_pace_warnings(segments: list[tuple[Anchor, Anchor, float, float, float]]) -> list[str]:
    """User-facing warnings for segments whose anchors imply an implausible pace.

    Each entry is (start_anchor, end_anchor, distance, active_seconds,
    modeled_time) for one paced segment. A segment's terrain-adjusted pace is
    active_seconds / modeled_time (seconds per flat-equivalent metre), so a
    slow climb doesn't count against it. It is compared with the whole
    workout's, Σactive_seconds / Σmodeled_time. Typical cause: a photo whose
    camera clock was in another timezone, still inside the activity window.

    One wrong anchor makes the segment before it off one way and the one after
    it off the other way. That pair is reported once, naming just the shared
    anchor, since that is the time to check.
    """
    # A lone segment (start and end only) is the workout, at a ratio of exactly 1.
    total_modeled = sum(modeled for *_, modeled in segments)
    if total_modeled <= 0:
        return []
    workout_pace = sum(active for *_, active, _ in segments) / total_modeled

    # (start_anchor, end_anchor, ratio) per flagged segment; ratio > 1 is faster than the workout.
    flagged: list[tuple[Anchor, Anchor, float]] = []
    for start_anchor, end_anchor, distance, active_seconds, modeled_time in segments:
        if distance < PLAUSIBILITY_MIN_SEGMENT_M:
            continue
        ratio = workout_pace / (active_seconds / modeled_time)
        if ratio > IMPLAUSIBLE_PACE_RATIO or ratio < 1 / IMPLAUSIBLE_PACE_RATIO:
            flagged.append((start_anchor, end_anchor, ratio))

    def how(rat: float) -> str:
        return f"about {rat:.1f}× faster" if rat > 1 else f"about {1 / rat:.1f}× slower"

    warnings = []
    i = 0
    while i < len(flagged):
        start_anchor, end_anchor, ratio = flagged[i]
        if i + 1 < len(flagged):
            next_start, _, next_ratio = flagged[i + 1]
            if next_start is end_anchor and (ratio > 1) != (next_ratio > 1):
                warnings.append(
                    f"The {_describe_anchor(end_anchor)} makes the pace before it come out {how(ratio)} and the "
                    f"pace after it {how(next_ratio)} than the activity as a whole, even allowing for the terrain. "
                    "Check its time: a photo whose camera clock was in another timezone, say."
                )
                i += 2
                continue
        hint = " If you took a long break there, add it as a stop instead." if ratio < 1 else ""
        warnings.append(
            f"Between the {_describe_anchor(start_anchor)} and the {_describe_anchor(end_anchor)}, the pace comes "
            f"out {how(ratio)} than the activity as a whole, even allowing for the terrain. If one of those two "
            f"times is off (a photo whose camera clock was in another timezone, say), fix it.{hint}"
        )
        i += 1
    return warnings


def _resolve_anchor_bounds(track_points: list[TrackPoint], anchors: list[Anchor]) -> list[int]:
    """Map each anchor to the index of the track point at its distance_from_start.

    Consecutive anchors may intentionally share a distance (a stop's arrival
    and departure); each is then given its own point from the run of track
    points at that distance, in order. `track_points` must be sorted by
    distance_from_start (binary search), and anchors sharing a distance must be
    consecutive.

    Raises:
        ValueError: If a group of anchors sharing a distance outnumbers the
            track points at that distance.
    """
    distances = [p.distance_from_start for p in track_points]
    anchor_indexes = [0] * len(anchors)

    i = 0
    while i < len(anchors):
        j = i
        while j + 1 < len(anchors) and anchors[j + 1].distance_from_start == anchors[i].distance_from_start:
            j += 1
        run_size = j - i + 1 # number of anchors sharing this distance

        run_lo = bisect_left(distances, anchors[i].distance_from_start) # index of first point with this distance
        run_hi = bisect_right(distances, anchors[i].distance_from_start) - 1 # index of last point with this distance
        run_len = run_hi - run_lo + 1 # number of points sharing this distance

        if run_len >= run_size:
            for k in range(run_size):
                anchor_indexes[i + k] = run_lo + k
        else:
            raise ValueError("Number of points sharing a distance is less than the number of anchors sharing that distance.")

        i = j + 1

    return anchor_indexes


def _bucket_mode_a_stops(anchors: list[Anchor], mode_a_stops: list[ModeAStop]) -> list[list[ModeAStop]]:
    """Bucket sorted Mode A stops into the anchor-to-anchor segment each falls in.

    Both `anchors` and `mode_a_stops` must be sorted ascending by
    distance_from_start. A stop's distance never coincides with an anchor's
    (resolve_stops rejects that), so a single forward pointer unambiguously
    assigns every stop to exactly one segment; a stop can never land in a
    zero-length segment (e.g. a Mode B stop's own arrival/departure pair)
    since such a segment's hi_distance was already passed by the pointer
    in a prior iteration.
    """
    buckets: list[list[ModeAStop]] = [[] for _ in range(len(anchors) - 1)]
    stop_index = 0
    for i in range(len(anchors) - 1):
        hi_distance = anchors[i + 1].distance_from_start
        while stop_index < len(mode_a_stops) and mode_a_stops[stop_index].distance_from_start < hi_distance:
            buckets[i].append(mode_a_stops[stop_index])
            stop_index += 1
    return buckets


def combine(
    track: Track,
    anchors: list[Anchor],
    sport: SportType,
    multipliers: list[float] | None = None,
    mode_a_stops: list[ModeAStop] | None = None,
    smoothness: int = DEFAULT_SMOOTHNESS,
    vary_pace: bool = True,
    acceleration_limit: bool = True,
    *,
    warnings: list[str] | None = None,
) -> Track:
    """Stamp every track point with a timestamp, paced to match the given anchors.

    Anchors split the track into consecutive segments (anchors[0] to
    anchors[1], anchors[1] to anchors[2], ...), each with a known duration.
    Within each segment, per-leg gradients drive a relative speed model (see
    pacing/gradient.py): a blend of the Minetti running curve and the Tobler
    walking one, mixed according to how slow and how steep this workout is.
    That blend is resolved **once, for the whole workout** (see
    pacing.curve_selection.resolve_tobler_weight), and then used by every
    segment.

    The modeled per-leg times are then scaled by a single per-segment factor
    so their sum matches that segment's *active* time (anchor-to-anchor
    duration minus any Mode A stop durations falling in that segment)
    exactly, each Mode A stop's duration is added to every later timestamp
    in its segment, and each point's timestamp is stamped in place
    (track.points is mutated; the return value is the same object, for
    convenience chaining).

    Args:
        track: Track whose points already have distance_from_start (and
            ideally elevation) set. A Mode A or Mode B stop must already be
            represented as a duplicated, zero-distance point pair in
            track.points (see pacing/stops.py's expand_track_with_stops).
            Mutated in place.
        anchors: Two or more Anchors, sorted by ascending distance_from_start,
            spanning the track from its first point to its last.
        sport: Sport type, which biases the Minetti/Tobler blend — see
            pacing.curve_selection.TOBLER_THRESHOLDS.
        multipliers: Optional per-leg surface speed multipliers from
            pacing.surface, aligned to `track` as passed here (N-1 values for
            its N points) — if `track` has already been expanded with stop
            points, the multipliers must be expanded the same way first (see
            pacing.stops.expand_multipliers_with_stops).
        mode_a_stops: Mode A stops (distance + duration only, arrival not
            yet known), sorted ascending by distance_from_start.
        smoothness: The user's pace-smoothness level (1–10, higher is more
            even), which narrows or widens the automatic speed-swing bound —
            see pacing.curve_selection.resolve_max_speed_ratio. It never
            changes which curve is used. It scales the pace variation by the
            same factor.
        vary_pace: Add pacing.variation wander to stretches whose modeled
            speed would otherwise stay constant (a flat road, a long even
            climb). Built once over the whole track and laid out by distance,
            like the gradients. False paces on the gradient model alone.
        acceleration_limit: Ramp every change of speed (a surface change, a
            hilltop) so it is never steeper than the sport's
            pacing.acceleration.MAX_ACCELERATION_MPS2. Each segment is limited
            on its own, so the change of pace at an anchor, and a stop's
            start and end, stay instant. False leaves every change instant.
        warnings: If given, a user-facing message is appended for each
            segment of at least PLAUSIBILITY_MIN_SEGMENT_M whose anchors make
            its terrain-adjusted pace more than IMPLAUSIBLE_PACE_RATIO times
            faster or slower than the workout's. The pacing itself is the same
            either way.

    Returns:
        The same track, with every point that falls inside a valid segment
        now timestamped.
    Raises:
        IndexError: If anchors is empty.
        InputError: If any two consecutive anchors don't have strictly
            increasing timestamps (a sign that an anchor's timestamp doesn't
            actually belong on this track — e.g. a photo anchor built from a
            capture time outside the activity, see pacing.photo_anchors), or
            if a segment's Mode A stop durations alone consume its entire
            anchor-to-anchor time budget, or if a segment's average moving
            speed would exceed MAX_PLAUSIBLE_SPEED_KMH. All are user-fixable
            data problems, not bugs.
        ValueError: If `multipliers` doesn't hold one entry per leg of
            `track`, or if _resolve_anchor_bounds can't map every anchor to its
            own track point (see its docstring) — this should never happen
            for anchors built via pacing.anchors.build_user_anchors, which
            already rejects two anchors resolving to the same distance.
    """
    mode_a_stops = mode_a_stops or []
    if multipliers is not None and len(multipliers) != len(track.points) - 1:
        # Checked on the whole list: one entry too many still slices into segments of the right length.
        raise ValueError(
            f"Got {len(multipliers)} surface multipliers for a track of {len(track.points) - 1} legs."
        )
    anchor_indexes = _resolve_anchor_bounds(track.points, anchors)
    buckets = _bucket_mode_a_stops(anchors, mode_a_stops)
    # Calculated once over the whole track: a mid-route anchor is a known
    # time, not a break in the terrain, so each segment's gradient smoothing
    # should still see the route on the far side of its anchors.
    gradients = calculate_gradient(track)

    workout_total_time = (anchors[-1].timestamp - anchors[0].timestamp).total_seconds()
    active_seconds = workout_total_time - _total_stop_seconds(anchors, mode_a_stops)
    leg_distances = [
        curr.distance_from_start - prev.distance_from_start
        for prev, curr in zip(track.points, track.points[1:])
    ]
    tobler_weight = resolve_tobler_weight(gradients, leg_distances, active_seconds, sport)
    max_speed_ratio = resolve_max_speed_ratio(gradients, leg_distances, sport, smoothness)
    curve_shape = resolve_curve_shape(max_speed_ratio, sport)
    speeds = _base_speeds(gradients, tobler_weight, curve_shape, multipliers)
    if vary_pace:
        # Judged across anchors too, since an anchor is a known time, not a change in the terrain.
        variation = pace_variation(leg_distances, speeds, route_noise(track.points), SMOOTHNESS_SCALES[smoothness])
        speeds = [speed * factor for speed, factor in zip(speeds, variation)]
    max_acceleration = MAX_ACCELERATION_MPS2[sport] if acceleration_limit else None

    paced_segments = []
    for i in range(len(anchors) - 1):
        lo, hi = anchor_indexes[i], anchor_indexes[i + 1]
        fit = _pace_segment(
            track.points[lo: hi + 1], speeds[lo: hi], anchors[i], anchors[i + 1], max_speed_ratio, buckets[i],
            max_acceleration,
        )
        if fit is not None:
            distance = anchors[i + 1].distance_from_start - anchors[i].distance_from_start
            paced_segments.append((anchors[i], anchors[i + 1], distance, *fit))

    if warnings is not None:
        warnings.extend(_implausible_pace_warnings(paced_segments))
    return track


# todo:
# Implausible pace ratio is a first guess.
# cycling will need its own max plausible speed.
