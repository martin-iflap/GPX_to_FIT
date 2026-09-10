import math
from datetime import datetime

from gpx2fit.core.models import Anchor, TrackPoint

START = datetime(2024, 1, 1, 8, 0, 0)


def point(
    *,
    lat: float = 0.0,
    lon: float = 0.0,
    elevation: float = 0.0,
    distance_from_start: float = 0.0,
    timestamp: datetime | None = None,
) -> TrackPoint:
    return TrackPoint(lat=lat, lon=lon, elevation=elevation, distance_from_start=distance_from_start, timestamp=timestamp)


def anchor(distance_from_start: float, timestamp: datetime, source: str = "user") -> Anchor:
    return Anchor(distance_from_start=distance_from_start, timestamp=timestamp, source=source)


def timestamp_of(p: TrackPoint) -> datetime:
    """Return a point's timestamp, asserting it has already been stamped (as combine() guarantees)."""
    assert p.timestamp is not None
    return p.timestamp


def haversine_m(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    """Reference Haversine formula, computed independently of the implementation under test."""
    r = 6371000.0
    phi1, phi2 = math.radians(lat_a), math.radians(lat_b)
    d_phi = math.radians(lat_b - lat_a)
    d_lambda = math.radians(lon_b - lon_a)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
