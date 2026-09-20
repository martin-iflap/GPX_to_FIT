import math
from datetime import datetime

from gpx2fit.core.models import Anchor, SportType, Track, TrackPoint
from gpx2fit.core.pacing.curve_selection import (
    DEFAULT_SMOOTHNESS,
    resolve_curve_shape,
    resolve_max_speed_ratio,
    resolve_tobler_weight,
)
from gpx2fit.core.pacing.gradient import CurveShape, calculate_gradient

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


def flat_track(distances: list[float]) -> Track:
    """A perfectly flat track through the given distances_from_start."""
    return Track(points=[point(elevation=0.0, distance_from_start=d) for d in distances])


def rolling_track(distance: float, climb_per_km: float, leg_m: float = 50.0) -> Track:
    """A sawtooth track climbing and descending `climb_per_km` metres of vertical per km.

    Each up-down cycle is 8 legs long, so the climbs and descents are longer
    than GRADIENT_WINDOW_M and survive smoothing as real terrain rather than
    averaging out to flat.
    """
    cycle_legs = 8
    rise_per_leg = climb_per_km * leg_m / 1000.0 * 2  # half the cycle climbs, half descends
    points = []
    elevation = 0.0
    for index in range(int(distance / leg_m) + 1):
        points.append(point(elevation=elevation, distance_from_start=index * leg_m))
        elevation += rise_per_leg if (index % cycle_legs) < cycle_legs // 2 else -rise_per_leg
    return Track(points=points)


def leg_distances(track: Track) -> list[float]:
    """The track's per-leg distances, as combine() derives them."""
    return [b.distance_from_start - a.distance_from_start for a, b in zip(track.points, track.points[1:])]


def resolved_weight(track: Track, active_seconds: float, sport: SportType) -> float:
    """The Minetti/Tobler blend combine() resolves for a stop-free workout over this track."""
    return resolve_tobler_weight(calculate_gradient(track), leg_distances(track), active_seconds, sport)


def resolved_max_speed_ratio(track: Track, sport: SportType, smoothness: int = DEFAULT_SMOOTHNESS) -> float:
    """The speed-swing bound combine() resolves for a workout over this track."""
    return resolve_max_speed_ratio(calculate_gradient(track), leg_distances(track), sport, smoothness=smoothness)


def resolved_curve_shape(track: Track, sport: SportType, smoothness: int = DEFAULT_SMOOTHNESS) -> CurveShape:
    """The curve exponents combine() fits to this workout's speed-swing bound."""
    return resolve_curve_shape(resolved_max_speed_ratio(track, sport, smoothness))


def haversine_m(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    """Reference Haversine formula, computed independently of the implementation under test."""
    r = 6371000.0
    phi1, phi2 = math.radians(lat_a), math.radians(lat_b)
    d_phi = math.radians(lat_b - lat_a)
    d_lambda = math.radians(lon_b - lon_a)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
