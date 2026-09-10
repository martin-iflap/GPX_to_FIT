from datetime import timedelta

import pytest

from gpx2fit.core.models import ModeAStop, SportType, Track
from gpx2fit.core.pacing.combine import HIKING_TOBLER_THRESHOLD_MPS, combine
from tests.conftest import START, anchor, point, timestamp_of


class TestCombineBasicPacing:
    def test_all_points_end_up_timestamped(self):
        track = Track(points=[point(elevation=0.0, distance_from_start=d) for d in (0.0, 100.0, 200.0, 300.0)])
        anchors = [anchor(0.0, START), anchor(300.0, START + timedelta(minutes=1))]

        result = combine(track, anchors, SportType.RUNNING)

        assert all(p.timestamp is not None for p in result.points)

    def test_first_and_last_point_match_anchor_timestamps_exactly(self):
        track = Track(points=[point(elevation=0.0, distance_from_start=d) for d in (0.0, 100.0, 200.0, 300.0)])
        end_time = START + timedelta(minutes=2)
        anchors = [anchor(0.0, START), anchor(300.0, end_time)]

        result = combine(track, anchors, SportType.RUNNING)

        assert result.points[0].timestamp == START
        assert result.points[-1].timestamp == end_time

    def test_timestamps_are_strictly_increasing_on_a_flat_track(self):
        track = Track(points=[point(elevation=0.0, distance_from_start=d) for d in (0.0, 100.0, 200.0, 300.0, 400.0)])
        anchors = [anchor(0.0, START), anchor(400.0, START + timedelta(minutes=3))]

        result = combine(track, anchors, SportType.RUNNING)

        timestamps = [timestamp_of(p) for p in result.points]
        assert timestamps == sorted(timestamps)
        assert len(set(timestamps)) == len(timestamps)

    def test_scaled_segment_time_matches_anchor_duration_exactly_even_with_gradient(self):
        # Uphill then downhill, so per-leg modeled speeds differ, but the
        # total elapsed time must still land exactly on the anchor duration.
        track = Track(points=[
            point(elevation=0.0, distance_from_start=0.0),
            point(elevation=20.0, distance_from_start=100.0),
            point(elevation=40.0, distance_from_start=200.0),
            point(elevation=10.0, distance_from_start=300.0),
        ])
        end_time = START + timedelta(minutes=5)
        anchors = [anchor(0.0, START), anchor(300.0, end_time)]

        result = combine(track, anchors, SportType.RUNNING)

        assert result.points[-1].timestamp == end_time


class TestCombineMultiSegment:
    def test_each_segment_time_matches_its_own_anchor_pair_independently(self):
        track = Track(points=[point(elevation=0.0, distance_from_start=d) for d in (0.0, 100.0, 200.0, 300.0, 400.0)])
        mid_time = START + timedelta(minutes=1)
        end_time = START + timedelta(minutes=10)  # much slower second half
        anchors = [anchor(0.0, START), anchor(200.0, mid_time), anchor(400.0, end_time)]

        result = combine(track, anchors, SportType.RUNNING)

        by_distance = {p.distance_from_start: timestamp_of(p) for p in result.points}
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
            point(elevation=0.0, distance_from_start=0.0),
            point(elevation=30.0, distance_from_start=250.0),
            point(elevation=60.0, distance_from_start=500.0),
            point(elevation=20.0, distance_from_start=750.0),
            point(elevation=0.0, distance_from_start=1000.0),
        ])
        anchors = [anchor(0.0, START), anchor(1000.0, START + duration)]
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


class TestCombineInvalidAnchorMappingGuard:
    # pacing.anchors.build_user_anchors now rejects two anchors resolving to
    # the same distance before combine() ever sees them, and every anchor's
    # distance is guaranteed (by construction) to match an existing track
    # point exactly. So an anchor with no matching point, or more anchors
    # than points sharing a distance, means that invariant was violated
    # somewhere upstream — combine() should fail loudly rather than silently
    # mis-pace or drop points.

    def test_anchor_with_no_matching_track_point_raises(self):
        track = Track(points=[
            point(elevation=0.0, distance_from_start=0.0),
            point(elevation=0.0, distance_from_start=100.0),
            point(elevation=0.0, distance_from_start=200.0),
        ])
        anchors = [
            anchor(0.0, START),
            anchor(110.0, START + timedelta(minutes=1)),
            anchor(200.0, START + timedelta(minutes=2)),
        ]

        with pytest.raises(ValueError):
            combine(track, anchors, SportType.RUNNING)

    def test_two_anchors_sharing_the_only_point_at_a_distance_raises(self):
        track = Track(points=[point(elevation=0.0, distance_from_start=0.0)])
        anchors = [anchor(0.0, START), anchor(0.0, START + timedelta(minutes=1))]

        with pytest.raises(ValueError):
            combine(track, anchors, SportType.RUNNING)


class TestCombineDuplicateDistancePoints:
    def test_arrival_and_departure_anchors_stamp_the_two_duplicate_points_exactly(self):
        # Simulates what pacing.stops.expand_track_with_stops produces: a
        # duplicated point at the stop's distance, paired with an
        # arrival/departure anchor pair at that same distance.
        arrival = START + timedelta(minutes=1)
        departure = arrival + timedelta(minutes=15)
        end_time = departure + timedelta(minutes=1)
        track = Track(points=[
            point(elevation=0.0, distance_from_start=0.0),
            point(elevation=0.0, distance_from_start=100.0),
            point(elevation=0.0, distance_from_start=100.0),
            point(elevation=0.0, distance_from_start=200.0),
        ])
        anchors = [
            anchor(0.0, START),
            anchor(100.0, arrival),
            anchor(100.0, departure),
            anchor(200.0, end_time),
        ]

        result = combine(track, anchors, SportType.RUNNING)

        assert result.points[1].timestamp == arrival
        assert result.points[2].timestamp == departure
        assert result.points[0].timestamp == START
        assert result.points[-1].timestamp == end_time

    def test_three_anchors_sharing_a_distance_with_only_one_matching_point_raises(self):
        # Degenerate, unsupported coincidence (more anchors than duplicate
        # points at that distance) — build_user_anchors now rejects this
        # before combine() sees it; combine() itself must still fail loudly
        # rather than guess which anchor's timestamp the lone point gets.
        track = Track(points=[
            point(elevation=0.0, distance_from_start=0.0),
            point(elevation=0.0, distance_from_start=100.0),
            point(elevation=0.0, distance_from_start=200.0),
        ])
        anchors = [
            anchor(0.0, START),
            anchor(100.0, START + timedelta(minutes=1)),
            anchor(100.0, START + timedelta(minutes=2)),
            anchor(100.0, START + timedelta(minutes=3)),
            anchor(200.0, START + timedelta(minutes=4)),
        ]

        with pytest.raises(ValueError):
            combine(track, anchors, SportType.RUNNING)


class TestCombineModeAStops:
    def test_single_mode_a_stop_gets_natural_arrival_and_duration_based_departure_and_exact_end_time(self):
        # Flat, evenly-spaced track: 0 -> 100 (arrival) -> 100 (departure dup) -> 200.
        duration = timedelta(minutes=6)
        end_time = START + timedelta(minutes=20)
        track = Track(points=[
            point(elevation=0.0, distance_from_start=0.0),
            point(elevation=0.0, distance_from_start=100.0),
            point(elevation=0.0, distance_from_start=100.0),
            point(elevation=0.0, distance_from_start=200.0),
        ])
        anchors = [anchor(0.0, START), anchor(200.0, end_time)]

        result = combine(track, anchors, SportType.RUNNING, mode_a_stops=[ModeAStop(100.0, duration)])

        active_time = timedelta(minutes=14)  # 20 min anchor time - 6 min stop
        arrival = START + active_time / 2
        assert result.points[1].timestamp == arrival
        assert result.points[2].timestamp == arrival + duration
        assert result.points[-1].timestamp == end_time

    def test_two_mode_a_stops_in_the_same_segment_cascade_shift_correctly(self):
        # Flat track: 0 -> 300 (arrival1) -> 300 (departure1 dup) -> 600 (arrival2) -> 600 (departure2 dup) -> 1000.
        first_duration = timedelta(minutes=10)
        second_duration = timedelta(minutes=5)
        end_time = START + timedelta(minutes=40)
        track = Track(points=[
            point(elevation=0.0, distance_from_start=0.0),
            point(elevation=0.0, distance_from_start=300.0),
            point(elevation=0.0, distance_from_start=300.0),
            point(elevation=0.0, distance_from_start=600.0),
            point(elevation=0.0, distance_from_start=600.0),
            point(elevation=0.0, distance_from_start=1000.0),
        ])
        anchors = [anchor(0.0, START), anchor(1000.0, end_time)]
        mode_a_stops = [ModeAStop(300.0, first_duration), ModeAStop(600.0, second_duration)]

        result = combine(track, anchors, SportType.RUNNING, mode_a_stops=mode_a_stops)

        active_time = timedelta(minutes=25)  # 40 min anchor time - 15 min of stops
        arrival1 = START + active_time * 0.3  # 300/1000 of the active time
        departure1 = arrival1 + first_duration
        arrival2 = START + active_time * 0.6 + first_duration  # cascades off stop 1's dwell
        departure2 = arrival2 + second_duration

        assert result.points[1].timestamp == arrival1
        assert result.points[2].timestamp == departure1
        assert result.points[3].timestamp == arrival2
        assert result.points[4].timestamp == departure2
        assert result.points[-1].timestamp == end_time

    def test_mode_a_stop_confined_to_one_segment_does_not_affect_another_segment(self):
        # Segment 1 (0 -> 200) contains a stop; segment 2 (200 -> 400) doesn't.
        mid_time = START + timedelta(minutes=20)
        end_time = mid_time + timedelta(minutes=10)
        track = Track(points=[
            point(elevation=0.0, distance_from_start=0.0),
            point(elevation=0.0, distance_from_start=100.0),
            point(elevation=0.0, distance_from_start=100.0),
            point(elevation=0.0, distance_from_start=200.0),
            point(elevation=0.0, distance_from_start=300.0),
            point(elevation=0.0, distance_from_start=400.0),
        ])
        anchors = [anchor(0.0, START), anchor(200.0, mid_time), anchor(400.0, end_time)]
        mode_a_stops = [ModeAStop(100.0, timedelta(minutes=5))]

        result = combine(track, anchors, SportType.RUNNING, mode_a_stops=mode_a_stops)

        # Second segment is flat and evenly spaced, so its midpoint lands
        # exactly halfway through its own anchor-to-anchor duration,
        # completely unaffected by the stop in the first segment.
        assert result.points[4].timestamp == mid_time + (end_time - mid_time) / 2
        assert result.points[-1].timestamp == end_time

    def test_mode_b_and_mode_a_stops_together_do_not_interfere(self):
        arrival = START + timedelta(minutes=3)
        departure = arrival + timedelta(minutes=7)
        end_time = departure + timedelta(minutes=30)
        mode_a_duration = timedelta(minutes=4)
        track = Track(points=[
            point(elevation=0.0, distance_from_start=0.0),
            point(elevation=0.0, distance_from_start=100.0),
            point(elevation=0.0, distance_from_start=100.0),
            point(elevation=0.0, distance_from_start=250.0),
            point(elevation=0.0, distance_from_start=250.0),
            point(elevation=0.0, distance_from_start=400.0),
        ])
        anchors = [
            anchor(0.0, START),
            anchor(100.0, arrival),
            anchor(100.0, departure),
            anchor(400.0, end_time),
        ]

        result = combine(track, anchors, SportType.RUNNING, mode_a_stops=[ModeAStop(250.0, mode_a_duration)])

        active_time = timedelta(minutes=26)  # 30 min anchor time - 4 min stop
        natural_arrival_250 = departure + active_time / 2  # two equal 150m legs
        assert result.points[1].timestamp == arrival
        assert result.points[2].timestamp == departure
        assert result.points[3].timestamp == natural_arrival_250
        assert result.points[4].timestamp == natural_arrival_250 + mode_a_duration
        assert result.points[-1].timestamp == end_time

    def test_stop_duration_exceeding_segment_time_budget_raises(self):
        track = Track(points=[
            point(elevation=0.0, distance_from_start=0.0),
            point(elevation=0.0, distance_from_start=100.0),
            point(elevation=0.0, distance_from_start=100.0),
            point(elevation=0.0, distance_from_start=200.0),
        ])
        anchors = [anchor(0.0, START), anchor(200.0, START + timedelta(minutes=5))]

        with pytest.raises(ValueError):
            combine(track, anchors, SportType.RUNNING, mode_a_stops=[ModeAStop(100.0, timedelta(minutes=10))])

    def test_mode_a_stops_omitted_or_none_behaves_like_today(self):
        def _track() -> Track:
            return Track(points=[point(elevation=0.0, distance_from_start=d) for d in (0.0, 100.0, 200.0, 300.0)])

        anchors = [anchor(0.0, START), anchor(300.0, START + timedelta(minutes=2))]

        omitted = combine(_track(), anchors, SportType.RUNNING)
        explicit_none = combine(_track(), anchors, SportType.RUNNING, mode_a_stops=None)

        omitted_times = [p.timestamp for p in omitted.points]
        explicit_none_times = [p.timestamp for p in explicit_none.points]
        assert omitted_times == explicit_none_times


class TestCombineReturnsSameTrack:
    def test_returns_the_same_track_object(self):
        track = Track(points=[point(elevation=0.0, distance_from_start=0.0), point(elevation=0.0, distance_from_start=100.0)])
        anchors = [anchor(0.0, START), anchor(100.0, START + timedelta(minutes=1))]

        result = combine(track, anchors, SportType.RUNNING)

        assert result is track
