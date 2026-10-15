import math
import random
import statistics
from datetime import timedelta
from typing import get_args

import pytest

from gpx2fit.core.fit_writer import _resample
from gpx2fit.core.gpx_reader import MAX_LEG_M, densify_points
from gpx2fit.core.models import Anchor, AnchorSource, InputError, ModeAStop, SportType, Track, TrackPoint
from gpx2fit.core.pacing.anchors import add_start_end_anchors
from gpx2fit.core.pacing import acceleration as acceleration_module
from gpx2fit.core.pacing import combine as combine_module
from gpx2fit.core.pacing.combine import combine
from gpx2fit.core.pacing.curve_selection import DEFAULT_SMOOTHNESS, MAX_SPEED_RATIO_BOUNDS, resolve_curve_shape
from gpx2fit.core.pacing.gradient import (
    GRADIENT_WINDOW_M,
    blended_speeds_from_gradients,
    calculate_gradient,
    minetti_speeds_from_gradients,
)
from gpx2fit.core.pacing.stops import expand_track_with_stops
from tests.core.conftest import (
    START,
    anchor,
    flat_track,
    point,
    resolved_curve_shape,
    resolved_max_speed_ratio,
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

    @pytest.mark.parametrize("sport", [SportType.HIKING, SportType.RUNNING])
    def test_a_leg_whose_speed_underflows_still_takes_time(self, sport):
        # A 99999 m elevation jump makes the gradient so steep that Tobler's
        # exp(-3.5 * |g|) underflows to 0.0. That leg must not be crossed instantly.
        distances = [i * 10.0 for i in range(21)]
        track = Track(points=[
            point(elevation=99999.0 if i == 10 else 100.0, distance_from_start=d) for i, d in enumerate(distances)
        ])
        anchors = [anchor(0.0, START), anchor(distances[-1], START + timedelta(hours=3))]

        result = combine(track, anchors, sport)

        assert all(seconds > 0 for seconds in _leg_seconds(result.points))

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

    def test_ordering_error_names_the_start_rather_than_a_distance(self):
        # The boundary anchors built by add_start_end_anchors, and a photo
        # timed before the start: the message must point at "the start"
        # the user typed, not at an anonymous "anchor at 0.00 km".
        track = Track(points=[
            point(elevation=0.0, distance_from_start=0.0),
            point(elevation=0.0, distance_from_start=100.0),
            point(elevation=0.0, distance_from_start=200.0),
        ])
        boundary = add_start_end_anchors(track, START, duration=timedelta(minutes=10))
        photo = anchor(100.0, START - timedelta(minutes=1), source="photo")

        with pytest.raises(InputError, match=r"than the start \(") as error:
            combine(track, [boundary[0], photo, boundary[1]], SportType.RUNNING)
        assert "anchor at 0.00 km" not in str(error.value)
        assert "photo anchor at 0.10 km" in str(error.value)

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


class TestCombineImpossibleSpeed:
    """Anchor times that put a segment beyond any speed on foot are refused, naming the two points."""

    @staticmethod
    def _seconds_for(distance_m: float, speed_kmh: float) -> float:
        return distance_m / (speed_kmh / 3.6)

    def test_a_segment_faster_than_the_limit_is_an_input_error(self):
        track = flat_track([0.0, 500.0, 1000.0])
        seconds = self._seconds_for(1000.0, combine_module.MAX_PLAUSIBLE_SPEED_KMH * 1.05)
        boundary = add_start_end_anchors(track, START, duration=timedelta(seconds=seconds))

        with pytest.raises(InputError, match=r"63 km/h") as error:
            combine(track, boundary, SportType.RUNNING)
        assert "start (" in str(error.value)
        assert "end (" in str(error.value)

    def test_a_segment_just_under_the_limit_is_paced(self):
        track = flat_track([0.0, 500.0, 1000.0])
        seconds = self._seconds_for(1000.0, combine_module.MAX_PLAUSIBLE_SPEED_KMH * 0.95)
        end_time = START + timedelta(seconds=seconds)

        result = combine(track, [anchor(0.0, START), anchor(1000.0, end_time)], SportType.RUNNING)

        assert result.points[-1].timestamp == end_time

    def test_only_the_offending_segment_is_named(self):
        track = flat_track([0.0, 1000.0, 2000.0])
        mid_time = START + timedelta(minutes=10)
        too_soon = mid_time + timedelta(seconds=self._seconds_for(1000.0, combine_module.MAX_PLAUSIBLE_SPEED_KMH * 2))
        anchors = [anchor(0.0, START), anchor(1000.0, mid_time, source="photo"), anchor(2000.0, too_soon)]

        with pytest.raises(InputError, match=r"photo anchor at 1\.00 km .* anchor at 2\.00 km"):
            combine(track, anchors, SportType.HIKING)

    @pytest.mark.parametrize(("segment_m", "refused"), [
        (combine_module.PLAUSIBILITY_MIN_SEGMENT_M, True),
        (combine_module.PLAUSIBILITY_MIN_SEGMENT_M - 1.0, False),
    ])
    def test_a_short_segment_is_not_judged(self, segment_m, refused):
        # A photo a second after the start, matched a few tens of metres along, is GPS
        # error. Like the pace warnings, only segments from PLAUSIBILITY_MIN_SEGMENT_M up are judged.
        track = flat_track([0.0, segment_m, 5000.0])
        too_soon = START + timedelta(seconds=self._seconds_for(segment_m, combine_module.MAX_PLAUSIBLE_SPEED_KMH * 2))
        anchors = [anchor(0.0, START, "start"), anchor(segment_m, too_soon, "photo"),
                   anchor(5000.0, START + timedelta(hours=1), "end")]

        if refused:
            with pytest.raises(InputError, match="photo anchor"):
                combine(track, anchors, SportType.HIKING)
        else:
            assert combine(track, anchors, SportType.HIKING).points[1].timestamp == too_soon

    def test_moving_time_left_after_the_stops_is_what_counts(self):
        # 1 km in 10 min is a walk, but a 9.5 min stop leaves 30 s to cover it.
        stops = [ModeAStop(500.0, timedelta(minutes=9, seconds=30))]
        track = Track(points=expand_track_with_stops(flat_track([0.0, 500.0, 1000.0]).points, [*stops]))
        anchors = [anchor(0.0, START), anchor(1000.0, START + timedelta(minutes=10))]

        with pytest.raises(InputError, match="stop"):
            combine(track, anchors, SportType.HIKING, mode_a_stops=stops)


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
        # fraction of the flat legs' speed, which - once scaled to fit a
        # slow enough overall pace. Can be slow enough to look
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
            resolved_curve_shape(track, SportType.RUNNING),
        )
        typical_raw_speed = statistics.median(s for s in raw_speeds if s > 0)
        raw_ratio = max(raw_speeds) / min(raw_speeds)
        max_ratio = resolved_max_speed_ratio(track, SportType.RUNNING)
        expected_ratio = (
            _compress_speed_toward_typical(max(raw_speeds), typical_raw_speed, max_ratio)
            / _compress_speed_toward_typical(min(raw_speeds), typical_raw_speed, max_ratio)
        )

        result = combine(track, anchors, SportType.RUNNING, vary_pace=False)

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

        result = combine(track, anchors, SportType.RUNNING, vary_pace=False)

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
        # too, not with a window cut off at the anchor.
        track = Track(points=[
            point(elevation=0.0 if d <= 190 else 1.0, distance_from_start=float(d)) for d in range(0, 410, 10)
        ])
        anchor_index = 20  # the point at 200 m
        mid_time = START + timedelta(minutes=1)
        end_time = mid_time + timedelta(minutes=1)
        anchors = [anchor(0.0, START), anchor(200.0, mid_time), anchor(400.0, end_time)]

        whole_track_gradients = calculate_gradient(Track(points=track.points))[anchor_index:]
        segment_only_gradients = calculate_gradient(Track(points=track.points[anchor_index:]))
        # premise: a window cut off at the anchor would miss the step just before it
        assert whole_track_gradients[0] > 0.0 == segment_only_gradients[0]
        # premise: this is a brisk, near-flat workout, so it paces on pure
        # Minetti — which keeps the expected speeds below a single curve.
        assert resolved_weight(track, (end_time - START).total_seconds(), SportType.RUNNING) == 0.0

        result = combine(track, anchors, SportType.RUNNING, vary_pace=False)

        shape = resolved_curve_shape(track, SportType.RUNNING)
        speeds = minetti_speeds_from_gradients(whole_track_gradients, shape.minetti.uphill, shape.minetti.downhill)
        typical = statistics.median(speeds)
        max_ratio = resolved_max_speed_ratio(track, SportType.RUNNING)
        compressed = [_compress_speed_toward_typical(s, typical, max_ratio) for s in speeds]
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

    def test_on_the_same_curve_hiking_swings_no_wider_than_running(self):
        # Well above both sports' speed thresholds and well below both
        # verticality ones, so both resolve to the same curve. The sport
        # then only changes how far leg speeds may swing (see
        # resolve_max_speed_ratio), and hiking's bound is the tighter one.
        running = self._run_with_avg_speed(SportType.RUNNING, avg_speed_mps=4.0)
        hiking = self._run_with_avg_speed(SportType.HIKING, avg_speed_mps=4.0)

        def pace_spread(track: Track) -> float:
            paces = [
                seconds / (b.distance_from_start - a.distance_from_start)
                for seconds, a, b in zip(_leg_seconds(track.points), track.points, track.points[1:])
            ]
            return max(paces) / min(paces)

        assert resolved_weight(running, 1000.0 / 4.0, SportType.RUNNING) == 0.0
        assert resolved_weight(hiking, 1000.0 / 4.0, SportType.HIKING) == 0.0
        assert pace_spread(hiking) <= pace_spread(running)

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
        # premise: counting the stop as moving time really would change the
        # curve. Without this, a retune of the thresholds could leave both
        # readings on the same curve and the test would pass without testing anything.
        probe = rolling_track(distance=2000.0, climb_per_km=40.0)
        assert resolved_weight(probe, (moving_time + stop_duration).total_seconds(), SportType.RUNNING) > (
            resolved_weight(probe, moving_time.total_seconds(), SportType.RUNNING) + 0.5
        )

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
        assert moving_legs_with == pytest.approx(moving_legs_without, rel=3e-3)


class TestCombineSmoothness:
    """The user's smoothness level, observed through the paced timestamps rather than the ratio itself."""

    DURATION = timedelta(minutes=40)

    def _paced(self, smoothness: int | None = None, climb_per_km: float = 100.0) -> Track:
        track = rolling_track(distance=5000.0, climb_per_km=climb_per_km)
        anchors = [anchor(0.0, START), anchor(track.total_distance, START + self.DURATION)]
        # Variation off: these pin how the level scales the gradient swing. At
        # level 10 that swing is narrow enough to count as steady and draw noise,
        # which TestCombinePaceVariation covers.
        if smoothness is None:
            return combine(track, anchors, SportType.RUNNING, vary_pace=False)
        return combine(track, anchors, SportType.RUNNING, smoothness=smoothness, vary_pace=False)

    @staticmethod
    def _log_leg_speeds(track: Track) -> list[float]:
        return [
            math.log((b.distance_from_start - a.distance_from_start) / seconds)
            for seconds, a, b in zip(_leg_seconds(track.points), track.points, track.points[1:])
        ]

    def _log_spread(self, track: Track) -> float:
        logs = self._log_leg_speeds(track)
        return max(logs) - min(logs)

    def test_omitting_smoothness_paces_exactly_like_the_default_level(self):
        omitted = [p.timestamp for p in self._paced().points]
        assert omitted == [p.timestamp for p in self._paced(DEFAULT_SMOOTHNESS).points]
        # The default must really be the middle of the scale, not an alias of
        # an end level that happens to share the name.
        assert omitted != [p.timestamp for p in self._paced(1).points]
        assert omitted != [p.timestamp for p in self._paced(10).points]

    def test_a_smoother_level_narrows_the_speed_swing(self):
        roughest, default, smoothest = (self._log_spread(self._paced(level)) for level in (1, DEFAULT_SMOOTHNESS, 10))
        assert roughest > default > smoothest
        # A real difference, not a rounding one: the smoothest level at least
        # halves the default swing.
        assert smoothest < 0.5 * default

    @pytest.mark.parametrize("level", [1, DEFAULT_SMOOTHNESS, 10])
    def test_the_entered_times_are_still_hit_exactly(self, level):
        track = self._paced(level)
        assert track.points[0].timestamp == START
        assert track.points[-1].timestamp == START + self.DURATION

    @pytest.mark.parametrize("level", [1, 3, 8, 10])
    def test_the_level_scales_every_legs_deviation_by_the_same_factor(self, level):
        # The curve exponents and the tanh bound both scale with log(max_ratio),
        # so moving the slider should multiply every leg's log-speed deviation by
        # the same factor — the shape of the activity kept, only its amplitude
        # changed. If the bound moved but the curve didn't follow, a smoother
        # level would squash the steep legs harder than the gentle ones and
        # flatten them into plateaus.
        # 60 m/km (about ±12%) rather than steeper: running's fitted descents
        # slow nearly as much as its climbs by ±20%, so a steeper sawtooth
        # paces almost evenly and leaves nothing to scale.
        climb_per_km = 60.0
        track = rolling_track(distance=5000.0, climb_per_km=climb_per_km)
        factor = (
            math.log(resolved_max_speed_ratio(track, SportType.RUNNING, smoothness=level))
            / math.log(resolved_max_speed_ratio(track, SportType.RUNNING))
        )

        def centred(logs: list[float]) -> list[float]:
            mean = statistics.fmean(logs)
            return [value - mean for value in logs]

        default = centred(self._log_leg_speeds(self._paced(DEFAULT_SMOOTHNESS, climb_per_km)))
        scaled = centred(self._log_leg_speeds(self._paced(level, climb_per_km)))
        assert max(abs(value) for value in default) > 0.1  # premise: the default really swings
        for got, reference in zip(scaled, default):
            assert got == pytest.approx(factor * reference, abs=0.01)


class TestCombinePaceVariation:
    """Steady stretches get a natural wander in pace; terrain that already varies the pace gets none."""

    DURATION = timedelta(minutes=100)

    @staticmethod
    def _flat(distance: float = 20_000.0) -> Track:
        # Distinct coordinates, so the route has a seed of its own.
        return Track(points=[
            point(lat=46.0 + d * 1e-5, lon=14.0, distance_from_start=float(d)) for d in range(0, int(distance) + 1, 20)
        ])

    @staticmethod
    def _log_speed_spread(track: Track) -> float:
        logs = [
            math.log((b.distance_from_start - a.distance_from_start) / seconds)
            for seconds, a, b in zip(_leg_seconds(track.points), track.points, track.points[1:])
            if b.distance_from_start > a.distance_from_start
        ]
        return statistics.pstdev(logs)

    def _paced(self, track: Track, **kwargs) -> Track:
        anchors = [anchor(0.0, START), anchor(track.total_distance, START + self.DURATION)]
        return combine(track, anchors, SportType.RUNNING, **kwargs)

    def test_a_flat_route_no_longer_paces_at_one_constant_speed(self):
        assert self._log_speed_spread(self._paced(self._flat(), vary_pace=False)) < 1e-9  # premise
        spread = self._log_speed_spread(self._paced(self._flat()))
        assert 0.03 < spread < 0.09

    def test_the_variation_is_the_same_every_time(self):
        first = [p.timestamp for p in self._paced(self._flat()).points]
        assert first == [p.timestamp for p in self._paced(self._flat()).points]

    def test_rolling_terrain_is_left_exactly_as_the_gradient_paces_it(self):
        # 100 m climbs and descents, so every window the steadiness is
        # judged over (VariationSettings.window_m) spans a change of slope.
        # Longer even climbs count as steady and are varied on purpose.
        def rolling() -> Track:
            return rolling_track(distance=5000.0, climb_per_km=100.0, leg_m=25.0)

        varied = [p.timestamp for p in self._paced(rolling()).points]
        assert varied == [p.timestamp for p in self._paced(rolling(), vary_pace=False).points]

    def test_a_smoother_level_varies_less(self):
        roughest = self._log_speed_spread(self._paced(self._flat(), smoothness=1))
        smoothest = self._log_speed_spread(self._paced(self._flat(), smoothness=10))
        assert smoothest < 0.3 * roughest

    def test_anchors_and_stops_are_still_hit_exactly(self):
        track = self._flat(10_000.0)
        stop_index = 250  # the point at 5 km
        stop_point = track.points[stop_index]
        track.points.insert(stop_index + 1, point(
            lat=stop_point.lat, lon=stop_point.lon, distance_from_start=stop_point.distance_from_start,
        ))
        mid_time = START + timedelta(minutes=12)
        end_time = START + timedelta(minutes=60)
        stop_duration = timedelta(minutes=5)
        anchors = [anchor(0.0, START), anchor(2000.0, mid_time), anchor(10_000.0, end_time)]

        result = combine(track, anchors, SportType.RUNNING, mode_a_stops=[ModeAStop(5000.0, stop_duration)])

        by_distance = {p.distance_from_start: timestamp_of(p) for p in result.points}
        assert by_distance[2000.0] == mid_time
        assert result.points[-1].timestamp == end_time
        assert timestamp_of(result.points[stop_index + 1]) - timestamp_of(result.points[stop_index]) == stop_duration


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

        result = combine(track, anchors, SportType.RUNNING, mode_a_stops=[ModeAStop(100.0, duration)], vary_pace=False)

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

        result = combine(track, anchors, SportType.RUNNING, mode_a_stops=mode_a_stops, vary_pace=False)

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

        result = combine(track, anchors, SportType.RUNNING, mode_a_stops=mode_a_stops, vary_pace=False)

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

        result = combine(track, anchors, SportType.RUNNING, mode_a_stops=[ModeAStop(250.0, mode_a_duration)], vary_pace=False)

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
    """Independently reproduce combine()'s multiplier -> compressed-speed -> scaled-time math for a flat (equal base speed, equal distance) running segment."""
    typical = statistics.median(multipliers)
    max_ratio = MAX_SPEED_RATIO_BOUNDS[SportType.RUNNING].flat
    compressed = [_compress_speed_toward_typical(m, typical, max_ratio) for m in multipliers]
    seconds_ratio = [1.0 / c for c in compressed]
    total_ratio = sum(seconds_ratio)
    return [budget_seconds * r / total_ratio for r in seconds_ratio]


class TestCombineSurfaceMultipliers:
    def test_leg_times_scale_by_their_compressed_multiplier_ratio(self):
        # Flat track (gradient 0 on every leg) so every leg's modeled speed
        # is identical before multipliers are applied — any difference in
        # the resulting leg durations comes from the multipliers, after
        # combine()'s own speed compression (see resolve_max_speed_ratio)
        # pulls extreme ratios toward the segment's median, the same as it
        # does for extreme gradients — so legs with multipliers 1/2/4 no
        # longer land on *exactly* inverse-proportional (4:2:1) times, only
        # on the same order (slowest multiplier still takes the most time).
        track = Track(points=[point(elevation=0.0, distance_from_start=d) for d in (0.0, 100.0, 200.0, 300.0)])
        end_time = START + timedelta(seconds=700)
        anchors = [anchor(0.0, START), anchor(300.0, end_time)]

        result = combine(track, anchors, SportType.RUNNING, multipliers=[1.0, 2.0, 4.0], vary_pace=False)

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

    def test_one_multiplier_too_many_raises_too(self):
        # Slicing per segment would silently drop the extra entry.
        track = Track(points=[point(elevation=0.0, distance_from_start=d) for d in (0.0, 100.0, 200.0, 300.0)])
        anchors = [anchor(0.0, START), anchor(300.0, START + timedelta(minutes=2))]

        with pytest.raises(ValueError, match="multipliers"):
            combine(track, anchors, SportType.RUNNING, multipliers=[1.0, 1.0, 1.0, 1.0])

    def test_multipliers_are_sliced_per_segment_in_multi_segment_tracks(self):
        # Two segments of 2 legs each; only the second segment's legs get a
        # non-trivial multiplier, so only its own internal split should
        # deviate from an even 50/50 time split.
        track = Track(points=[point(elevation=0.0, distance_from_start=d) for d in (0.0, 100.0, 200.0, 300.0, 400.0)])
        mid_time = START + timedelta(seconds=200)
        end_time = mid_time + timedelta(seconds=300)
        anchors = [anchor(0.0, START), anchor(200.0, mid_time), anchor(400.0, end_time)]

        result = combine(track, anchors, SportType.RUNNING, multipliers=[1.0, 1.0, 1.0, 2.0], vary_pace=False)

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


class TestBaseSpeeds:
    """The per-leg speeds both the pace variation and every segment start from."""

    GRADIENTS = [-0.2, -0.05, 0.0, 0.08, 0.3]
    WEIGHT = 0.4
    SHAPE = resolve_curve_shape(2.2, SportType.RUNNING)

    def _curve(self) -> list[float]:
        return blended_speeds_from_gradients(self.GRADIENTS, self.WEIGHT, self.SHAPE)

    def test_without_surface_data_it_is_the_blended_curve(self):
        assert combine_module._base_speeds(self.GRADIENTS, self.WEIGHT, self.SHAPE, None) == self._curve()

    def test_each_leg_is_scaled_by_its_own_surface_multiplier(self):
        multipliers = [0.5, 1.0, 1.3, 0.8, 2.0]
        speeds = combine_module._base_speeds(self.GRADIENTS, self.WEIGHT, self.SHAPE, multipliers)
        assert speeds == pytest.approx([c * m for c, m in zip(self._curve(), multipliers)])

    @pytest.mark.parametrize("multiplier", [0.0, -1.0, 0.01])
    def test_a_surface_multiplier_never_drags_a_leg_below_the_floor(self, multiplier):
        multipliers = [1.0, multiplier, 1.0, 1.0, 1.0]
        speeds = combine_module._base_speeds(self.GRADIENTS, self.WEIGHT, self.SHAPE, multipliers)
        assert speeds[1] == pytest.approx(self._curve()[1] * combine_module._MIN_SURFACE_MULTIPLIER)

    def test_a_multiplier_above_the_floor_is_kept_as_it_is(self):
        multipliers = [1.0, 0.06, 1.0, 1.0, 1.0]
        speeds = combine_module._base_speeds(self.GRADIENTS, self.WEIGHT, self.SHAPE, multipliers)
        assert speeds[1] == pytest.approx(self._curve()[1] * 0.06)


class TestCombineVariationSeesTheSurface:
    def test_a_flat_route_whose_surface_keeps_changing_gets_no_added_wander(self):
        # The gradient alone is steady here, but the surface changes every
        # 40 m, so the speeds already vary and wander must stay off.
        def flat() -> Track:
            return Track(points=[
                point(lat=46.0 + d * 1e-5, lon=14.0, distance_from_start=float(d)) for d in range(0, 4001, 20)
            ])

        multipliers = [1.0 if (leg // 2) % 2 == 0 else 0.8 for leg in range(len(flat().points) - 1)]
        anchors = [anchor(0.0, START), anchor(4000.0, START + timedelta(minutes=25))]

        varied = combine(flat(), anchors, SportType.RUNNING, multipliers=multipliers)
        unvaried = combine(flat(), anchors, SportType.RUNNING, multipliers=multipliers, vary_pace=False)
        assert [p.timestamp for p in varied.points] == [p.timestamp for p in unvaried.points]

        # Premise: without the surface the same route does get wander.
        assert [p.timestamp for p in combine(flat(), anchors, SportType.RUNNING).points] != [
            p.timestamp for p in combine(flat(), anchors, SportType.RUNNING, vary_pace=False).points
        ]


class TestAnchorDescriptions:
    def test_every_anchor_source_has_its_own_label(self):
        assert set(combine_module._ANCHOR_SOURCE_LABELS) == set(get_args(AnchorSource.__value__))

    @pytest.mark.parametrize("source, described", [
        ("start", "start (2024-01-01 08:00)"),
        ("photo", "photo anchor at 1.50 km (2024-01-01 08:00)"),
        ("stop_departure", "stop departure at 1.50 km (2024-01-01 08:00)"),
    ])
    def test_an_anchor_is_named_by_its_source(self, source, described):
        assert combine_module._describe_anchor(Anchor(1500.0, START, source)) == described


class TestCombineReturnsSameTrack:
    def test_returns_the_same_track_object(self):
        track = Track(points=[point(elevation=0.0, distance_from_start=0.0), point(elevation=0.0, distance_from_start=100.0)])
        anchors = [anchor(0.0, START), anchor(100.0, START + timedelta(minutes=1))]

        result = combine(track, anchors, SportType.RUNNING)

        assert result is track


def _flat_10k() -> Track:
    return flat_track([i * 100.0 for i in range(101)])


def _paced_warnings(track: Track, anchors, **kwargs) -> list[str]:
    warnings: list[str] = []
    combine(track, anchors, SportType.RUNNING, warnings=warnings, **kwargs)
    return warnings


class TestCombineImplausibleSegmentPace:
    """A6: warn when an anchor makes one segment's terrain-adjusted pace far off the workout's.

    On a flat track without pace variation every leg's modeled time is proportional to its
    distance, so for a 10 km, 1 h activity a first segment of d metres taking t seconds is
    (3600 / 10000) / (t / d) times the workout's pace.
    """

    def test_a_proportional_anchor_does_not_warn(self):
        anchors = [anchor(0.0, START), anchor(5000.0, START + timedelta(seconds=1800)),
                   anchor(10000.0, START + timedelta(hours=1))]

        assert _paced_warnings(_flat_10k(), anchors) == []

    def test_a_far_too_fast_segment_warns_naming_both_ends(self):
        # 1 km in 90 s against 0.36 s/m overall: 4x.
        anchors = [anchor(0.0, START, "start"), anchor(1000.0, START + timedelta(seconds=90)),
                   anchor(10000.0, START + timedelta(hours=1), "end")]

        warnings = _paced_warnings(_flat_10k(), anchors, vary_pace=False)

        assert len(warnings) == 1
        assert "start (2024-01-01 08:00)" in warnings[0]
        assert "anchor at 1.00 km (2024-01-01 08:01)" in warnings[0]
        assert "4.0× faster" in warnings[0]

    def test_a_far_too_slow_segment_warns(self):
        # 1 km in 1800 s: 0.2x, i.e. 5x slower.
        anchors = [anchor(0.0, START), anchor(1000.0, START + timedelta(seconds=1800)),
                   anchor(10000.0, START + timedelta(hours=1))]

        warnings = _paced_warnings(_flat_10k(), anchors, vary_pace=False)

        assert len(warnings) == 1
        assert "5.0× slower" in warnings[0]
        assert "anchor at 1.00 km" in warnings[0]

    def test_one_misplaced_anchor_is_named_once_rather_than_as_two_segments(self):
        # 0.72 s/m overall. 2 km in 90 min is 2.7 s/m (3.75x slower), then 8 km in 30 min
        # is 0.225 s/m (3.2x faster). Both sides
        # share the anchor at 2 km, which is almost certainly the wrong one.
        anchors = [anchor(0.0, START, "start"), anchor(2000.0, START + timedelta(minutes=90), "photo"),
                   anchor(10000.0, START + timedelta(hours=2), "end")]

        warnings = _paced_warnings(_flat_10k(), anchors, vary_pace=False)

        assert len(warnings) == 1
        assert "photo anchor at 2.00 km (2024-01-01 09:30)" in warnings[0]
        assert "3.8× slower" in warnings[0] and "3.2× faster" in warnings[0]
        assert "start (" not in warnings[0] and "end (" not in warnings[0]

    def test_opposite_flags_without_a_shared_anchor_are_reported_separately(self):
        # 0.375 s/m overall: 1 km in 30 min (4.8x slower), 4 km at exactly the workout's pace,
        # then 5 km in 7.5 min (4.2x faster). Nothing points at a single anchor.
        anchors = [anchor(0.0, START, "start"), anchor(1000.0, START + timedelta(seconds=1800)),
                   anchor(5000.0, START + timedelta(seconds=3300)),
                   anchor(10000.0, START + timedelta(seconds=3750), "end")]

        warnings = _paced_warnings(_flat_10k(), anchors, vary_pace=False)

        assert len(warnings) == 2
        assert warnings[0].startswith("Between the start") and "slower" in warnings[0]
        assert warnings[1].startswith("Between the anchor at 5.00 km") and "faster" in warnings[1]

    def test_neighbours_off_the_same_way_are_reported_separately(self):
        # Two slow segments either side of an anchor don't point at that anchor.
        # 0.6 s/m overall: 1 km in 30 min twice is 3.0x slower each, then 8 km in 40 min is 2.0x.
        anchors = [anchor(0.0, START, "start"), anchor(1000.0, START + timedelta(minutes=30)),
                   anchor(2000.0, START + timedelta(minutes=60)), anchor(10000.0, START + timedelta(minutes=100), "end")]

        warnings = _paced_warnings(_flat_10k(), anchors, vary_pace=False)

        assert len(warnings) == 2
        assert all("slower" in w for w in warnings)

    @pytest.mark.parametrize(("first_half_seconds", "warns"), [
        (700.0, True),   # 1800 / 700 = 2.57x
        (740.0, False),  # 1800 / 740 = 2.43x
    ])
    def test_the_threshold_is_two_and_a_half_times(self, first_half_seconds, warns):
        anchors = [anchor(0.0, START), anchor(5000.0, START + timedelta(seconds=first_half_seconds)),
                   anchor(10000.0, START + timedelta(hours=1))]

        warnings = _paced_warnings(_flat_10k(), anchors, vary_pace=False)

        assert bool(warnings) is warns

    def test_a_short_segment_is_too_noisy_to_judge(self):
        # 200 m in 10 s would be 7x, but minute-rounded anchor times make a segment that short meaningless.
        anchors = [anchor(0.0, START), anchor(200.0, START + timedelta(seconds=10)),
                   anchor(10000.0, START + timedelta(hours=1))]

        assert _paced_warnings(_flat_10k(), anchors, vary_pace=False) == []

    def test_start_and_end_alone_never_warn(self):
        # As fast as the speed limit allows, and still the workout's own pace.
        anchors = [anchor(0.0, START), anchor(10000.0, START + timedelta(minutes=11))]

        assert _paced_warnings(_flat_10k(), anchors, vary_pace=False) == []

    def test_a_mode_b_stop_is_not_mistaken_for_a_slow_segment(self):
        distances = [i * 100.0 for i in range(101)]
        distances.insert(51, 5000.0)  # the stop's duplicated point
        anchors = [
            anchor(0.0, START),
            anchor(5000.0, START + timedelta(seconds=1800), source="stop_arrival"),
            anchor(5000.0, START + timedelta(seconds=1800 + 3 * 3600), source="stop_departure"),
            anchor(10000.0, START + timedelta(seconds=3600 + 3 * 3600)),
        ]

        assert _paced_warnings(flat_track(distances), anchors, vary_pace=False) == []

    def test_a_mode_a_stop_is_judged_on_moving_time(self):
        # 1 km moving in 360 s (exactly the workout's pace) plus a 3 h stop. Judged on
        # total time it would read as about 8x slower.
        distances = [i * 100.0 for i in range(101)]
        distances.insert(6, 500.0)
        stop_seconds = 3 * 3600
        anchors = [anchor(0.0, START), anchor(1000.0, START + timedelta(seconds=360 + stop_seconds)),
                   anchor(10000.0, START + timedelta(seconds=3600 + stop_seconds))]

        warnings = _paced_warnings(
            flat_track(distances), anchors, vary_pace=False,
            mode_a_stops=[ModeAStop(500.0, timedelta(seconds=stop_seconds))],
        )

        assert warnings == []

    def test_slow_climbing_is_explained_by_the_terrain(self, monkeypatch):
        # Terrain can't swing a leg past the speed bound (~2.7x), so a raw 2.5x gap can't
        # come from terrain alone. The threshold is lowered to show what is compared.
        monkeypatch.setattr(combine_module, "IMPLAUSIBLE_PACE_RATIO", 1.3)
        legs = 60
        climb = Track(points=[
            point(elevation=min(i, legs) * 12.5, distance_from_start=i * 50.0) for i in range(2 * legs + 1)
        ])  # 3 km at 25%, then 3 km flat
        end = START + timedelta(hours=1)
        modeled = combine(climb, [anchor(0.0, START, "start"), anchor(6000.0, end, "end")], SportType.RUNNING)
        midpoint_time = timestamp_of(modeled.points[legs])
        climb_seconds = (midpoint_time - START).total_seconds()
        flat_seconds = (end - midpoint_time).total_seconds()
        assert climb_seconds / flat_seconds > 2.0  # raw speeds differ far more than the threshold

        # The anchor sits where the model would put it anyway.
        anchors = [anchor(0.0, START, "start"), anchor(3000.0, midpoint_time), anchor(6000.0, end, "end")]
        warnings: list[str] = []
        combine(climb, anchors, SportType.RUNNING, warnings=warnings)
        assert warnings == []

        # The same times on flat ground: the first half 1/0.73x, the second 1.57x, the
        # workout's pace, which points at the anchor between them.
        flat = flat_track([i * 50.0 for i in range(2 * legs + 1)])
        warnings = []
        combine(flat, anchors, SportType.RUNNING, warnings=warnings)
        assert len(warnings) == 1
        assert "anchor at 3.00 km" in warnings[0]
        assert "slower" in warnings[0] and "faster" in warnings[0]

    def test_warning_never_changes_the_pacing(self):
        anchors = [anchor(0.0, START), anchor(1000.0, START + timedelta(seconds=90)),
                   anchor(10000.0, START + timedelta(hours=1))]
        silent, warned = _flat_10k(), _flat_10k()

        combine(silent, anchors, SportType.RUNNING)
        combine(warned, anchors, SportType.RUNNING, warnings=[])

        assert [p.timestamp for p in silent.points] == [p.timestamp for p in warned.points]


def _leg_speeds(points: list[TrackPoint]) -> list[float]:
    """Each leg's absolute speed in m/s from stamped points (0.0 for a zero-distance leg)."""
    return [
        (b.distance_from_start - a.distance_from_start) / seconds if seconds > 0 else 0.0
        for seconds, a, b in zip(_leg_seconds(points), points, points[1:])
    ]


def _worst_acceleration_share(points: list[TrackPoint]) -> float:
    """The largest |Δv²| between adjacent moving legs, as a share of what max acceleration 1 m/s² would allow.

    Between leg midpoints at a constant acceleration a -> |Δv²| = a * (Δs₁ + Δs₂),
    so this is the steepest acceleration on the track, in m/s².
    """
    distances = [b.distance_from_start - a.distance_from_start for a, b in zip(points, points[1:])]
    speeds = _leg_speeds(points)
    worst = 0.0
    for d0, d1, v0, v1 in zip(distances, distances[1:], speeds, speeds[1:]):
        if d0 > 0 and d1 > 0:
            worst = max(worst, abs(v1 * v1 - v0 * v0) / (d0 + d1))
    return worst


class TestCombineAccelerationLimit:
    """No two adjacent legs change speed faster than the sport's maximum acceleration."""

    LEG_M = 2.0  # gpx_reader.MAX_LEG_M; the limit can't ramp within one leg

    def _surface_step(self) -> tuple[Track, list[float]]:
        # 2 km flat, the second half on a surface 40% slower.
        count = int(2000 / self.LEG_M)
        track = flat_track([index * self.LEG_M for index in range(count + 1)])
        multipliers = [1.0 if index < count // 2 else 0.6 for index in range(count)]
        return track, multipliers

    def _crest(self) -> Track:
        # 1 km up at 12%, 1 km down at 12%: one sharp hilltop.
        count = int(2000 / self.LEG_M)
        return Track(points=[
            point(elevation=0.12 * min(d, 2000 - d), distance_from_start=d)
            for d in (index * self.LEG_M for index in range(count + 1))
        ])

    @staticmethod
    def _anchors(track: Track, seconds: float):
        return [anchor(0.0, START), anchor(track.total_distance, START + timedelta(seconds=seconds))]

    # Typical speeds per sport: ~1.3 m/s hiking, ~3 m/s running.
    SECONDS = {SportType.HIKING: 1600.0, SportType.RUNNING: 700.0}

    @pytest.mark.parametrize("sport", [SportType.HIKING, SportType.RUNNING])
    def test_a_surface_step_is_ramped_within_the_limit(self, sport):
        limit = acceleration_module.MAX_ACCELERATION_MPS2[sport]
        track, multipliers = self._surface_step()
        unlimited, _ = self._surface_step()
        anchors = self._anchors(track, self.SECONDS[sport])
        combine(unlimited, anchors, sport, multipliers=multipliers, acceleration_limit=False)
        assert _worst_acceleration_share(unlimited.points) > 2 * limit  # premise: an instant step

        combine(track, anchors, sport, multipliers=multipliers)
        assert _worst_acceleration_share(track.points) <= limit * (1 + 1e-4)

    def test_a_sharp_crest_is_ramped_within_the_limit(self):
        sport = SportType.RUNNING
        limit = acceleration_module.MAX_ACCELERATION_MPS2[sport]
        unlimited = self._crest()
        combine(unlimited, self._anchors(unlimited, self.SECONDS[sport]), sport, acceleration_limit=False)
        assert _worst_acceleration_share(unlimited.points) > 1.5 * limit  # premise

        track = self._crest()
        combine(track, self._anchors(track, self.SECONDS[sport]), sport)
        assert _worst_acceleration_share(track.points) <= limit * (1 + 1e-4)

    def test_a_hiking_crest_is_already_gentle_and_left_alone(self):
        # Tobler's response turns from climb to descent gently enough that a
        # hiker's crest is within the limit as paced, so nothing moves.
        sport = SportType.HIKING
        limited, unlimited = self._crest(), self._crest()
        combine(limited, self._anchors(limited, self.SECONDS[sport]), sport)
        combine(unlimited, self._anchors(unlimited, self.SECONDS[sport]), sport, acceleration_limit=False)
        assert [p.timestamp for p in limited.points] == [p.timestamp for p in unlimited.points]

    def test_anchors_are_still_hit_exactly(self):
        track, multipliers = self._surface_step()
        mid_time = START + timedelta(seconds=700)
        end_time = START + timedelta(seconds=1600)
        anchors = [anchor(0.0, START), anchor(1200.0, mid_time), anchor(2000.0, end_time)]

        combine(track, anchors, SportType.HIKING, multipliers=multipliers)

        by_distance = {p.distance_from_start: timestamp_of(p) for p in track.points}
        assert by_distance[1200.0] == mid_time
        assert by_distance[2000.0] == end_time
        # And the step inside the first segment was really limited.
        assert _worst_acceleration_share(track.points[:601]) <= 0.15 * (1 + 1e-4)

    def test_each_segment_is_limited_on_its_own(self):
        # An anchor is a known time: the jump in pace it implies is reported
        # (see _implausible_pace_warnings), not smoothed across.
        track = flat_track([index * 5.0 for index in range(401)])
        anchors = [anchor(0.0, START), anchor(1000.0, START + timedelta(seconds=500)),
                   anchor(2000.0, START + timedelta(seconds=1500))]
        combine(track, anchors, SportType.HIKING, vary_pace=False)
        speeds = _leg_speeds(track.points)
        assert speeds[199] == pytest.approx(2.0)
        assert speeds[200] == pytest.approx(1.0)

    def test_gentle_pace_variation_is_left_exactly_alone(self):
        def flat() -> Track:
            return Track(points=[
                point(lat=46.0 + d * 1e-5, lon=14.0, distance_from_start=float(d)) for d in range(0, 10_001, 2)
            ])

        for sport, seconds in ((SportType.HIKING, 7700.0), (SportType.RUNNING, 3300.0)):
            limited = flat()
            unlimited = flat()
            combine(limited, self._anchors(limited, seconds), sport)
            combine(unlimited, self._anchors(unlimited, seconds), sport, acceleration_limit=False)
            assert [p.timestamp for p in limited.points] == [p.timestamp for p in unlimited.points]

    def test_the_limit_is_per_sport(self):
        # A 2.5 -> 2.0 m/s step is Δv² = 2.25 over 10 m: inside running's
        # 0.30 m/s² (cap 3.0), outside hiking's 0.15 (cap 1.5).
        def step() -> tuple[Track, list[float], list]:
            t = flat_track([index * 5.0 for index in range(401)])
            multip = [1.0] * 200 + [0.8] * 200
            return t, multip, self._anchors(t, 1000 / 2.5 + 1000 / 2.0)

        results = {}
        for sport in (SportType.RUNNING, SportType.HIKING):
            for limited in (True, False):
                track, multipliers, anchors = step()
                combine(track, anchors, sport, multipliers=multipliers, vary_pace=False, acceleration_limit=limited)
                results[sport, limited] = [p.timestamp for p in track.points]

        assert results[SportType.RUNNING, True] == results[SportType.RUNNING, False]
        assert results[SportType.HIKING, True] != results[SportType.HIKING, False]

    def test_a_stop_still_starts_and_ends_instantly(self):
        track = flat_track([index * 5.0 for index in range(401)])
        track.points.insert(201, point(distance_from_start=1000.0))
        stop = ModeAStop(1000.0, timedelta(minutes=5))
        anchors = self._anchors(track, 1600.0 + 300.0)

        combine(track, anchors, SportType.HIKING, mode_a_stops=[stop], vary_pace=False)

        speeds = _leg_speeds(track.points)
        assert speeds[200] == 0.0
        assert speeds[199] == pytest.approx(speeds[0])
        assert speeds[201] == pytest.approx(speeds[0])


class TestLimitLegTimes:
    """combine._limit_leg_times on plain floats, where stamping's microsecond rounding can't hide anything."""

    distances = [2.0] * 500
    # A hiker at 1.5 m/s onto a surface 40% slower, halfway.
    seconds = [2.0 / 1.5] * 250 + [2.0 / 0.9] * 250
    limit = 0.15

    def _limited(self) -> list[float]:
        return combine_module._limit_leg_times(self.distances, self.seconds, sum(self.seconds), self.limit)

    def test_the_total_time_is_kept(self):
        assert sum(self._limited()) == pytest.approx(sum(self.seconds), rel=1e-12)

    def test_the_rescaled_speeds_are_still_within_the_limit(self):
        speeds = [d / t for d, t in zip(self.distances, self._limited())]
        for v0, v1 in zip(speeds, speeds[1:]):
            assert abs(v1 * v1 - v0 * v0) <= self.limit * 4.0 * (1 + 1e-9)

    def test_times_within_the_limit_come_back_exactly(self):
        # Arbitrary floats, where distance / (distance / t) often isn't t to the last bit.
        rng = random.Random(3)
        distances = [rng.uniform(1.0, 2.0) for _ in range(500)]
        seconds = [d / (1.3 + 0.01 * math.sin(index / 10)) * rng.uniform(0.999, 1.001) for index, d in enumerate(distances)]
        assert combine_module._limit_leg_times(distances, seconds, sum(seconds), self.limit) == seconds

    @pytest.mark.parametrize("seed", range(20))
    def test_a_rescale_that_speeds_everything_up_is_still_within_the_limit(self, seed):
        # Limiting only slows legs, so the rescale back to the anchors' time
        # speeds every leg up again, which steepens every change of speed by
        # the square of that factor. Here the limit slows a lot, so one
        # limit-then-rescale would break it by far more than the tolerance.
        rng = random.Random(seed)
        distances = [rng.uniform(0.5, 3.0) for _ in range(300)]
        seconds = [d / rng.uniform(0.5, 5.0) for d in distances]
        limited = combine_module._limit_leg_times(distances, seconds, sum(seconds), self.limit)
        assert sum(limited) == pytest.approx(sum(seconds), rel=1e-12)
        speeds = [d / t for d, t in zip(distances, limited)]
        for d0, d1, v0, v1 in zip(distances, distances[1:], speeds, speeds[1:]):
            assert abs(v1 * v1 - v0 * v0) <= self.limit * (d0 + d1) * (1 + 1e-9)

    def test_the_step_down_ends_where_the_slower_surface_begins(self):
        # Rescaling speeds every leg up by one factor, so the slow side stays
        # one flat speed and the ramp stays on the fast side.
        speeds = [d / t for d, t in zip(self.distances, self._limited())]
        assert max(speeds[250:]) == pytest.approx(min(speeds[250:]), rel=1e-12)
        assert speeds[249] > speeds[250] * (1 + 1e-6)

    def test_a_stop_leg_keeps_zero_time(self):
        distances = self.distances[:250] + [0.0] + self.distances[250:]
        seconds = self.seconds[:250] + [0.0] + self.seconds[250:]
        limited = combine_module._limit_leg_times(distances, seconds, sum(seconds), self.limit)
        assert limited[250] == 0.0
        assert sum(limited) == pytest.approx(sum(seconds), rel=1e-12)


def test_the_last_leg_does_not_absorb_the_rounding_of_the_total():
    # combine stamps the end point from its anchor, so a total that drifted
    # in limiting would land on the last leg alone as a jump in speed.
    track = flat_track([index * 2.0 for index in range(1001)])
    multipliers = [1.0] * 500 + [0.6] * 500
    anchors = [anchor(0.0, START), anchor(2000.0, START + timedelta(seconds=1600))]
    combine(track, anchors, SportType.HIKING, multipliers=multipliers, vary_pace=False)
    speeds = _leg_speeds(track.points)
    assert speeds[-1] == pytest.approx(speeds[-2], rel=1e-5)


@pytest.mark.parametrize("sport, seconds", [(SportType.HIKING, 1600.0), (SportType.RUNNING, 700.0)])
def test_a_sparse_route_has_no_sudden_jump_in_its_1hz_records(sport, seconds):
    # A planner's GPX: points 100 m apart, densified as parse_gpx_bytes does.
    # A leg must take well under the ~3 s a step's ramp lasts, or the limit
    # has nothing to ramp across and the whole step lands between two records.
    sparse = [point(lat=46.0 + d * 9e-6, lon=14.0, distance_from_start=float(d)) for d in range(0, 2001, 100)]
    track = Track(points=densify_points(sparse, MAX_LEG_M))
    multipliers = [1.0 if b.distance_from_start <= 1000 else 0.6 for b in track.points[1:]]
    anchors = [anchor(0.0, START), anchor(2000.0, START + timedelta(seconds=seconds))]

    combine(track, anchors, sport, multipliers=multipliers, vary_pace=False)

    (records,) = _resample(track.points, interval_s=1)
    per_second = [b.distance - a.distance for a, b in zip(records, records[1:])]
    worst_change = max(abs(later - earlier) for earlier, later in zip(per_second, per_second[1:]))
    # Unlimited, the step is about 0.55 m/s in one second hiking and 0.9 running.
    assert worst_change < 2 * acceleration_module.MAX_ACCELERATION_MPS2[sport]
