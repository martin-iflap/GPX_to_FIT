import math
import statistics
from datetime import timedelta

import pytest

from gpx2fit.core.models import InputError, ModeAStop, SportType, Track, TrackPoint
from gpx2fit.core.pacing.combine import _MAX_SPEED_RATIO, combine
from gpx2fit.core.pacing.gradient import (
    GRADIENT_WINDOW_M,
    blended_speeds_from_gradients,
    calculate_gradient,
    minetti_speeds_from_gradients,
)
from tests.core.conftest import (
    START,
    anchor,
    flat_track,
    point,
    resolved_weight,
    rolling_track,
    timestamp_of,
)


def _compress_speed_toward_typical(speed: float, typical_speed: float, max_ratio: float) -> float:
    """Reference tanh soft-bound, computed independently of the implementation under test — see combine.py's own."""
    log_limit = math.log(max_ratio)
    log_ratio = math.log(speed / typical_speed)
    return typical_speed * math.exp(log_limit * math.tanh(log_ratio / log_limit))


def _leg_seconds(points: list[TrackPoint]) -> list[float]:
    """Each leg's duration in seconds, from already-stamped points."""
    return [(timestamp_of(b) - timestamp_of(a)).total_seconds() for a, b in zip(points, points[1:])]


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
    def test_zero_distance_segment_still_stamps_segment_boundaries(self):
        # Two points recorded at the same distance_from_start (e.g. a
        # duplicate/glitched GPS fix) give the segment zero real distance to
        # model, so its total modeled time is 0 even though its
        # anchor-to-anchor duration is real (gradient.py's clamp means an
        # extreme *gradient* alone, e.g. -90%, no longer zeroes out a real,
        # distance-bearing leg's speed — see test_gradient.py). Before the
        # original fix this was based on, combine() bailed out of this
        # segment without ever stamping its boundary points, leaving the
        # whole track without a start (or, symmetrically, end) timestamp for
        # fit_writer to find.
        track = Track(points=[
            point(elevation=0.0, distance_from_start=0.0),
            point(elevation=-9.0, distance_from_start=0.0),
        ])
        end_time = START + timedelta(minutes=1)
        anchors = [anchor(0.0, START), anchor(0.0, end_time)]

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
        # The degenerate (zero-distance) segment is followed by a normal,
        # flat one (0 -> 200); the later segment's own pacing must still
        # work even though the first segment never modeled any interior
        # points.
        mid_time = START + timedelta(minutes=1)
        end_time = mid_time + timedelta(minutes=2)
        track = Track(points=[
            point(elevation=0.0, distance_from_start=0.0),
            point(elevation=-9.0, distance_from_start=0.0),
            point(elevation=-9.0, distance_from_start=100.0),
            point(elevation=-9.0, distance_from_start=200.0),
        ])
        anchors = [anchor(0.0, START), anchor(0.0, mid_time), anchor(200.0, end_time)]

        result = combine(track, anchors, SportType.RUNNING)

        assert result.points[0].timestamp == START
        assert result.points[1].timestamp == mid_time
        assert result.points[-1].timestamp == end_time


class TestCombineHandlesExtremeLocalGradient:
    def test_a_single_extreme_gradient_leg_does_not_get_modeled_as_instantaneous(self):
        # One very short, very steep leg (a -90% grade "spike" over 10m —
        # the kind of thing a noisy GPS/DEM elevation reading can produce
        # even on an otherwise ordinary, moderately-graded route) sits among
        # several flat legs. Before gradient.py clamped its input domain,
        # this leg's modeled speed came out to exactly 0.0, which combine.py
        # converted to a modeled time of 0 seconds — its real 10m of
        # distance got stamped as covered instantly, and the segment's
        # single scale factor then had to spread the *entire* time budget
        # across the remaining legs, making them look faster than their own
        # gradient justified (this is the bug reported: steep sections
        # coming out as the *fastest* splits).
        track = Track(points=[
            point(elevation=0.0, distance_from_start=0.0),
            point(elevation=0.0, distance_from_start=100.0),
            point(elevation=-9.0, distance_from_start=110.0),  # -90% grade over 10m
            point(elevation=-9.0, distance_from_start=210.0),
            point(elevation=-9.0, distance_from_start=310.0),
        ])
        end_time = START + timedelta(minutes=10)
        anchors = [anchor(0.0, START), anchor(310.0, end_time)]

        result = combine(track, anchors, SportType.RUNNING)

        by_distance = {p.distance_from_start: timestamp_of(p) for p in result.points}
        spike_leg_seconds = (by_distance[110.0] - by_distance[100.0]).total_seconds()
        # The spike leg covers 10m of the 310m route; modeled sanely (a
        # finite, if slow, speed) it should take a non-negligible share of
        # the 10-minute budget, not be squeezed to near-zero.
        assert spike_leg_seconds > 1.0

        # No leg should be modeled as "free" distance that inflates every
        # other leg to compensate — every leg's pace should stay within a
        # sane range of the others, not vary by an order of magnitude.
        leg_paces = [
            (timestamp_of(b) - timestamp_of(a)).total_seconds() / (b.distance_from_start - a.distance_from_start)
            for a, b in zip(result.points, result.points[1:])
        ]
        assert max(leg_paces) / min(leg_paces) < 5.0


class TestCombineBoundsExtremeSpeedRatio:
    def test_a_sustained_steep_climb_does_not_model_as_near_stationary(self):
        # A real, *sustained* ~40% grade climb — well within gradient.py's
        # calibrated Minetti domain, so its own domain fallback doesn't
        # touch this at all (that only kicks in past ~45%). Minetti's raw
        # cost curve alone models a sustained climb like this at a small
        # fraction of the flat legs' speed, which — once scaled to fit a
        # slow enough overall pace — can be slow enough to look
        # indistinguishable from "stopped" to a real device or platform's
        # own moving-time detection discarding that whole climb's real
        # distance and duration from the activity's moving stats even
        # though combine()'s total elapsed time is correct.
        flat = [point(elevation=0.0, distance_from_start=float(d)) for d in range(0, 220, 20)]
        climb = [point(elevation=float(i) * 8.0, distance_from_start=200.0 + i * 20.0) for i in range(1, 6)]
        track = Track(points=flat + climb)
        end_time = START + timedelta(minutes=20)
        anchors = [anchor(0.0, START), anchor(track.points[-1].distance_from_start, end_time)]

        # Ground truth for the bound: combine() should compress gradient.py's
        # own raw speeds toward their median using the same tanh soft-bound
        # (see _compress_speed_toward_typical, reproduced above), not by
        # some other amount. Which curve those raw speeds come from is
        # resolve_tobler_weight's call, not this test's — a 40% grade
        # ground out at 0.25 m/s resolves to Tobler, and the bound has to
        # hold whatever the blend is.
        raw_speeds = blended_speeds_from_gradients(
            calculate_gradient(Track(points=track.points)),
            resolved_weight(track, (end_time - START).total_seconds(), SportType.RUNNING),
        )
        typical_raw_speed = statistics.median(s for s in raw_speeds if s > 0)
        raw_ratio = max(raw_speeds) / min(raw_speeds)
        expected_ratio = (
            _compress_speed_toward_typical(max(raw_speeds), typical_raw_speed, _MAX_SPEED_RATIO)
            / _compress_speed_toward_typical(min(raw_speeds), typical_raw_speed, _MAX_SPEED_RATIO)
        )

        result = combine(track, anchors, SportType.RUNNING)

        assert result.points[-1].timestamp == end_time
        leg_paces = [
            (timestamp_of(b) - timestamp_of(a)).total_seconds() / (b.distance_from_start - a.distance_from_start)
            for a, b in zip(result.points, result.points[1:])
        ]
        # A pace ratio (time/distance) is the inverse of a speed ratio, so
        # it comes out numerically equal to expected_ratio (a speed ratio).
        assert max(leg_paces) / min(leg_paces) == pytest.approx(expected_ratio)
        # Narrower than the gradient model's own spread — the point of the
        # compression. (The exact amount is pinned by the approx check above;
        # gradient.py's softened Minetti curve already narrows the spread
        # before compression, so there's less left for it to take off here.)
        assert expected_ratio < raw_ratio

    def test_two_distinctly_different_steep_legs_stay_distinguishable_not_pinned_to_one_floor(self):
        # Regression test for the previous hard min/max clamp's failure
        # mode: it pinned every leg past its bound to the exact same floor
        # (or ceiling) speed, so a route with several *differently* steep
        # sections would show them all at one identical, flat pace instead
        # of each other's own, still-slower-than-flat pace. Two climbs of
        # different steepness must end up at two different (both slow)
        # paces, not collapse onto one.
        track = Track(points=[
            point(elevation=0.0, distance_from_start=0.0),
            point(elevation=0.0, distance_from_start=20.0),
            point(elevation=12.0, distance_from_start=40.0),  # 60% grade
            point(elevation=12.0, distance_from_start=60.0),
            point(elevation=28.0, distance_from_start=80.0),  # 80% grade
        ])
        end_time = START + timedelta(minutes=20)
        anchors = [anchor(0.0, START), anchor(80.0, end_time)]

        result = combine(track, anchors, SportType.RUNNING)

        by_distance = {p.distance_from_start: timestamp_of(p) for p in result.points}
        sixty_percent_leg_seconds = (by_distance[40.0] - by_distance[20.0]).total_seconds()
        eighty_percent_leg_seconds = (by_distance[80.0] - by_distance[60.0]).total_seconds()
        assert sixty_percent_leg_seconds != eighty_percent_leg_seconds
        assert eighty_percent_leg_seconds > sixty_percent_leg_seconds


class TestCombineUsesSmoothedWholeTrackGradient:
    def test_dem_quantized_gentle_slope_does_not_produce_spiky_leg_paces(self):
        # A uniform 3% slope sampled every 5 m with elevation rounded to
        # whole metres. Paced on per-leg rise/run, this alternates between
        # 0% and 20% legs — a pace graph of flat stretches and spikes on
        # what is really a steady, even climb.
        track = Track(points=[
            point(elevation=float(round(0.03 * d)), distance_from_start=float(d)) for d in range(0, 1005, 5)
        ])
        anchors = [anchor(0.0, START), anchor(1000.0, START + timedelta(minutes=10))]

        result = combine(track, anchors, SportType.RUNNING)

        half_window = GRADIENT_WINDOW_M / 2
        interior_paces = [
            (timestamp_of(b) - timestamp_of(a)).total_seconds() / (b.distance_from_start - a.distance_from_start)
            for a, b in zip(result.points, result.points[1:])
            if a.distance_from_start >= half_window and b.distance_from_start <= 1000.0 - half_window
        ]
        assert max(interior_paces) / min(interior_paces) < 1.1

    def test_gradient_window_reaches_across_mid_route_anchors(self):
        # A mid-route anchor is a known time, not a break in the terrain:
        # legs right after it must be smoothed with the terrain before it
        # too, not with a window truncated at the anchor.
        track = Track(points=[
            point(elevation=0.0 if d <= 200 else 1.0, distance_from_start=float(d)) for d in range(0, 410, 10)
        ])
        anchor_index = 20  # the point at 200 m
        mid_time = START + timedelta(minutes=1)
        end_time = mid_time + timedelta(minutes=1)
        anchors = [anchor(0.0, START), anchor(200.0, mid_time), anchor(400.0, end_time)]

        whole_track_gradients = calculate_gradient(Track(points=track.points))[anchor_index:]
        segment_only_gradients = calculate_gradient(Track(points=track.points[anchor_index:]))
        # premise: truncating the window at the anchor would read a steeper step
        assert segment_only_gradients[0] > whole_track_gradients[0] * 1.5
        # premise: this is a brisk, near-flat workout, so it paces on pure
        # Minetti — which keeps the expected speeds below a single curve.
        assert resolved_weight(track, (end_time - START).total_seconds(), SportType.RUNNING) == 0.0

        result = combine(track, anchors, SportType.RUNNING)

        speeds = minetti_speeds_from_gradients(whole_track_gradients)
        typical = statistics.median(speeds)
        compressed = [_compress_speed_toward_typical(s, typical, _MAX_SPEED_RATIO) for s in speeds]
        modeled = [10.0 / s for s in compressed]
        budget = (end_time - mid_time).total_seconds()
        expected_leg_seconds = [budget * m / sum(modeled) for m in modeled]

        segment = result.points[anchor_index:]
        actual_leg_seconds = [(timestamp_of(b) - timestamp_of(a)).total_seconds() for a, b in zip(segment, segment[1:])]
        assert actual_leg_seconds == pytest.approx(expected_leg_seconds, rel=1e-4)


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
        # Gently rolling — well below either sport's verticality threshold,
        # so these tests exercise the speed criterion alone.
        track = Track(points=[
            point(elevation=0.0, distance_from_start=0.0),
            point(elevation=10.0, distance_from_start=250.0),
            point(elevation=20.0, distance_from_start=500.0),
            point(elevation=7.0, distance_from_start=750.0),
            point(elevation=0.0, distance_from_start=1000.0),
        ])
        anchors = [anchor(0.0, START), anchor(1000.0, START + duration)]
        return combine(track, anchors, sport)

    def test_a_slow_hike_still_produces_valid_pacing(self):
        result = self._run_with_avg_speed(SportType.HIKING, avg_speed_mps=1.0)
        assert result.points[0].timestamp is not None
        assert result.points[-1].timestamp is not None
        assert result.points[0].timestamp < result.points[-1].timestamp

    def test_a_brisk_hike_still_produces_valid_pacing(self):
        result = self._run_with_avg_speed(SportType.HIKING, avg_speed_mps=3.0)
        assert result.points[0].timestamp is not None
        assert result.points[-1].timestamp is not None
        assert result.points[0].timestamp < result.points[-1].timestamp

    def test_the_same_terrain_paced_slower_shifts_toward_the_walking_curve(self):
        track = flat_track([float(d) for d in range(0, 5050, 50)])
        fast = self._weight_for(track, avg_speed_mps=3.0)
        slow = self._weight_for(track, avg_speed_mps=1.2)
        assert fast < slow

    def _weight_for(self, track: Track, avg_speed_mps: float) -> float:
        return resolved_weight(track, track.total_distance / avg_speed_mps, SportType.RUNNING)

    def test_a_workout_that_resolves_to_the_same_curve_for_both_sports_paces_identically(self):
        # Well above both sports' speed thresholds and well below both
        # verticality ones, so the declared sport can't change the answer.
        running = self._run_with_avg_speed(SportType.RUNNING, avg_speed_mps=4.0)
        hiking = self._run_with_avg_speed(SportType.HIKING, avg_speed_mps=4.0)

        assert [p.timestamp for p in running.points] == [p.timestamp for p in hiking.points]

    def test_the_declared_sport_changes_the_pacing_of_a_borderline_workout(self):
        running = self._run_with_avg_speed(SportType.RUNNING, avg_speed_mps=2.0)
        hiking = self._run_with_avg_speed(SportType.HIKING, avg_speed_mps=2.0)

        assert [p.timestamp for p in running.points] != [p.timestamp for p in hiking.points]
        # Same anchors either way — only the shape in between differs.
        assert running.points[-1].timestamp == hiking.points[-1].timestamp

    def test_a_long_stop_does_not_make_a_brisk_run_pace_like_a_walk(self):
        # combine() resolves the blend from *active* time, so a 45-minute
        # lunch break must leave the moving legs paced exactly as they were.
        moving_time = timedelta(seconds=2000.0 / 3.5)
        stop_duration = timedelta(minutes=45)

        without_stop = combine(
            rolling_track(distance=2000.0, climb_per_km=40.0),
            [anchor(0.0, START), anchor(2000.0, START + moving_time)],
            SportType.RUNNING,
        )

        with_stop_track = rolling_track(distance=2000.0, climb_per_km=40.0)
        stop_index = 20  # the point at 1000 m
        stop_point = with_stop_track.points[stop_index]
        # What pacing.stops.expand_track_with_stops produces: a duplicated,
        # zero-distance point at the stop's own distance.
        with_stop_track.points.insert(
            stop_index, point(elevation=stop_point.elevation, distance_from_start=stop_point.distance_from_start)
        )
        with_stop = combine(
            with_stop_track,
            [anchor(0.0, START), anchor(2000.0, START + moving_time + stop_duration)],
            SportType.RUNNING,
            mode_a_stops=[ModeAStop(1000.0, stop_duration)],
        )

        moving_legs_without = _leg_seconds(without_stop.points)
        moving_legs_with = [
            seconds for seconds, (a, b) in zip(
                _leg_seconds(with_stop.points), zip(with_stop.points, with_stop.points[1:])
            )
            if b.distance_from_start > a.distance_from_start
        ]
        # Not bit-identical: the stop's duplicated point is one extra sample
        # inside the gradient smoothing window, which nudges the surrounding legs.
        # Had the stop been counted as moving time, the workout would
        # have resolved to Tobler instead and the legs would differ by far
        # more than this.
        assert moving_legs_with == pytest.approx(moving_legs_without, rel=1e-3)


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


def _expected_leg_seconds_for_multipliers(multipliers: list[float], budget_seconds: float) -> list[float]:
    """Independently reproduce combine()'s multiplier -> compressed-speed -> scaled-time math for a flat (equal base speed, equal distance) segment."""
    typical = statistics.median(multipliers)
    compressed = [_compress_speed_toward_typical(m, typical, _MAX_SPEED_RATIO) for m in multipliers]
    seconds_ratio = [1.0 / c for c in compressed]
    total_ratio = sum(seconds_ratio)
    return [budget_seconds * r / total_ratio for r in seconds_ratio]


class TestCombineSurfaceMultipliers:
    def test_leg_times_scale_by_their_compressed_multiplier_ratio(self):
        # Flat track (gradient 0 on every leg) so every leg's modeled speed
        # is identical before multipliers are applied — any difference in
        # the resulting leg durations comes from the multipliers, after
        # combine()'s own speed compression (see _MAX_SPEED_RATIO)
        # pulls extreme ratios toward the segment's median, the same as it
        # does for extreme gradients — so legs with multipliers 1/2/4 no
        # longer land on *exactly* inverse-proportional (4:2:1) times, only
        # on the same order (slowest multiplier still takes the most time).
        track = Track(points=[point(elevation=0.0, distance_from_start=d) for d in (0.0, 100.0, 200.0, 300.0)])
        end_time = START + timedelta(seconds=700)
        anchors = [anchor(0.0, START), anchor(300.0, end_time)]

        result = combine(track, anchors, SportType.RUNNING, multipliers=[1.0, 2.0, 4.0])

        times = [timestamp_of(p) for p in result.points]
        leg_seconds = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
        assert leg_seconds[0] > leg_seconds[1] > leg_seconds[2]
        for actual, expected in zip(leg_seconds, _expected_leg_seconds_for_multipliers([1.0, 2.0, 4.0], 700.0)):
            assert actual == pytest.approx(expected)

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
        # Second segment: leg 2->3 (multiplier 1.0) takes longer than leg
        # 3->4 (multiplier 2.0), by the same compressed ratio combine() uses
        # (see _expected_leg_seconds_for_multipliers) — not exactly double,
        # since compression pulls the raw 2x ratio toward the median.
        second_segment_seconds = [(times[3] - times[2]).total_seconds(), (times[4] - times[3]).total_seconds()]
        for actual, expected in zip(second_segment_seconds, _expected_leg_seconds_for_multipliers([1.0, 2.0], 300.0)):
            assert actual == pytest.approx(expected)


class TestCombineReturnsSameTrack:
    def test_returns_the_same_track_object(self):
        track = Track(points=[point(elevation=0.0, distance_from_start=0.0), point(elevation=0.0, distance_from_start=100.0)])
        anchors = [anchor(0.0, START), anchor(100.0, START + timedelta(minutes=1))]

        result = combine(track, anchors, SportType.RUNNING)

        assert result is track
