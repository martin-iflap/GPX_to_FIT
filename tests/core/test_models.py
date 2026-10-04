from datetime import datetime

from gpx2fit.core.models import Track
from tests.core.conftest import point


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
