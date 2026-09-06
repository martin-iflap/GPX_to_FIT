"""Fit per-leg pacing speeds to known anchor timestamps and stamp them onto a track."""

from datetime import timedelta

from gpx2fit.core.models import Anchor, SportType, Track, TrackPoint
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
) -> None:
    """Assign timestamps to one anchor-to-anchor segment's points, in place.

    Picks a per-leg relative speed model (Minetti or Tobler),
    then scales the modeled per-leg times so the segment's total time
    matches the anchor-to-anchor duration exactly, and stamps
    each point's timestamp accordingly.

    Args:
        segment_points: Points between start_anchor and end_anchor, inclusive,
            in route order.
        start_anchor: Anchor at the beginning of this segment.
        end_anchor: Anchor at the end of this segment.
        sport: Sport type, used to pick the speed model.
        workout_avg_speed_mps: Average speed over the whole workout (not just
            this segment) — used only to decide between Minetti and Tobler for HIKING.

    Note:
        Does nothing if the segment has fewer than 2 points, if the chosen
        model yields no speeds, or if either the modeled or anchor-to-anchor
        time is zero/negative — in every such case the segment's points are
        left with whatever timestamp they already had (typically None). See
        combine()'s docstring for when this can happen and why it's safe.
    """
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

    scale = anchor_total_time / modeled_total_time

    segment_points[0].timestamp = start_anchor.timestamp
    elapsed = 0.0
    for index, modeled_leg_time in enumerate(modeled_leg_times, start=1):
        elapsed += modeled_leg_time * scale
        segment_points[index].timestamp = start_anchor.timestamp + timedelta(seconds=elapsed)


def combine(track: Track, anchors: list[Anchor], sport: SportType) -> Track:
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
    so their sum matches that segment's anchor-to-anchor duration exactly,
    and each point's timestamp is stamped in place (track.points is mutated;
    the return value is the same object, for convenience chaining).

    Args:
        track: Track whose points already have distance_from_start (and
            ideally elevation) set. Mutated in place.
        anchors: Two or more Anchors, sorted by ascending distance_from_start,
            spanning the track from its first point to its last.
        sport: Sport type used to select the speed model.

    Returns:
        The same track, with every point that falls inside a valid segment
        now timestamped.
    Raises:
        IndexError: If anchors is empty.

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

    for start_anchor, end_anchor in zip(anchors, anchors[1:]):
        segment_points = [
            p for p in track.points
            if start_anchor.distance_from_start <= p.distance_from_start <= end_anchor.distance_from_start
        ]
        _pace_segment(segment_points, start_anchor, end_anchor, sport, workout_avg_speed_mps)

    return track

# todo:
# improve the threshold for hiking speed, and probably come up with some formula that also takes ascent into account
# cap the maximum speed relative to the average speed, so that downhills are not too fast (no way someone was running 3:20 downhill on average 6:10 Z2 run)
