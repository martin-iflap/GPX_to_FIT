"""Synthetic recorded activities, for testing the harness without a real corpus.

The real corpus is gitignored (it carries the athlete's home GPS), so every
test here builds its own activity and writes it through the project's own
`write_fit`. That also means the reader is always tested against bytes the
project itself produced, which is the one FIT dialect it definitely has to
handle.

Following tests/core/conftest.py, these are plain functions rather than pytest
fixtures, so they can be imported and called directly.
"""

import math
from datetime import datetime, timedelta, timezone

from gpx2fit.core.fit_writer import write_fit
from gpx2fit.core.models import SportType, Track, TrackPoint

START = datetime(2024, 6, 1, 7, 0, 0, tzinfo=timezone.utc)

# Roughly one degree of latitude in metres, good enough to lay out a synthetic
# north-south course; the harness recomputes real distances with haversine.
_METERS_PER_DEGREE_LAT = 111320.0


def rolling_elevation(index: int, base: float = 300.0, amplitude: float = 120.0, period: float = 45.0) -> float:
    """A smooth hill profile with a shorter ripple on top, rounded to whole metres."""
    return float(round(base + amplitude * math.sin(index / period) + 0.3 * amplitude * math.sin(index / 11.0)))


def synthetic_activity(
    *,
    sport: SportType = SportType.HIKING,
    point_count: int = 700,
    leg_m: float = 16.0,
    flat_speed_mps: float = 1.6,
    uphill_factor: float = 3.2,
    downhill_factor: float = 1.0,
    amplitude: float = 120.0,
    start: datetime = START,
) -> Track:
    """A recorded activity whose pace responds to gradient in a known, controllable way.

    Speed on each leg is `flat_speed_mps * exp(-uphill_factor * g)` climbing and
    `* exp(downhill_factor * |g|)` descending, so a test can plant a specific
    gradient response and check the harness recovers it. Nothing here is the
    model's own curve — the point is to be an independent reference.

    Args:
        sport: Which sport the FIT declares.
        point_count: How many points to lay out.
        leg_m: Horizontal spacing between points.
        flat_speed_mps: Speed on flat ground.
        uphill_factor: How hard climbs slow this athlete. 0 means gradient has
            no effect uphill.
        downhill_factor: How many descents speed them up.
        amplitude: Hill size in metres; 0 gives a flat course.
        start: The activity's start time.

    Returns:
        A fully-timestamped Track, ready for `write_fit`.
    """
    points: list[TrackPoint] = []
    latitude = 45.0
    distance = 0.0
    for index in range(point_count):
        points.append(TrackPoint(
            lat=latitude,
            lon=7.0,
            elevation=rolling_elevation(index, amplitude=amplitude),
            distance_from_start=distance,
            timestamp=start,
        ))
        latitude += leg_m / _METERS_PER_DEGREE_LAT
        distance += leg_m

    moment = start
    for index, point in enumerate(points):
        point.timestamp = moment
        if index + 1 < len(points):
            gradient = (points[index + 1].elevation - point.elevation) / leg_m
            if gradient >= 0:
                speed = flat_speed_mps * math.exp(-uphill_factor * gradient)
            else:
                speed = flat_speed_mps * math.exp(downhill_factor * gradient * -1.0)
            moment += timedelta(seconds=leg_m / max(speed, 0.3))

    return Track(points=points, sport=sport, device="synthetic")


def synthetic_fit_bytes(**kwargs) -> bytes:
    """`synthetic_activity` written out as a FIT file."""
    return write_fit(synthetic_activity(**kwargs))


def with_standing_pause(track: Track, at_index: int, seconds: float, record_every: float = 2.0) -> Track:
    """Insert a stretch of standing still, recorded but never auto-paused.

    Mutates and returns `track`. The inserted points repeat the position at
    `at_index`, so they carry time but no distance — which is how a watch
    records someone stopping without triggering auto-pause.
    """
    anchor = track.points[at_index]
    assert anchor.timestamp is not None
    inserted = [
        TrackPoint(
            lat=anchor.lat,
            lon=anchor.lon,
            elevation=anchor.elevation,
            # Nudged forward so write_fit doesn't read this as a zero-distance
            # leg and turn it into a timer pause, which is a different case.
            distance_from_start=anchor.distance_from_start + 0.01 * step,
            timestamp=anchor.timestamp + timedelta(seconds=record_every * step),
        )
        for step in range(1, int(seconds / record_every) + 1)
    ]
    for point in track.points[at_index + 1:]:
        assert point.timestamp is not None
        point.timestamp += timedelta(seconds=seconds)
    track.points[at_index + 1:at_index + 1] = inserted
    return track
