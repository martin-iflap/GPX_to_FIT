"""Fit per-leg pacing speeds to known anchor timestamps and stamp them onto a track."""

import math
import statistics
from bisect import bisect_left, bisect_right
from datetime import timedelta

from gpx2fit.core.models import Anchor, InputError, ModeAStop, SportType, Track, TrackPoint
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
# its gradient-modeled speed — see _pace_segment.
_MIN_SURFACE_MULTIPLIER = 0.05

# Relative speeds are floored here before compression — see _pace_segment.
# Any positive value works: the compression bounds the leg to the segment's
# median / max_speed_ratio however small it is.
_MIN_RELATIVE_SPEED = 1e-9

# A segment whose terrain-adjusted pace is this many times faster or slower
# than the whole workout's is reported (see _implausible_pace_warnings).
IMPLAUSIBLE_PACE_RATIO = 2.5

# Segments shorter than this are never judged: anchor times are entered to the
# minute, which on a short segment is a big share of its time.
PLAUSIBILITY_MIN_SEGMENT_M = 300.0

# Human-readable label for each Anchor.source, used only to make InputError
# messages (contradictory anchor/stop times) point at a concrete, recognizable
# entry rather than an opaque distance/timestamp pair.
_ANCHOR_SOURCE_LABELS = {
    "start": "start",
    "end": "end",
    "user": "anchor",
    "photo": "photo anchor",
    "stop_arrival": "stop arrival",
    "stop_departure": "stop departure",
}

def _describe_anchor(anchor: Anchor) -> str:
    """Human-readable description of an anchor for InputError messages, e.g. "photo anchor at 5.30 km (2026-09-13 14:02)"."""
    label = _ANCHOR_SOURCE_LABELS.get(anchor.source, "anchor")
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


def _pace_segment(
    segment_points: list[TrackPoint],
    segment_gradients: list[float],
    start_anchor: Anchor,
    end_anchor: Anchor,
    tobler_weight: float,
    max_speed_ratio: float,
    curve_shape: CurveShape,
    segment_multipliers: list[float] | None = None,
    segment_mode_a_stops: list[ModeAStop] | None = None,
    segment_variation: list[float] | None = None,
) -> tuple[float, float] | None:
    """Assign timestamps to one anchor-to-anchor segment's points, in place.

    Computes a per-leg relative speed from the workout's blend of the Minetti
    and Tobler curves, applies any surface multipliers and pace variation,
    compresses each leg's resulting speed toward the segment's
    own median leg speed (see _compress_speed_toward_typical).
    Then scales the modeled per-leg times so the segment's total *active* time
    (anchor-to-anchor duration minus any Mode A stop durations in this segment)
    matches exactly, and stamps each point's timestamp accordingly. Each Mode A
    stop's duration is then added, at its own point, to every later timestamp
    in the segment.

    Args:
        segment_points: Points from start_anchor to end_anchor inclusive, in
            route order. A Mode A stop in this segment must already appear as a
            duplicated, zero-distance point pair (see pacing.stops
            expand_track_with_stops) — the first of the pair becomes its
            arrival, the second its departure.
        segment_gradients: This segment's per-leg gradients, sliced from the
            whole track's smoothed gradients so the smoothing window isn't cut
            off at this segment's anchors.
        start_anchor: Anchor at the beginning of this segment.
        end_anchor: Anchor at the end of this segment.
        tobler_weight: How far this workout's speed curve sits between Minetti
            (0.0) and Tobler (1.0), resolved once for the whole workout by
            pacing.curve_selection.resolve_tobler_weight.
        max_speed_ratio: Soft bound on how far a leg's speed may swing from
            the segment's median, resolved once for the whole workout by
            pacing.curve_selection.resolve_max_speed_ratio. Must be > 1.
        curve_shape: Both curves' exponents, fitted to max_speed_ratio by
            pacing.curve_selection.resolve_curve_shape.
        segment_multipliers: Optional per-leg surface multipliers, one per leg.
        segment_mode_a_stops: This segment's Mode A stops, sorted ascending
            by distance_from_start.
        segment_variation: Optional per-leg pace-variation multipliers, one
            per leg, sliced from the whole track's (see pacing.variation).
            Applied before the compression, so its bound still holds.

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
            the entire anchor-to-anchor time budget. Both are user-fixable
            (bad anchor/stop times), not bugs.
    """
    segment_mode_a_stops = segment_mode_a_stops or []
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

    speeds = blended_speeds_from_gradients(segment_gradients, tobler_weight, curve_shape)
    if not speeds:
        return None

    if segment_multipliers is not None:
        if len(segment_multipliers) != len(speeds):
            raise ValueError(
                f"Length of segment_multipliers ({len(segment_multipliers)}) does not match number of legs ({len(speeds)})."
            )
        # Floored so a surface multiplier can never fully zero out a leg's speed.
        speeds = [
            speed * max(multiplier, _MIN_SURFACE_MULTIPLIER)
            for speed, multiplier in zip(speeds, segment_multipliers)
        ]

    if segment_variation is not None:
        speeds = [speed * variation for speed, variation in zip(speeds, segment_variation)]

    # A curve can underflow to 0.0 on an absurd gradient.
    # Floored, the compression below lifts it
    # to the slowest speed the bound allows instead.
    speeds = [max(speed, _MIN_RELATIVE_SPEED) for speed in speeds]

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

    scale = active_time / modeled_total_time

    elapsed = 0.0
    extra = 0.0
    stop_index = 0
    for index, modeled_leg_time in enumerate(modeled_leg_times, start=1):
        elapsed += modeled_leg_time * scale
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


def _whole_track_variation(
    track: Track,
    gradients: list[float],
    leg_distances: list[float],
    tobler_weight: float,
    curve_shape: CurveShape,
    multipliers: list[float] | None,
    smoothness: int,
) -> list[float]:
    """pacing.variation's per-leg multipliers for the whole track.

    Steadiness is judged on the same speeds _pace_segment starts from (the
    blended curve times the floored surface multipliers), across anchors, since
    an anchor is a known time and not a change in the terrain.
    """
    base_speeds = blended_speeds_from_gradients(gradients, tobler_weight, curve_shape)
    if multipliers is not None:
        base_speeds = [
            speed * max(multiplier, _MIN_SURFACE_MULTIPLIER) for speed, multiplier in zip(base_speeds, multipliers)
        ]
    return pace_variation(leg_distances, base_speeds, route_noise(track.points), SMOOTHNESS_SCALES[smoothness])


def combine(
    track: Track,
    anchors: list[Anchor],
    sport: SportType,
    multipliers: list[float] | None = None,
    mode_a_stops: list[ModeAStop] | None = None,
    smoothness: int = DEFAULT_SMOOTHNESS,
    vary_pace: bool = True,
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
            anchor-to-anchor time budget. Both are user-fixable data
            problems, not bugs.
        ValueError: If _resolve_anchor_bounds can't map every anchor to its
            own track point (see its docstring) — this should never happen
            for anchors built via pacing.anchors.build_user_anchors, which
            already rejects two anchors resolving to the same distance.
    """
    mode_a_stops = mode_a_stops or []
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
    variation = (
        _whole_track_variation(track, gradients, leg_distances, tobler_weight, curve_shape, multipliers, smoothness)
        if vary_pace
        else None
    )

    paced_segments = []
    for i in range(len(anchors) - 1):
        lo, hi = anchor_indexes[i], anchor_indexes[i + 1]
        fit = _pace_segment(
            track.points[lo: hi + 1], gradients[lo: hi], anchors[i], anchors[i + 1], tobler_weight, max_speed_ratio,
            curve_shape,
            multipliers[lo: hi] if multipliers is not None else None,
            buckets[i],
            variation[lo: hi] if variation is not None else None,
        )
        if fit is not None:
            distance = anchors[i + 1].distance_from_start - anchors[i].distance_from_start
            paced_segments.append((anchors[i], anchors[i + 1], distance, *fit))

    if warnings is not None:
        warnings.extend(_implausible_pace_warnings(paced_segments))
    return track


# todo:
# Possible optimization once the pacing logic settles: the Minetti curve is
# currently evaluated twice over the whole track - once inside
# resolve_tobler_weight as its probe, once inside blended_speeds_from_gradients
# per segment. Both could be computed once here, next to calculate_gradient,
# and the resulting speeds sliced per segment the way gradients already are;
# _pace_segment would then take segment speeds instead of tobler_weight and
# stop knowing curve names at all. Measured cost of the duplicate pass is only
# ~3 ms per 10k legs, so this is about structure, not speed. And it does mean
# threading a speeds list through three call sites, so it is worth doing only
# if _pace_segment comes out simpler for it.

# We also compute the blended_speeds_from_gradients twice per segment: once for the whole track in
# _whole_track_variation, once inside _pace_segment.

# Implausible pace ratio is a first guess.
