from datetime import timedelta
from gpx2fit.core.models import Track


def simple_uniform_speed(track: Track) -> Track:
    """Assign timestamps so the route is traveled at a constant speed."""
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
