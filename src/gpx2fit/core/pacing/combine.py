from datetime import timedelta
from gpx2fit.core.models import Track, Anchor, SportType
from gpx2fit.core.pacing.gradient import calculate_tobler_speeds, calculate_minetti_speeds

HIKING_TOBLER_THRESHOLD_MPS = 1.8


def simple_uniform_speed(track: Track) -> Track: # probably remove this function later once useless
    """Assign timestamps so the route is traveled at a constant speed.
     - Good for testing or some random stuff.
    """
    if not track.points:
        return track
    if track.points[0].timestamp is None or track.points[-1].timestamp is None:
        raise ValueError("Both the first and last point need timestamps before pacing.")

    start_time = track.points[0].timestamp
    total_time_seconds = (track.points[-1].timestamp - start_time).total_seconds()
    if total_time_seconds <= 0:
        raise ValueError("The final timestamp must be later than the start timestamp.")

    uniform_speed = track.total_distance / total_time_seconds
    for index, point in enumerate(track.points):
        if index == 0:
            point.timestamp = start_time
            continue
        time_offset = point.distance_from_start / uniform_speed if uniform_speed > 0 else 0.0
        point.timestamp = start_time + timedelta(seconds=time_offset)

    return track


def combine(track: Track, anchors: list[Anchor], sport: SportType) -> Track:
    """"""
    workout_total_time = (anchors[-1].timestamp - anchors[0].timestamp).total_seconds()
    workout_avg_speed_mps = (
        track.total_distance / workout_total_time if workout_total_time > 0 else 0.0
    )

    for start_anchor, end_anchor in zip(anchors, anchors[1:]):
        segment_points = [
            p for p in track.points
            if start_anchor.distance_from_start <= p.distance_from_start <= end_anchor.distance_from_start
        ]
        if len(segment_points) < 2: # check this logic
            continue

        segment_track = Track(points=segment_points)
        if sport == SportType.RUNNING:
            speeds = calculate_minetti_speeds(segment_track)
        elif sport == SportType.HIKING and workout_avg_speed_mps > HIKING_TOBLER_THRESHOLD_MPS:
            speeds = calculate_minetti_speeds(segment_track)
        else:
            speeds = calculate_tobler_speeds(segment_track)
        if not speeds:
            continue

        leg_distances: list[float] = []
        for prev, curr in zip(segment_points, segment_points[1:]):
            leg_distances.append(curr.distance_from_start - prev.distance_from_start)

        modeled_leg_times = [
            (distance / speed) if speed > 0 else 0.0
            for distance, speed in zip(leg_distances, speeds)
        ]
        modeled_total_time = sum(modeled_leg_times)
        anchor_total_time = (end_anchor.timestamp - start_anchor.timestamp).total_seconds()
        if modeled_total_time <= 0 or anchor_total_time <= 0:
            continue

        scale = anchor_total_time / modeled_total_time

        segment_points[0].timestamp = start_anchor.timestamp
        elapsed = 0.0
        for index, modeled_leg_time in enumerate(modeled_leg_times, start=1):
            elapsed += modeled_leg_time * scale
            segment_points[index].timestamp = start_anchor.timestamp + timedelta(seconds=elapsed)

    return track


# later perhaps extract some of the logic from combine() into a separate function
# improve the threshold for running speed, and probably come up with some formula that also takes ascent into account
