from datetime import datetime

from gpx2fit.core.models import Track, TrackPoint


def _point(elevation: float, distance_from_start: float, timestamp: datetime | None = None) -> TrackPoint:
    return TrackPoint(lat=0.0, lon=0.0, elevation=elevation, distance_from_start=distance_from_start, timestamp=timestamp)


class TestTotalElevationGain:
    def test_empty_track_has_no_gain(self):
        assert Track(points=[]).total_elevation_gain == 0.0

    def test_single_point_has_no_gain(self):
        assert Track(points=[_point(100.0, 0.0)]).total_elevation_gain == 0.0

    def test_flat_track_has_no_gain(self):
        track = Track(points=[_point(100.0, 0.0), _point(100.0, 10.0), _point(100.0, 20.0)])
        assert track.total_elevation_gain == 0.0

    def test_sums_only_positive_deltas(self):
        # +50, -30, +20, -10 -> only the two climbs (50 + 20) should count.
        track = Track(points=[
            _point(100.0, 0.0),
            _point(150.0, 10.0),
            _point(120.0, 20.0),
            _point(140.0, 30.0),
            _point(130.0, 40.0),
        ])
        assert track.total_elevation_gain == 70.0

    def test_pure_descent_has_no_gain(self):
        track = Track(points=[_point(200.0, 0.0), _point(100.0, 10.0), _point(0.0, 20.0)])
        assert track.total_elevation_gain == 0.0


class TestStartTime:
    def test_empty_track_has_no_start_time(self):
        assert Track(points=[]).start_time is None

    def test_first_point_without_timestamp_has_no_start_time(self):
        track = Track(points=[_point(0.0, 0.0, timestamp=None)])
        assert track.start_time is None

    def test_returns_first_point_timestamp(self):
        t = datetime(2024, 1, 1, 8, 0, 0)
        track = Track(points=[_point(0.0, 0.0, timestamp=t), _point(0.0, 10.0, timestamp=None)])
        assert track.start_time == t


class TestTotalDistance:
    def test_empty_track_has_zero_distance(self):
        assert Track(points=[]).total_distance == 0.0

    def test_returns_last_point_distance_from_start(self):
        track = Track(points=[_point(0.0, 0.0), _point(0.0, 5.0), _point(0.0, 12.5)])
        assert track.total_distance == 12.5
