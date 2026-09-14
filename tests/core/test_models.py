from datetime import datetime

from gpx2fit.core.models import Track
from tests.core.conftest import point


class TestTotalElevationGain:
    def test_empty_track_has_no_gain(self):
        assert Track(points=[]).total_elevation_gain == 0.0

    def test_single_point_has_no_gain(self):
        assert Track(points=[point(elevation=100.0, distance_from_start=0.0)]).total_elevation_gain == 0.0

    def test_flat_track_has_no_gain(self):
        track = Track(points=[
            point(elevation=100.0, distance_from_start=0.0),
            point(elevation=100.0, distance_from_start=10.0),
            point(elevation=100.0, distance_from_start=20.0),
        ])
        assert track.total_elevation_gain == 0.0

    def test_sums_only_positive_deltas(self):
        # +50, -30, +20, -10 -> only the two climbs (50 + 20) should count.
        track = Track(points=[
            point(elevation=100.0, distance_from_start=0.0),
            point(elevation=150.0, distance_from_start=10.0),
            point(elevation=120.0, distance_from_start=20.0),
            point(elevation=140.0, distance_from_start=30.0),
            point(elevation=130.0, distance_from_start=40.0),
        ])
        assert track.total_elevation_gain == 70.0

    def test_pure_descent_has_no_gain(self):
        track = Track(points=[
            point(elevation=200.0, distance_from_start=0.0),
            point(elevation=100.0, distance_from_start=10.0),
            point(elevation=0.0, distance_from_start=20.0),
        ])
        assert track.total_elevation_gain == 0.0


class TestStartTime:
    def test_empty_track_has_no_start_time(self):
        assert Track(points=[]).start_time is None

    def test_first_point_without_timestamp_has_no_start_time(self):
        track = Track(points=[point(distance_from_start=0.0, timestamp=None)])
        assert track.start_time is None

    def test_returns_first_point_timestamp(self):
        t = datetime(2024, 1, 1, 8, 0, 0)
        track = Track(points=[point(distance_from_start=0.0, timestamp=t), point(distance_from_start=10.0, timestamp=None)])
        assert track.start_time == t


class TestTotalDistance:
    def test_empty_track_has_zero_distance(self):
        assert Track(points=[]).total_distance == 0.0

    def test_returns_last_point_distance_from_start(self):
        track = Track(points=[
            point(distance_from_start=0.0), point(distance_from_start=5.0), point(distance_from_start=12.5),
        ])
        assert track.total_distance == 12.5
