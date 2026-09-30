import math

import pytest

from gpx2fit.core.models import SportType, Track
from gpx2fit.core.pacing.curve_selection import (
    CURVE_FILLS,
    CURVE_REFERENCE_GRADE,
    DEFAULT_SMOOTHNESS,
    MAX_SPEED_RATIO_BOUNDS,
    TOBLER_THRESHOLDS,
    _reference_swings,
    resolve_curve_shape,
    resolve_max_speed_ratio,
    resolve_tobler_weight,
)
from gpx2fit.core.pacing.gradient import (
    MINETTI_DOWNHILL_COST_SLOPE,
    blended_speeds_from_gradients,
    calculate_gradient,
    minetti_speeds_from_gradients,
    tobler_speeds_from_gradients,
)
from tests.core.conftest import (
    flat_track,
    leg_distances,
    point,
    resolved_max_speed_ratio,
    resolved_weight,
    rolling_track,
)


class TestResolveToblerWeight:
    """Which speed curve a workout is paced on, decided once from its own slowness and steepness."""

    def _weight(self, track: Track, avg_speed_mps: float, sport: SportType) -> float:
        return resolved_weight(track, track.total_distance / avg_speed_mps, sport)

    def test_fast_run_on_flat_ground_is_pure_minetti(self):
        track = flat_track([float(d) for d in range(0, 5050, 50)])
        assert self._weight(track, avg_speed_mps=3.5, sport=SportType.RUNNING) == 0.0

    def test_slow_walk_on_flat_ground_is_pure_tobler(self):
        track = flat_track([float(d) for d in range(0, 5050, 50)])
        assert self._weight(track, avg_speed_mps=1.0, sport=SportType.HIKING) == 1.0

    def test_steep_terrain_reaches_tobler_even_when_the_activity_is_fast(self):
        # The verticality criterion has to fire on its own: an alpine route
        # ground out at a respectable speed is still being power-hiked, and
        # Minetti's shape is wrong for it either way.
        track = rolling_track(distance=5000.0, climb_per_km=200.0)
        assert self._weight(track, avg_speed_mps=3.0, sport=SportType.RUNNING) == 1.0

    def test_dem_rounded_flat_road_is_not_mistaken_for_climbing(self):
        # Regression for deriving climb from the smoothed gradients rather
        # than from raw point-to-point deltas: this road is dead flat, but
        # whole-metre DEM rounding gives it ±1 m of jitter every 10 m, which
        # Track.total_elevation_gain reads as hundreds of metres of climb.
        track = Track(points=[
            point(elevation=float(index % 2), distance_from_start=float(index * 10))
            for index in range(1001)
        ])
        assert track.total_elevation_gain > 400.0  # premise: the raw deltas really are that bad
        assert self._weight(track, avg_speed_mps=3.5, sport=SportType.RUNNING) == 0.0

    def test_weight_rises_smoothly_as_the_workout_slows(self):
        track = flat_track([float(d) for d in range(0, 5050, 50)])
        speeds = [3.0 - step * 0.1 for step in range(21)]
        weights = [self._weight(track, speed, SportType.RUNNING) for speed in speeds]

        assert weights[0] == 0.0 and weights[-1] == 1.0
        assert all(later >= earlier for earlier, later in zip(weights, weights[1:]))
        # Smooth, not a step: the band is crossed in several distinct values,
        # so a second either way can't flip the whole activity's character.
        assert len({round(w, 3) for w in weights if 0.0 < w < 1.0}) >= 5

    def test_weight_rises_as_the_terrain_steepens(self):
        # Held at a pace brisk enough that the speed criterion contributes
        # nothing, so this isolates the verticality one.
        weights = [
            self._weight(rolling_track(distance=5000.0, climb_per_km=climb), 3.0, SportType.RUNNING)
            for climb in (0.0, 30.0, 60.0, 80.0, 120.0)
        ]
        assert weights[0] == 0.0 and weights[-1] == 1.0
        assert all(later >= earlier for earlier, later in zip(weights, weights[1:]))

    def test_the_declared_sport_biases_the_same_borderline_workout(self):
        # Running needs a stronger signal than hiking before it walks.
        track = flat_track([float(d) for d in range(0, 5050, 50)])
        borderline_speed = TOBLER_THRESHOLDS[SportType.RUNNING].flat_equivalent_mps

        # 0.5 is structural: the ramp is centred on the threshold. How far
        # hiking sits above it depends on the gap between the two sports'
        # (uncalibrated) thresholds, so only its direction is pinned.
        assert self._weight(track, borderline_speed, SportType.RUNNING) == pytest.approx(0.5)
        assert self._weight(track, borderline_speed, SportType.HIKING) > 0.5

    @pytest.mark.parametrize("sport, expected", [(SportType.RUNNING, 0.0), (SportType.HIKING, 1.0)])
    def test_degenerate_track_falls_back_to_the_sports_own_curve(self, sport, expected):
        assert resolve_tobler_weight([], [], 0.0, sport) == expected
        assert resolve_tobler_weight([0.0], [0.0], 600.0, sport) == expected
        assert resolve_tobler_weight([0.0], [100.0], 0.0, sport) == expected


class TestResolveMaxSpeedRatio:
    """How far a workout's leg speeds may swing, decided once from its sport and steepness."""

    @pytest.mark.parametrize("sport", list(SportType))
    def test_flat_track_gets_the_sports_flat_bound(self, sport):
        track = flat_track([float(d) for d in range(0, 5050, 50)])
        assert resolved_max_speed_ratio(track, sport) == MAX_SPEED_RATIO_BOUNDS[sport].flat

    @pytest.mark.parametrize("sport", list(SportType))
    def test_steep_track_gets_the_sports_hilly_bound(self, sport):
        track = rolling_track(distance=5000.0, climb_per_km=200.0)
        assert resolved_max_speed_ratio(track, sport) == pytest.approx(MAX_SPEED_RATIO_BOUNDS[sport].hilly)

    def test_dem_rounded_flat_road_is_not_mistaken_for_hilly(self):
        track = Track(points=[
            point(elevation=float(index % 2), distance_from_start=float(index * 10))
            for index in range(1001)
        ])
        assert resolved_max_speed_ratio(track, SportType.RUNNING) == MAX_SPEED_RATIO_BOUNDS[SportType.RUNNING].flat

    def test_ratio_widens_smoothly_as_the_terrain_steepens(self):
        ratios = [
            resolved_max_speed_ratio(rolling_track(distance=5000.0, climb_per_km=climb), SportType.RUNNING)
            for climb in (0.0, 15.0, 30.0, 45.0, 60.0, 80.0, 120.0)
        ]
        assert all(later >= earlier for earlier, later in zip(ratios, ratios[1:]))
        assert len({round(r, 3) for r in ratios}) >= 4

    @pytest.mark.parametrize("climb_per_km", [0.0, 40.0, 200.0])
    def test_hiking_swings_no_more_than_running_on_the_same_track(self, climb_per_km):
        track = rolling_track(distance=5000.0, climb_per_km=climb_per_km)
        assert resolved_max_speed_ratio(track, SportType.HIKING) <= resolved_max_speed_ratio(track, SportType.RUNNING)

    @pytest.mark.parametrize("sport", list(SportType))
    def test_bounds_are_usable_and_hills_swing_at_least_as_much_as_flats(self, sport):
        # _compress_speed_toward_typical needs > 1, and the model (and the tuning
        # harness's fit) assumes hilly >= flat. No upper cap here: the harness
        # searches ratios up to 4.0, and whether a wide bound still paces
        # realistically is TestResolveCurveShape.test_resolved_curves_stay_sane's job.
        bounds = MAX_SPEED_RATIO_BOUNDS[sport]
        assert 1.0 < bounds.flat <= bounds.hilly

    @pytest.mark.parametrize("sport", list(SportType))
    def test_degenerate_track_falls_back_to_the_flat_bound(self, sport):
        assert resolve_max_speed_ratio([], [], sport) == MAX_SPEED_RATIO_BOUNDS[sport].flat
        assert resolve_max_speed_ratio([0.0], [0.0], sport) == MAX_SPEED_RATIO_BOUNDS[sport].flat


class TestSmoothness:
    """The user's 1–10 smoothness level, applied on top of the automatic, terrain-resolved ratio."""

    LEVELS = range(1, 11)
    FLAT = flat_track([float(d) for d in range(0, 5050, 50)])
    HILLY = rolling_track(distance=5000.0, climb_per_km=200.0)

    def _ratio(self, track: Track, sport: SportType, smoothness: int | None = None) -> float:
        gradients = calculate_gradient(track)
        if smoothness is None:
            return resolve_max_speed_ratio(gradients, leg_distances(track), sport)
        return resolve_max_speed_ratio(gradients, leg_distances(track), sport, smoothness=smoothness)

    @pytest.mark.parametrize("sport", list(SportType))
    def test_the_default_level_is_the_automatic_ratio(self, sport):
        for track in (self.FLAT, self.HILLY):
            assert self._ratio(track, sport, smoothness=DEFAULT_SMOOTHNESS) == self._ratio(track, sport)

    def test_the_default_level_sits_inside_the_scale_not_at_an_end(self):
        # Otherwise the slider could only ever smooth, or only ever roughen.
        assert min(self.LEVELS) < DEFAULT_SMOOTHNESS < max(self.LEVELS)

    @pytest.mark.parametrize("sport", list(SportType))
    def test_higher_levels_strictly_narrow_the_ratio(self, sport):
        for track in (self.FLAT, self.HILLY):
            ratios = [self._ratio(track, sport, level) for level in self.LEVELS]
            assert all(later < earlier for earlier, later in zip(ratios, ratios[1:]))

    @pytest.mark.parametrize("sport", list(SportType))
    def test_every_level_keeps_a_usable_bound(self, sport):
        # _compress_speed_toward_typical divides by log(max_ratio), so the bound must stay above 1.
        smoothest = self._ratio(self.FLAT, sport, max(self.LEVELS))
        assert smoothest > 1.0
        # ...and the smoothest level must actually be close to even pacing,
        # not a token change: under a 20% swing on flat ground.
        assert smoothest < 1.2

    def test_the_roughest_level_stays_within_reason(self):
        # The top of the scale is still meant to look like a real activity.
        assert self._ratio(self.HILLY, SportType.RUNNING, min(self.LEVELS)) < 4.0

    @pytest.mark.parametrize("level", LEVELS)
    def test_terrain_and_sport_adaptation_survive_at_every_level(self, level):
        # The slider is relative to the automatic ratio, not an absolute one:
        # a hilly route still swings more than a flat one, and a run no less
        # than a hike, whatever level the user picks.
        for sport in SportType:
            assert self._ratio(self.HILLY, sport, level) > self._ratio(self.FLAT, sport, level)
        for track in (self.FLAT, self.HILLY):
            assert self._ratio(track, SportType.RUNNING, level) >= self._ratio(track, SportType.HIKING, level)

    @pytest.mark.parametrize("level", [0, 11, -1, 5.5])
    def test_a_level_outside_the_scale_raises(self, level):
        with pytest.raises(ValueError):
            self._ratio(self.HILLY, SportType.RUNNING, level)


class TestResolveCurveShape:
    """Both curves' exponents, fitted so the curves use a fixed share of the speed-swing bound."""

    RATIOS = [1.8, 2.0, 2.6, 2.8]

    @pytest.mark.parametrize("sport", list(SportType))
    @pytest.mark.parametrize("ratio", RATIOS)
    @pytest.mark.parametrize("tobler_weight", [0.0, 1.0])
    def test_largest_swing_within_the_reference_grade_is_its_fill_share_of_the_bound(self, sport, ratio, tobler_weight):
        shape = resolve_curve_shape(ratio, sport)
        fills = CURVE_FILLS[sport]

        def largest_swing(grades: list[float]) -> float:
            return max(abs(math.log(s)) for s in blended_speeds_from_gradients(grades, tobler_weight, shape))

        assert largest_swing(_up_to(CURVE_REFERENCE_GRADE)) == pytest.approx(fills.uphill * math.log(ratio))
        assert largest_swing(_up_to(-CURVE_REFERENCE_GRADE)) == pytest.approx(fills.downhill * math.log(ratio))

    def test_the_sports_differ_only_in_how_much_of_the_bound_they_use(self):
        # Same curves, same bound: each exponent is just its sport's fill share
        # scaled, so the two sports' exponents stand in the ratio of their fills.
        running = resolve_curve_shape(2.4, SportType.RUNNING)
        hiking = resolve_curve_shape(2.4, SportType.HIKING)
        run_fills, hike_fills = CURVE_FILLS[SportType.RUNNING], CURVE_FILLS[SportType.HIKING]
        for curve in ("minetti", "tobler"):
            ran, hiked = getattr(running, curve), getattr(hiking, curve)
            assert ran.uphill / hiked.uphill == pytest.approx(run_fills.uphill / hike_fills.uphill)
            assert ran.downhill / hiked.downhill == pytest.approx(run_fills.downhill / hike_fills.downhill)

    @pytest.mark.parametrize("sport", list(SportType))
    def test_curve_narrows_together_with_the_bound(self, sport):
        # The point of the relation: a tighter bound flattens the curve itself,
        # so its share of the bound — and so how hard tanh squashes it — stays put.
        gradients = [-0.3, -0.15, 0.15, 0.3, 0.4]
        shares = [
            [abs(math.log(s)) / math.log(ratio) for s in blended_speeds_from_gradients(gradients, 0.5, resolve_curve_shape(ratio, sport))]
            for ratio in self.RATIOS
        ]
        for share in shares[1:]:
            assert share == pytest.approx(shares[0])

    @pytest.mark.parametrize("sport", list(SportType))
    def test_steep_climbs_are_not_pinned_to_a_plateau(self, sport):
        # A 40% climb, well past the reference grade, must still sit where tanh
        # has a real slope (d tanh(x)/dx = 1 - tanh(x)²), so a long steep climb
        # keeps following the terrain rather than flattening against the bound.
        # Running's fitted uphill fill (0.85) leaves a 40% climb at a slope of
        # about 0.23 on Tobler, so the floor sits just under that.
        for ratio in self.RATIOS:
            shape = resolve_curve_shape(ratio, sport)
            for weight in (0.0, 1.0):
                [steep] = blended_speeds_from_gradients([0.4], weight, shape)
                share = abs(math.log(steep)) / math.log(ratio)
                assert 1 - math.tanh(share) ** 2 > 0.2

    # Every curve combine() can actually pace on at the default smoothness: each
    # sport at both ends of its bound, on either end of the Minetti/Tobler blend.
    PRODUCTION_SHAPES = [
        pytest.param(sport, getattr(MAX_SPEED_RATIO_BOUNDS[sport], end), weight, id=f"{sport.name}-{end}-w{weight}")
        for sport in SportType
        for end in ("flat", "hilly")
        for weight in (0.0, 1.0)
    ]

    @pytest.mark.parametrize("sport, ratio, tobler_weight", PRODUCTION_SHAPES)
    def test_resolved_curves_stay_sane(self, sport, ratio, tobler_weight):
        # Sanity bands on the output, not on any constant. The fills and bounds
        # are there to be retuned, and the tuning harness, not this test, is what
        # judges realism, so the bands are wide enough to admit every fill it
        # has proposed so far (uphill 0.30 to 0.95, which put a +20% climb
        # anywhere from 0.87x down to 0.44x; running's downhill 0.79, which puts
        # a -20% descent at 0.57x, as slow as the corpus's one runner). They
        # catch a broken curve: one that goes the wrong way, collapses to even
        # pacing, or runs away.
        climb_10, climb_20, climb_30, descent_10, descent_20 = blended_speeds_from_gradients(
            [0.1, 0.2, 0.3, -0.1, -0.2], tobler_weight, resolve_curve_shape(ratio, sport)
        )
        assert 1.0 > climb_10 > climb_20 > climb_30
        assert 0.4 <= climb_20 <= 0.9
        assert climb_30 >= 0.25
        assert max(descent_10, descent_20) <= 1.5
        assert descent_20 >= 0.5

    @pytest.mark.parametrize("sport", list(SportType))
    def test_hilly_bound_never_gives_a_flatter_curve_than_the_flat_bound(self, sport):
        bounds = MAX_SPEED_RATIO_BOUNDS[sport]
        gradients = [0.1, 0.2, -0.2]
        for weight in (0.0, 1.0):
            flat = blended_speeds_from_gradients(gradients, weight, resolve_curve_shape(bounds.flat, sport))
            hilly = blended_speeds_from_gradients(gradients, weight, resolve_curve_shape(bounds.hilly, sport))
            for on_flat, on_hilly in zip(flat, hilly):
                assert abs(math.log(on_hilly)) >= abs(math.log(on_flat))

    def test_tobler_is_softened_rather_than_raw(self):
        shape = resolve_curve_shape(MAX_SPEED_RATIO_BOUNDS[SportType.HIKING].hilly, SportType.HIKING)
        assert shape.tobler.uphill < 1.0 and shape.tobler.downhill < 1.0


def _up_to(grade: float, samples: int = 1000) -> list[float]:
    """Grades evenly spaced from flat to `grade`, both ends included."""
    return [grade * index / samples for index in range(samples + 1)]


def _raw_minetti_at(slope: float):
    """gradients -> raw Minetti speeds with this descent cost slope."""
    return lambda grades: minetti_speeds_from_gradients(grades, 1.0, 1.0, downhill_cost_slope=slope)


def _slope_back_at_flat_on_the_reference_grade() -> float:
    """The descent cost slope at which raw Minetti is exactly flat speed at -CURVE_REFERENCE_GRADE.

    Found by bisection: a steeper slope only ever slows descents.
    """
    low, high = 0.0, 40.0
    for _ in range(60):
        middle = (low + high) / 2
        [speed] = _raw_minetti_at(middle)([-CURVE_REFERENCE_GRADE])
        low, high = (middle, high) if speed > 1.0 else (low, middle)
    return (low + high) / 2


class TestReferenceSwings:
    """How far a raw curve swings from flat speed, which resolve_curve_shape divides each fill by."""

    @pytest.mark.parametrize("raw_speeds_of", [_raw_minetti_at(MINETTI_DOWNHILL_COST_SLOPE), tobler_speeds_from_gradients])
    def test_shipped_curves_are_measured_at_the_reference_grade(self, raw_speeds_of):
        up, down = raw_speeds_of([CURVE_REFERENCE_GRADE, -CURVE_REFERENCE_GRADE])
        assert _reference_swings(raw_speeds_of, CURVE_REFERENCE_GRADE) == pytest.approx(
            (abs(math.log(up)), abs(math.log(down)))
        )

    def test_a_curve_back_at_flat_speed_on_the_reference_grade_is_measured_at_its_peak(self):
        raw_speeds_of = _raw_minetti_at(_slope_back_at_flat_on_the_reference_grade())
        [at_reference] = raw_speeds_of([-CURVE_REFERENCE_GRADE])
        assert at_reference == pytest.approx(1.0)  # premise: no swing at the reference grade itself

        peak = max(abs(math.log(s)) for s in raw_speeds_of(_up_to(-CURVE_REFERENCE_GRADE)))
        _, downhill_swing = _reference_swings(raw_speeds_of, CURVE_REFERENCE_GRADE)
        assert downhill_swing == pytest.approx(peak, rel=0.01)

    @pytest.mark.parametrize("slope", [
        0.0,
        pytest.param(_slope_back_at_flat_on_the_reference_grade(), id="back-at-flat"),
        10.0,
        MINETTI_DOWNHILL_COST_SLOPE,
        40.0,
    ])
    def test_softened_descents_stay_within_their_fill_share_at_any_descent_cost_slope(self, slope):
        # What resolve_curve_shape does with the swing, for a Minetti curve of any shape.
        log_limit = math.log(MAX_SPEED_RATIO_BOUNDS[SportType.RUNNING].hilly)
        downhill_fill = CURVE_FILLS[SportType.RUNNING].downhill
        _, downhill_swing = _reference_swings(_raw_minetti_at(slope), CURVE_REFERENCE_GRADE)
        exponent = downhill_fill * log_limit / downhill_swing

        softened = minetti_speeds_from_gradients(
            _up_to(-CURVE_REFERENCE_GRADE), 1.0, exponent, downhill_cost_slope=slope
        )
        largest = max(abs(math.log(s)) for s in softened)
        assert largest == pytest.approx(downhill_fill * log_limit, rel=0.01)
