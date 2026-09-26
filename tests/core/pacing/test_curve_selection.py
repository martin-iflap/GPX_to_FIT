import math

import pytest

from gpx2fit.core.models import SportType, Track
from gpx2fit.core.pacing.curve_selection import (
    CURVE_REFERENCE_GRADE,
    DEFAULT_SMOOTHNESS,
    DOWNHILL_FILL,
    MAX_SPEED_RATIO_BOUNDS,
    TOBLER_THRESHOLDS,
    UPHILL_FILL,
    resolve_curve_shape,
    resolve_max_speed_ratio,
    resolve_tobler_weight,
)
from gpx2fit.core.pacing.gradient import (
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

        assert self._weight(track, borderline_speed, SportType.RUNNING) == pytest.approx(0.5)
        assert self._weight(track, borderline_speed, SportType.HIKING) == 1.0

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
    def test_bounds_stay_within_a_sane_range(self, sport):
        # _compress_speed_toward_typical needs > 1; much above ~2.8 lets pace swing unrealistically.
        bounds = MAX_SPEED_RATIO_BOUNDS[sport]
        assert 1.0 < bounds.flat <= bounds.hilly <= 2.8

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

    @pytest.mark.parametrize("ratio", RATIOS)
    @pytest.mark.parametrize("tobler_weight", [0.0, 1.0])
    def test_reference_grade_lands_on_its_fill_share_of_the_bound(self, ratio, tobler_weight):
        up, down = blended_speeds_from_gradients(
            [CURVE_REFERENCE_GRADE, -CURVE_REFERENCE_GRADE], tobler_weight, resolve_curve_shape(ratio)
        )
        assert abs(math.log(up)) == pytest.approx(UPHILL_FILL * math.log(ratio))
        assert abs(math.log(down)) == pytest.approx(DOWNHILL_FILL * math.log(ratio))

    def test_curve_narrows_together_with_the_bound(self):
        # The point of the relation: a tighter bound flattens the curve itself,
        # so its share of the bound — and so how hard tanh squashes it — stays put.
        gradients = [-0.3, -0.15, 0.15, 0.3, 0.4]
        shares = [
            [abs(math.log(s)) / math.log(ratio) for s in blended_speeds_from_gradients(gradients, 0.5, resolve_curve_shape(ratio))]
            for ratio in self.RATIOS
        ]
        for share in shares[1:]:
            assert share == pytest.approx(shares[0])

    def test_steep_climbs_are_not_pinned_to_a_plateau(self):
        # A 40% climb, well past the reference grade, must still sit where tanh
        # has a real slope (d tanh(x)/dx = 1 - tanh(x)²), so a long steep climb
        # keeps following the terrain rather than flattening against the bound.
        for ratio in self.RATIOS:
            shape = resolve_curve_shape(ratio)
            for weight in (0.0, 1.0):
                [steep] = blended_speeds_from_gradients([0.4], weight, shape)
                share = abs(math.log(steep)) / math.log(ratio)
                assert 1 - math.tanh(share) ** 2 > 0.35

    def test_running_at_its_hilly_bound_keeps_roughly_the_old_minetti_uphill_exponent(self):
        # Only uphill: the downhill exponent moved on purpose when the descent
        # cost term changed Minetti's shape below 0%.
        shape = resolve_curve_shape(MAX_SPEED_RATIO_BOUNDS[SportType.RUNNING].hilly) # todo: we probably don't want this hardcoded test.
        assert shape.minetti.uphill == pytest.approx(0.6, abs=0.05)

    @pytest.mark.parametrize("speeds_fn", [minetti_speeds_from_gradients, tobler_speeds_from_gradients])
    def test_descent_reference_grade_is_clearly_off_flat_speed(self, speeds_fn):
        # The downhill exponent divides by |log(raw speed at -CURVE_REFERENCE_GRADE)|.
        # Neither curve is monotonic downhill: both peak on a gentle descent and
        # cross back through flat speed further down. If a curve change moved
        # that crossing near the reference grade, the exponent would blow up.
        [down] = speeds_fn([-CURVE_REFERENCE_GRADE], uphill_exponent=1.0, downhill_exponent=1.0)
        assert abs(math.log(down)) > 0.15

    def test_tobler_is_softened_rather_than_raw(self):
        shape = resolve_curve_shape(MAX_SPEED_RATIO_BOUNDS[SportType.HIKING].hilly)
        assert shape.tobler.uphill < 1.0 and shape.tobler.downhill < 1.0
