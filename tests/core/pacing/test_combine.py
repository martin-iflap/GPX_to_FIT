from datetime import timedelta

import pytest

from gpx2fit.core.models import InputError, ModeAStop, SportType, Track
from gpx2fit.core.pacing.combine import HIKING_TOBLER_THRESHOLD_MPS, combine
from tests.core.conftest import START, anchor, point, timestamp_of


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


class TestCombineDegenerateSegment:
    def test_extreme_gradient_that_zeroes_out_modeled_speed_still_stamps_segment_boundaries(self):
        # A -90% grade over a short leg is outside Minetti's calibrated
        # domain and yields a modeled speed of exactly 0.0 (see
        # gradient.py), so the segment's total modeled time is 0 even
        # though its anchor-to-anchor duration is real. This is exactly the
        # kind of bad elevation reading real GPX files can contain (e.g. a
        # GPS/barometer glitch on the very first recorded point). Before the
        # fix, combine() bailed out of this segment without ever stamping
        # its boundary points, leaving the whole track without a start
        # (or, symmetrically, end) timestamp for fit_writer to find.
        track = Track(points=[
            point(elevation=0.0, distance_from_start=0.0),
            point(elevation=-9.0, distance_from_start=10.0),
        ])
        end_time = START + timedelta(minutes=1)
        anchors = [anchor(0.0, START), anchor(10.0, end_time)]

        result = combine(track, anchors, SportType.RUNNING)

        assert result.points[0].timestamp == START
        assert result.points[-1].timestamp == end_time

    def test_anchor_not_later_than_previous_anchor_raises(self):
        # A later-distance anchor whose timestamp isn't actually later is a
        # contradiction, not something to silently paper over — this is
        # exactly what a wrongly-dated photo anchor produces (its capture
        # time landing before the route's own start time).
        track = Track(points=[
            point(elevation=0.0, distance_from_start=0.0),
            point(elevation=0.0, distance_from_start=100.0),
        ])
        anchors = [anchor(0.0, START), anchor(100.0, START - timedelta(minutes=1))]

        # InputError, specifically: this is a user-fixable data problem (bad
        # anchor/stop times), not a bug
        with pytest.raises(InputError):
            combine(track, anchors, SportType.RUNNING)

    def test_anchor_with_identical_timestamp_to_previous_anchor_raises(self):
        track = Track(points=[
            point(elevation=0.0, distance_from_start=0.0),
            point(elevation=0.0, distance_from_start=100.0),
        ])
        anchors = [anchor(0.0, START), anchor(100.0, START)]

        with pytest.raises(InputError):
            combine(track, anchors, SportType.RUNNING)

    def test_degenerate_first_segment_does_not_break_a_later_well_behaved_segment(self):
        # The degenerate segment (0 -> 10) is followed by a normal, flat
        # one (10 -> 210); the later segment's own pacing must still work
        # even though the first segment never modeled any interior points.
        mid_time = START + timedelta(minutes=1)
        end_time = mid_time + timedelta(minutes=2)
        track = Track(points=[
            point(elevation=0.0, distance_from_start=0.0),
            point(elevation=-9.0, distance_from_start=10.0),
            point(elevation=-9.0, distance_from_start=110.0),
            point(elevation=-9.0, distance_from_start=210.0),
        ])
        anchors = [anchor(0.0, START), anchor(10.0, mid_time), anchor(210.0, end_time)]

        result = combine(track, anchors, SportType.RUNNING)

        assert result.points[0].timestamp == START
        assert result.points[1].timestamp == mid_time
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

        with pytest.raises(InputError):
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


class TestCombineSurfaceMultipliers:
    def test_leg_times_scale_inversely_with_their_multiplier(self):
        # Flat track (gradient 0 on every leg) so every leg's modeled speed
        # is identical before multipliers are applied — any difference in
        # the resulting leg durations can only come from the multipliers.
        track = Track(points=[point(elevation=0.0, distance_from_start=d) for d in (0.0, 100.0, 200.0, 300.0)])
        end_time = START + timedelta(seconds=700)
        anchors = [anchor(0.0, START), anchor(300.0, end_time)]

        result = combine(track, anchors, SportType.RUNNING, multipliers=[1.0, 2.0, 4.0])

        times = [timestamp_of(p) for p in result.points]
        # Equal-distance legs with multipliers 1/2/4 model to raw times in
        # ratio 4:2:1 (inversely proportional to their multiplier), which
        # scale-to-total-duration to exactly 400s/200s/100s of 700s.
        assert (times[1] - times[0]).total_seconds() == pytest.approx(400.0)
        assert (times[2] - times[1]).total_seconds() == pytest.approx(200.0)
        assert (times[3] - times[2]).total_seconds() == pytest.approx(100.0)

    def test_all_ones_multipliers_match_no_multipliers_at_all(self):
        def _track() -> Track:
            return Track(points=[
                point(elevation=0.0, distance_from_start=0.0),
                point(elevation=20.0, distance_from_start=100.0),
                point(elevation=40.0, distance_from_start=200.0),
                point(elevation=10.0, distance_from_start=300.0),
            ])

        anchors = [anchor(0.0, START), anchor(300.0, START + timedelta(minutes=5))]

        without = combine(_track(), anchors, SportType.RUNNING)
        with_ones = combine(_track(), anchors, SportType.RUNNING, multipliers=[1.0, 1.0, 1.0])

        without_times = [p.timestamp for p in without.points]
        with_ones_times = [p.timestamp for p in with_ones.points]
        assert without_times == with_ones_times

    def test_multiplier_count_mismatched_with_legs_raises(self):
        track = Track(points=[point(elevation=0.0, distance_from_start=d) for d in (0.0, 100.0, 200.0, 300.0)])
        anchors = [anchor(0.0, START), anchor(300.0, START + timedelta(minutes=2))]

        with pytest.raises(ValueError):
            combine(track, anchors, SportType.RUNNING, multipliers=[1.0, 1.0])

    def test_multipliers_are_sliced_per_segment_in_multi_segment_tracks(self):
        # Two segments of 2 legs each; only the second segment's legs get a
        # non-trivial multiplier, so only its own internal split should
        # deviate from an even 50/50 time split.
        track = Track(points=[point(elevation=0.0, distance_from_start=d) for d in (0.0, 100.0, 200.0, 300.0, 400.0)])
        mid_time = START + timedelta(seconds=200)
        end_time = mid_time + timedelta(seconds=300)
        anchors = [anchor(0.0, START), anchor(200.0, mid_time), anchor(400.0, end_time)]

        result = combine(track, anchors, SportType.RUNNING, multipliers=[1.0, 1.0, 1.0, 2.0])

        times = [timestamp_of(p) for p in result.points]
        # First segment: no differentiating multiplier -> even split.
        assert (times[1] - times[0]).total_seconds() == pytest.approx((times[2] - times[1]).total_seconds())
        # Second segment: leg 2->3 (multiplier 1.0) takes twice as long as
        # leg 3->4 (multiplier 2.0) once scaled to the segment's 300s budget.
        assert (times[3] - times[2]).total_seconds() == pytest.approx(200.0)
        assert (times[4] - times[3]).total_seconds() == pytest.approx(100.0)


class TestCombineReturnsSameTrack:
    def test_returns_the_same_track_object(self):
        track = Track(points=[point(elevation=0.0, distance_from_start=0.0), point(elevation=0.0, distance_from_start=100.0)])
        anchors = [anchor(0.0, START), anchor(100.0, START + timedelta(minutes=1))]

        result = combine(track, anchors, SportType.RUNNING)

        assert result is track
