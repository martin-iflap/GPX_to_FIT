"""Fit per-leg pacing speeds to known anchor timestamps and stamp them onto a track."""

from bisect import bisect_left, bisect_right
from datetime import timedelta

from gpx2fit.core.models import Anchor, ModeAStop, SportType, Track, TrackPoint
from gpx2fit.core.pacing.gradient import calculate_minetti_speeds, calculate_tobler_speeds

# Above this workout-average speed, a HIKING activity is paced like a run (Minetti)
# rather than a walk (Tobler) — see combine()'s docstring for why.
HIKING_TOBLER_THRESHOLD_MPS = 1.8


def _pace_segment(
    segment_points: list[TrackPoint],
    start_anchor: Anchor,
    end_anchor: Anchor,
    sport: SportType,
    workout_avg_speed_mps: float,
    segment_mode_a_stops: list[ModeAStop] | None = None,
) -> None:
    """Assign timestamps to one anchor-to-anchor segment's points, in place.

    Picks a per-leg relative speed model (Minetti or Tobler),
    then scales the modeled per-leg times so the segment's total *active*
    time (anchor-to-anchor duration minus any Mode A stop durations in this
    segment) matches exactly, and stamps each point's timestamp accordingly.
    Each Mode A stop's duration is then added, at its own point, to every
    later timestamp in the segment.

    Args:
        segment_points: Points between start_anchor and end_anchor, inclusive,
            in route order. A Mode A stop in this segment must already
            appear as a duplicated, zero-distance point pair (see
            pacing/stops.py's expand_track_with_stops) — the first of the
            pair becomes its arrival, the second its departure.
        start_anchor: Anchor at the beginning of this segment.
        end_anchor: Anchor at the end of this segment.
        sport: Sport type, used to pick the speed model.
        workout_avg_speed_mps: Average speed over the whole workout (not just
            this segment) — used only to decide between Minetti and Tobler for HIKING.
        segment_mode_a_stops: This segment's Mode A stops, sorted ascending
            by distance_from_start.

    Note:
        Does nothing if the segment has fewer than 2 points, if the chosen
        model yields no speeds, or if either the modeled or anchor-to-anchor
        time is zero/negative — in every such case the segment's points are
        left with whatever timestamp they already had (typically None). See
        combine()'s docstring for when this can happen and why it's safe.

    Raises:
        ValueError: If this segment's Mode A stop durations alone consume
            the entire anchor-to-anchor time budget.
    """
    segment_mode_a_stops = segment_mode_a_stops or []
    if len(segment_points) < 2:
        return

    segment_track = Track(points=segment_points)
    if sport == SportType.RUNNING:
        speeds = calculate_minetti_speeds(segment_track)
    elif sport == SportType.HIKING and workout_avg_speed_mps > HIKING_TOBLER_THRESHOLD_MPS:
        speeds = calculate_minetti_speeds(segment_track)
    else:
        speeds = calculate_tobler_speeds(segment_track)
    if not speeds:
        return

    leg_distances = [
        curr.distance_from_start - prev.distance_from_start
        for prev, curr in zip(segment_points, segment_points[1:])
    ]
    modeled_leg_times = [
        (distance / speed) if speed > 0 else 0.0
        for distance, speed in zip(leg_distances, speeds)
    ]
    modeled_total_time = sum(modeled_leg_times)
    anchor_total_time = (end_anchor.timestamp - start_anchor.timestamp).total_seconds()
    if modeled_total_time <= 0 or anchor_total_time <= 0:
        return

    total_stop_seconds = sum(s.duration.total_seconds() for s in segment_mode_a_stops)
    active_time = anchor_total_time - total_stop_seconds
    if active_time <= 0:
        raise ValueError(
            "Mode A stop duration(s) exceed the segment's anchor-to-anchor time budget."
        )

    scale = active_time / modeled_total_time

    segment_points[0].timestamp = start_anchor.timestamp
    elapsed = 0.0
    extra = 0.0
    stop_index = 0
    for index, modeled_leg_time in enumerate(modeled_leg_times, start=1):
        elapsed += modeled_leg_time * scale
        segment_points[index].timestamp = start_anchor.timestamp + timedelta(seconds=elapsed + extra)

        if (
            stop_index < len(segment_mode_a_stops)
            and leg_distances[index - 1] == 0.0
            and segment_points[index - 1].distance_from_start == segment_mode_a_stops[stop_index].distance_from_start
        ):
            duration_seconds = segment_mode_a_stops[stop_index].duration.total_seconds()
            segment_points[index].timestamp += timedelta(seconds=duration_seconds)
            extra += duration_seconds
            stop_index += 1


def _resolve_anchor_bounds(track_points: list[TrackPoint], anchors: list[Anchor]) -> tuple[list[int], list[int]]:
    """Resolve each anchor to a (lo, hi) point-index range in track_points.

    For an anchor whose distance_from_start is unique among `anchors`, lo and
    hi are computed independently via bisect and are equivalent to the index
    range matching `lo_distance <= p.distance_from_start <= hi_distance` —
    this reproduces combine()'s original distance-value filtering exactly,
    so ordinary (non-stop) anchor arrangements are unaffected.

    For a run of two or more anchors sharing the exact same distance
    (deliberately created for a stop's arrival/departure pair — see
    pacing/stops.py), each anchor in the run is assigned a distinct point
    index in order, provided enough points share that distance to give each
    anchor its own; otherwise every anchor in the run falls back to sharing
    one (lo, hi) range, matching combine()'s pre-existing (harmless,
    unsupported) behavior for a degenerate distance coincidence.
    """
    distances = [p.distance_from_start for p in track_points]
    lo = [0] * len(anchors)
    hi = [0] * len(anchors)

    i = 0
    while i < len(anchors):
        j = i
        while j + 1 < len(anchors) and anchors[j + 1].distance_from_start == anchors[i].distance_from_start:
            j += 1
        run_size = j - i + 1

        run_lo = bisect_left(distances, anchors[i].distance_from_start)
        run_hi = bisect_right(distances, anchors[i].distance_from_start) - 1
        run_len = run_hi - run_lo + 1

        if 1 < run_size <= run_len:
            for offset in range(run_size):
                lo[i + offset] = hi[i + offset] = run_lo + offset
        else:
            for offset in range(run_size):
                lo[i + offset] = run_lo
                hi[i + offset] = run_hi

        i = j + 1

    return lo, hi


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
        mode_a_stops: Mode A stops (distance + duration only, arrival not
            yet known), sorted ascending by distance_from_start.

    Returns:
        The same track, with every point that falls inside a valid segment
        now timestamped.
    Raises:
        IndexError: If anchors is empty.
        ValueError: If a segment's Mode A stop durations alone consume its
            entire anchor-to-anchor time budget.

    Note:
        A segment can end up with fewer than 2 points if two anchors are
        placed closer together than the track's point spacing (e.g. a
        mid-route anchor a few meters from the start). Such segments are
        skipped, and any point caught only in that gap keeps whatever
        timestamp it already had (usually None). This is intentionally left
        as-is rather than special-cased: it's rare in practice (anchors are
        normally far apart relative to GPS point spacing), and a point left
        without a timestamp will cause fit_writer.write_fit() to raise
        rather than silently emit a wrong one.
    """
    workout_total_time = (anchors[-1].timestamp - anchors[0].timestamp).total_seconds()
    workout_avg_speed_mps = track.total_distance / workout_total_time if workout_total_time > 0 else 0.0

    mode_a_stops = mode_a_stops or []
    lo, hi = _resolve_anchor_bounds(track.points, anchors)
    buckets = _bucket_mode_a_stops(anchors, mode_a_stops)
    for i in range(len(anchors) - 1):
        segment_points = track.points[lo[i]: hi[i + 1] + 1]
        _pace_segment(segment_points, anchors[i], anchors[i + 1], sport, workout_avg_speed_mps, buckets[i])

    return track

# todo:
# improve the threshold for hiking speed, and probably come up with some formula that also takes ascent into account
# cap the maximum speed relative to the average speed, so that downhills are not too fast (no way someone was running 3:20 downhill on average 6:10 Z2 run)
