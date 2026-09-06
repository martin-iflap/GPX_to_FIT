from datetime import datetime, timedelta

from gpx2fit.core.models import Anchor, SportType, Track, TrackPoint
from gpx2fit.core.pacing.combine import HIKING_TOBLER_THRESHOLD_MPS, combine


def _point(elevation: float, distance_from_start: float) -> TrackPoint:
    return TrackPoint(lat=0.0, lon=0.0, elevation=elevation, distance_from_start=distance_from_start)


def _anchor(distance_from_start: float, timestamp: datetime) -> Anchor:
    return Anchor(distance_from_start=distance_from_start, timestamp=timestamp, source="user")


START = datetime(2024, 1, 1, 8, 0, 0)


class TestCombineBasicPacing:
    def test_all_points_end_up_timestamped(self):
        track = Track(points=[_point(0.0, d) for d in (0.0, 100.0, 200.0, 300.0)])
        anchors = [_anchor(0.0, START), _anchor(300.0, START + timedelta(minutes=1))]

        result = combine(track, anchors, SportType.RUNNING)

        assert all(p.timestamp is not None for p in result.points)

    def test_first_and_last_point_match_anchor_timestamps_exactly(self):
        track = Track(points=[_point(0.0, d) for d in (0.0, 100.0, 200.0, 300.0)])
        end_time = START + timedelta(minutes=2)
        anchors = [_anchor(0.0, START), _anchor(300.0, end_time)]

        result = combine(track, anchors, SportType.RUNNING)

        assert result.points[0].timestamp == START
        assert result.points[-1].timestamp == end_time

    def test_timestamps_are_strictly_increasing_on_a_flat_track(self):
        track = Track(points=[_point(0.0, d) for d in (0.0, 100.0, 200.0, 300.0, 400.0)])
        anchors = [_anchor(0.0, START), _anchor(400.0, START + timedelta(minutes=3))]

        result = combine(track, anchors, SportType.RUNNING)

        timestamps = [p.timestamp for p in result.points]
        assert timestamps == sorted(timestamps)
        assert len(set(timestamps)) == len(timestamps)

    def test_scaled_segment_time_matches_anchor_duration_exactly_even_with_gradient(self):
        # Uphill then downhill, so per-leg modeled speeds differ, but the
        # total elapsed time must still land exactly on the anchor duration.
        track = Track(points=[
            _point(0.0, 0.0), _point(20.0, 100.0), _point(40.0, 200.0), _point(10.0, 300.0),
        ])
        end_time = START + timedelta(minutes=5)
        anchors = [_anchor(0.0, START), _anchor(300.0, end_time)]

        result = combine(track, anchors, SportType.RUNNING)

        assert result.points[-1].timestamp == end_time


class TestCombineMultiSegment:
    def test_each_segment_time_matches_its_own_anchor_pair_independently(self):
        track = Track(points=[_point(0.0, d) for d in (0.0, 100.0, 200.0, 300.0, 400.0)])
        mid_time = START + timedelta(minutes=1)
        end_time = START + timedelta(minutes=10)  # much slower second half
        anchors = [_anchor(0.0, START), _anchor(200.0, mid_time), _anchor(400.0, end_time)]

        result = combine(track, anchors, SportType.RUNNING)

        by_distance = {p.distance_from_start: p.timestamp for p in result.points}
        assert by_distance[0.0] == START
        assert by_distance[200.0] == mid_time
        assert by_distance[400.0] == end_time
        # First half (fast) should take far less time than the second half (slow).
        first_half_seconds = (by_distance[200.0] - by_distance[0.0]).total_seconds()
        second_half_seconds = (by_distance[400.0] - by_distance[200.0]).total_seconds()
        assert first_half_seconds < second_half_seconds


class TestCombineModelSelection:
    def _run_with_avg_speed(self, sport: SportType, avg_speed_mps: float) -> Track:
        distance = 1000.0
        duration = timedelta(seconds=distance / avg_speed_mps)
        track = Track(points=[
            _point(0.0, 0.0), _point(30.0, 250.0), _point(60.0, 500.0), _point(20.0, 750.0), _point(0.0, 1000.0),
        ])
        anchors = [_anchor(0.0, START), _anchor(1000.0, START + duration)]
        return combine(track, anchors, sport)

    def test_hiking_below_threshold_still_produces_valid_pacing(self):
        # Slow hiking pace (well under the Tobler/Minetti switch threshold).
        result = self._run_with_avg_speed(SportType.HIKING, avg_speed_mps=1.0)
        assert result.points[0].timestamp is not None
        assert result.points[-1].timestamp is not None
        assert result.points[0].timestamp < result.points[-1].timestamp

    def test_hiking_above_threshold_still_produces_valid_pacing(self):
        # Brisk hiking pace, above HIKING_TOBLER_THRESHOLD_MPS -> uses Minetti.
        assert HIKING_TOBLER_THRESHOLD_MPS < 3.0
        result = self._run_with_avg_speed(SportType.HIKING, avg_speed_mps=3.0)
        assert result.points[0].timestamp is not None
        assert result.points[-1].timestamp is not None
        assert result.points[0].timestamp < result.points[-1].timestamp

    def test_running_and_brisk_hiking_produce_the_same_relative_pacing_shape(self):
        # Above the threshold, HIKING is defined to use the same model as RUNNING.
        running = self._run_with_avg_speed(SportType.RUNNING, avg_speed_mps=3.0)
        hiking = self._run_with_avg_speed(SportType.HIKING, avg_speed_mps=3.0)

        running_times = [p.timestamp for p in running.points]
        hiking_times = [p.timestamp for p in hiking.points]
        assert running_times == hiking_times


class TestCombineSparseSegmentGuard:
    def test_segment_with_fewer_than_two_points_leaves_its_lone_point_unstamped(self):
        # Two anchors (100 and 110) placed closer together than the point
        # spacing: the segments on either side of 110 each contain only one
        # point, so both are skipped per combine()'s documented behavior —
        # the point at distance 200 is never the endpoint of a processed
        # segment, so it's left stranded with no timestamp.
        track = Track(points=[_point(0.0, 0.0), _point(0.0, 100.0), _point(0.0, 200.0)])
        anchors = [
            _anchor(0.0, START),
            _anchor(100.0, START + timedelta(minutes=1)),
            _anchor(110.0, START + timedelta(minutes=1, seconds=5)),
            _anchor(200.0, START + timedelta(minutes=2)),
        ]

        result = combine(track, anchors, SportType.RUNNING)

        assert result.points[0].timestamp == START
        assert result.points[1].timestamp == START + timedelta(minutes=1)
        assert result.points[-1].timestamp is None

    def test_single_point_track_is_a_no_op(self):
        track = Track(points=[_point(0.0, 0.0)])
        anchors = [_anchor(0.0, START), _anchor(0.0, START + timedelta(minutes=1))]

        result = combine(track, anchors, SportType.RUNNING)

        assert result.points[0].timestamp is None


class TestCombineReturnsSameTrack:
    def test_returns_the_same_track_object(self):
        track = Track(points=[_point(0.0, 0.0), _point(0.0, 100.0)])
        anchors = [_anchor(0.0, START), _anchor(100.0, START + timedelta(minutes=1))]

        result = combine(track, anchors, SportType.RUNNING)

        assert result is track
