from datetime import datetime, timedelta
from gpx2fit.core.models import Anchor, Track, RawAnchor



def _squared_distance(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    """Compute the squared Euclidean distance between two geographic points (lat/lon)."""
    d_lat = lat_a - lat_b
    d_lon = lon_a - lon_b
    return d_lat * d_lat + d_lon * d_lon


def nearest_point_distance_from_start(track: Track, lat: float, lon: float) -> float:
    """Find the nearest point in the track to the given lat/lon and return its distance_from_start."""
    if not track.points:
        raise ValueError("Track must contain points to resolve anchor position.")
    nearest_point = min(track.points, key=lambda p: _squared_distance(p.lat, p.lon, lat, lon))
    return nearest_point.distance_from_start


def add_start_end_anchors(track: Track, start_time: datetime,
                          end_time: datetime|None = None,
                          duration: timedelta|None = None) -> list[Anchor]:
    """Add anchors at the start and end of the track.
     - If end_time is provided, it will be used as the timestamp for the last point.
     - If duration is provided, it will be added to the start_time to calculate the timestamp for the last point.
     - If neither end_time nor duration is provided, a ValueError will be raised.
    """
    if not track.points:
        raise ValueError("Track must contain points to build start/end anchors.")

    if end_time is not None:
        resolved_end_time = end_time
    elif duration is not None:
        resolved_end_time = start_time + duration
    else:
        raise ValueError("Either end_time or duration must be provided.")

    return [
        Anchor(distance_from_start=0.0, timestamp=start_time, source="user"),
        Anchor(distance_from_start=track.total_distance, timestamp=resolved_end_time, source="user"),
    ]


def build_user_anchors(track: Track, raw_anchors: list[RawAnchor]) -> list[Anchor]:
    """Resolve frontend-provided anchor candidates to concrete Anchor objects.
     - If distance_from_start is provided for an anchor, it will be used directly.
     - If lat/lon is provided, the nearest point in the track will be found and its distance_from_start will be used.
     - If neither is provided, a ValueError will be raised.
     - Anchors will be sorted by distance_from_start and timestamp.

    Returns: A list of Anchor objects sorted by distance_from_start and timestamp.
    """
    anchors: list[Anchor] = []
    for raw in raw_anchors:
        if raw.distance_from_start is not None:
            distance = raw.distance_from_start
        elif raw.lat is not None and raw.lon is not None:
            distance = nearest_point_distance_from_start(track, raw.lat, raw.lon)
        else:
            raise ValueError("Each raw anchor must provide distance_from_start or lat/lon.")

        if distance < 0 or distance > track.total_distance:
            raise ValueError("Anchor distance_from_start is outside track bounds.")

        anchors.append(Anchor(distance_from_start=distance, timestamp=raw.timestamp, source=raw.source))

    anchors.sort(key=lambda anchor: (anchor.distance_from_start, anchor.timestamp))
    return anchors


# check what is the best way to find the closest track point
