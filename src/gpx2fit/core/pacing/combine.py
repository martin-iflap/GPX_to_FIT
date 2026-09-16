"""Fit per-leg pacing speeds to known anchor timestamps and stamp them onto a track."""

import math
import statistics
from bisect import bisect_left, bisect_right
from datetime import timedelta

from gpx2fit.core.models import Anchor, InputError, ModeAStop, SportType, Track, TrackPoint
from gpx2fit.core.pacing.gradient import calculate_gradient, minetti_speeds_from_gradients, tobler_speeds_from_gradients

# Above this workout-average speed, a HIKING activity is paced like a run (Minetti)
# rather than a walk (Tobler) — see combine()'s docstring for why.
HIKING_TOBLER_THRESHOLD_MPS = 1.8

# A surface multiplier can never drag a leg's speed below this fraction of
# its gradient-modeled speed — see _pace_segment.
_MIN_SURFACE_MULTIPLIER = 0.05

# A leg's final speed (gradient model * surface multiplier) is
# pulled toward the segment's own median leg speed, asymptotically bounded
# to within roughly this factor of it in either direction — see
# _compress_speed_toward_typical. Real pace doesn't swing within one
# activity by the 10-30x range the raw gradient curves alone can produce.
_MAX_SPEED_RATIO = 2.5

# Human-readable label for each Anchor.source, used only to make InputError
# messages (contradictory anchor/stop times) point at a concrete, recognizable
# entry rather than an opaque distance/timestamp pair.
_ANCHOR_SOURCE_LABELS = {
    "user": "anchor",
    "photo": "photo anchor",
    "stop_arrival": "stop arrival",
    "stop_departure": "stop departure",
}

def _describe_anchor(anchor: Anchor) -> str:
    """Human-readable description of an anchor for InputError messages, e.g. "photo anchor at 5.30 km (2026-09-13 14:02)"."""
    label = _ANCHOR_SOURCE_LABELS.get(anchor.source, "anchor")
    return f"{label} at {anchor.distance_from_start / 1000:.2f} km ({anchor.timestamp:%Y-%m-%d %H:%M})"


def _resolve_pacing_model(total_elevation_gain: int, average_speed: float, length: float) -> None:
    """"""
    # todo: implement a better model determining logic here.
    pass


def _compress_speed_toward_typical(speed: float, typical_speed: float, max_ratio: float) -> float:
    """Pull `speed` toward `typical_speed`, asymptotically bounded to within `max_ratio` of it either way.

    Works in log-space so the bound is symmetric for speeding up and slowing
    down: `tanh` maps a ratio near 1 (log-ratio near 0) to itself almost
    unchanged, and maps arbitrarily large or small ratios to a value that
    gets closer and closer to, but never reaches, `max_ratio` (or its
    reciprocal). This is deliberately not a hard min/max clamp which could
    produce constant-speed plateaus.

    Args:
        speed: Must be > 0.
        typical_speed: Must be > 0.
        max_ratio: Must be > 1.
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
    sport: SportType,
    workout_avg_speed_mps: float,
    segment_multipliers: list[float] | None = None,
    segment_mode_a_stops: list[ModeAStop] | None = None,
) -> None:
    """Assign timestamps to one anchor-to-anchor segment's points, in place.

    Picks a per-leg relative speed model (Minetti or Tobler), compresses
    each leg's resulting speed toward the segment's own median leg speed
    (see _MAX_SPEED_RATIO / _compress_speed_toward_typical). Then scales the
    modeled per-leg times so the segment's total *active* time (anchor-to-anchor
    duration minus any Mode A stop durations in this segment) matches exactly,
    and stamps each point's timestamp accordingly. Each Mode A stop's duration
    is then added, at its own point, to every later timestamp in the
    segment.

    Args:
        segment_points: Points between start_anchor and end_anchor, inclusive,
            in route order. A Mode A stop in this segment must already
            appear as a duplicated, zero-distance point pair (see
            pacing/stops.py's expand_track_with_stops) — the first of the
            pair becomes its arrival, the second its departure.
        segment_gradients: This segment's per-leg gradients (N-1 values for
            N points), sliced from the whole track's smoothed gradients so the
            smoothing window isn't cut off at this segment's anchors.
        start_anchor: Anchor at the beginning of this segment.
        end_anchor: Anchor at the end of this segment.
        sport: Sport type, used to pick the speed model.
        workout_avg_speed_mps: Average speed over the whole workout (not just
            this segment) — used only to decide between Minetti and Tobler for HIKING.
        segment_multipliers: Optional per-leg speed multipliers, one for each leg
            (N-1 values for N points). If provided, the per-leg speeds are multiplied
            by the corresponding multiplier before scaling to match the segment's
            anchor-to-anchor duration (now the multipliers are surface based).
        segment_mode_a_stops: This segment's Mode A stops, sorted ascending
            by distance_from_start.

    Note:
        Beyond the two boundary points (always stamped from the anchors
        themselves), does nothing to the segment's interior if the segment
        has fewer than 2 points, if the chosen model yields no speeds, or if
        the modeled time comes out zero/negative (e.g. every leg in the
        segment has zero real distance) — in that case the interior points
        are left with whatever timestamp they already had (typically None).
        See combine()'s docstring for when this can happen and why it's safe.

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
        return

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

    if sport == SportType.RUNNING:
        speeds = minetti_speeds_from_gradients(segment_gradients)
    elif sport == SportType.HIKING and workout_avg_speed_mps > HIKING_TOBLER_THRESHOLD_MPS:
        speeds = minetti_speeds_from_gradients(segment_gradients)
    else:
        speeds = tobler_speeds_from_gradients(segment_gradients)
    if not speeds:
        return

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

    # Pull each leg's speed toward this segment's own typical pace.
    positive_speeds = [speed for speed in speeds if speed > 0]
    if positive_speeds:
        typical_speed = statistics.median(positive_speeds)
        speeds = [
            _compress_speed_toward_typical(speed, typical_speed, _MAX_SPEED_RATIO) if speed > 0 else speed
            for speed in speeds
        ]

    leg_distances = [
        curr.distance_from_start - prev.distance_from_start
        for prev, curr in zip(segment_points, segment_points[1:])
    ]
    modeled_leg_times = [
        (distance / speed) if speed > 0 else 0.0
        for distance, speed in zip(leg_distances, speeds)
    ]
    modeled_total_time = sum(modeled_leg_times)
    if modeled_total_time <= 0:
        return

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


def _resolve_anchor_bounds(track_points: list[TrackPoint], anchors: list[Anchor]) -> list[int]:
    """Resolve each anchor to an index of the first track point that matches its distance.

    Each anchor is matched against ``track_points`` using its
    ``distance_from_start`` value. For an anchor with a unique distance
    among the anchors, the index of the first track point with that distance is returned.

    Multiple consecutive anchors may intentionally share the same
    ``distance_from_start`` (for example, an arrival and departure anchor
    for a stop). When this happens, and there are enough track points at
    that distance to give each anchor its own point, the anchors are
    assigned distinct track-point indices in order.

    If there are not enough matching track points for a group of anchors
    sharing a distance the function raises a ValueError.

    Args:
        track_points: Track points to match against. The points must be
            sorted by ``distance_from_start`` because binary search is
            used to locate matching points.
        anchors: Anchors to resolve. Anchors sharing the same
            ``distance_from_start`` must be consecutive if they are
            intended to form a single duplicate-distance group.

    Returns:
        A list of track-point indices of the first track point
        that matches each anchor's distance, one for each anchor.
    Raises:
        ValueError: If there are not enough track points to assign each anchor a distinct index.
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
) -> Track:
    """Stamp every track point with a timestamp, paced to match the given anchors.

    Anchors split the track into consecutive segments (anchors[0] to
    anchors[1], anchors[1] to anchors[2], ...), each with a known duration.
    Within each segment, per-leg gradients drive a relative speed model
    (see pacing/gradient.py):

    - RUNNING always uses the Minetti energy-cost model.
    - HIKING uses Minetti too, but only if the *whole workout's* average
      speed (total distance / total anchor-to-anchor time) is brisk enough
      (> HIKING_TOBLER_THRESHOLD_MPS) to look more like a run than a walk;
      otherwise it uses Tobler's hiking function.

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
        sport: Sport type used to select the speed model.
        multipliers: Optional per-leg speed multipliers, aligned to `track`
            as passed here (N-1 values for `track`'s N points) — if `track`
            has already been expanded with stop points (see
            pacing.stops.expand_track_with_stops), the multipliers must be
            expanded the same way first (pacing.stops.expand_multipliers_with_stops)
            before being passed in. Calculated by pacing.surface module from
            a Valhalla trace_attributes response. If provided, the per-leg
            speeds are multiplied by the corresponding multiplier before
            scaling to match the segment's anchor-to-anchor duration.
        mode_a_stops: Mode A stops (distance + duration only, arrival not
            yet known), sorted ascending by distance_from_start.

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
    workout_total_time = (anchors[-1].timestamp - anchors[0].timestamp).total_seconds()
    workout_avg_speed_mps = track.total_distance / workout_total_time if workout_total_time > 0 else 0.0

    mode_a_stops = mode_a_stops or []
    anchor_indexes = _resolve_anchor_bounds(track.points, anchors)
    buckets = _bucket_mode_a_stops(anchors, mode_a_stops)
    # Calculated once over the whole track: a mid-route anchor is a known
    # time, not a break in the terrain, so each segment's gradient smoothing
    # should still see the route on the far side of its anchors.
    gradients = calculate_gradient(track)
    for i in range(len(anchors) - 1):
        segment_points = track.points[anchor_indexes[i]: anchor_indexes[i + 1] + 1]
        segment_gradients = gradients[anchor_indexes[i]: anchor_indexes[i + 1]]
        segment_multipliers = multipliers[anchor_indexes[i]: anchor_indexes[i + 1]] if multipliers is not None else None
        _pace_segment(
            segment_points, segment_gradients, anchors[i], anchors[i + 1], sport, workout_avg_speed_mps,
            segment_multipliers, buckets[i],
        )

    return track

# todo:
# _MAX_SPEED_RATIO is a first-guess constant (2.5) - it needs real tuning,
# and ideally should vary by sport/terrain rather than being one fixed number.
