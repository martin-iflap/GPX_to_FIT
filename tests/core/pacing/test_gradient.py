import math
import pytest
from gpx2fit.core.models import Track
from gpx2fit.core.pacing.gradient import (
    GRADIENT_WINDOW_M,
    blended_speeds_from_gradients,
    calculate_gradient,
    minetti_speeds_from_gradients,
    tobler_speeds_from_gradients,
)
from tests.core.conftest import point


def _track(legs: list[tuple[float, float]]) -> Track:
    """Build a Track from (elevation, distance_from_start) pairs, one per point."""
    return Track(points=[point(elevation=ele, distance_from_start=dist) for ele, dist in legs])


def _minetti_cost(gradient: float) -> float:
    """Minetti et al. (2002) energy-cost polynomial, computed independently of the implementation."""
    return (
        155.4 * gradient ** 5
        - 30.4 * gradient ** 4
        - 43.3 * gradient ** 3
        + 46.3 * gradient ** 2
        + 19.5 * gradient
        + 3.6
    )


def _tobler_speed_kmh(gradient: float) -> float:
    """Tobler's (1993) hiking function, computed independently of the implementation."""
    return 6.0 * math.exp(-3.5 * abs(gradient + 0.05))


def _tobler_relative(gradient: float) -> float:
    """Tobler's speed as a multiple of its own flat-ground speed, computed independently."""
    return _tobler_speed_kmh(gradient) / _tobler_speed_kmh(0.0)


def _raw_minetti(gradients: list[float]) -> list[float]:
    """Un-softened Minetti speeds (both exponents 1.0) — the pure inverted-cost curve."""
    return minetti_speeds_from_gradients(gradients, uphill_exponent=1.0, downhill_exponent=1.0)


def _relative_to_flat(speeds_fn, gradient: float) -> float:
    """Speed at `gradient` as a multiple of the same model's flat-ground speed."""
    [at_gradient, flat] = speeds_fn([gradient, 0.0])
    return at_gradient / flat


class TestCalculateGradient:
    def test_flat_track_has_zero_gradient(self):
        track = _track([(100.0, 0.0), (100.0, 50.0), (100.0, 100.0)])
        assert calculate_gradient(track) == [0.0, 0.0]

    def test_returns_one_gradient_per_leg(self):
        track = _track([(0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0)])
        assert len(calculate_gradient(track)) == 3

    def test_single_point_track_has_no_gradients(self):
        assert calculate_gradient(_track([(100.0, 0.0)])) == []

    def test_uphill_and_downhill_gradients_match_rise_over_run(self):
        # +10m over 100m, then -5m over 100m.
        track = _track([(0.0, 0.0), (10.0, 100.0), (5.0, 200.0)])
        gradients = calculate_gradient(track, window_m=0.0)
        assert gradients[0] == pytest.approx(0.1)
        assert gradients[1] == pytest.approx(-0.05)

    def test_zero_distance_leg_does_not_divide_by_zero(self):
        # Two points at the same distance_from_start but different elevation.
        track = _track([(0.0, 0.0), (50.0, 0.0), (50.0, 100.0)])
        gradients = calculate_gradient(track)
        assert gradients[0] == 0.0

    def test_negative_window_raises(self):
        with pytest.raises(ValueError):
            calculate_gradient(_track([(0.0, 0.0), (1.0, 10.0)]), window_m=-1.0)


class TestGradientSmoothing:
    def test_zero_window_gives_raw_per_leg_rise_over_run(self):
        track = _track([(0.0, 0.0), (1.0, 10.0), (1.0, 20.0), (3.0, 30.0)])
        assert calculate_gradient(track, window_m=0.0) == pytest.approx([0.1, 0.0, 0.2])

    def test_dem_quantization_steps_are_smoothed_into_the_underlying_slope(self):
        # A uniform 3% slope sampled every 5 m, with elevation rounded to
        # whole metres the way DEM-derived GPX exports usually are. Per-leg
        # rise/run turns that into runs of exactly-0% legs broken up by
        # single 20% "steps" — the source of both perfectly flat pace
        # stretches and sudden pace spikes.
        true_slope = 0.03
        track = _track([(float(round(true_slope * d)), float(d)) for d in range(0, 605, 5)])

        raw = calculate_gradient(track, window_m=0.0)
        assert min(raw) == 0.0 and max(raw) >= 0.2  # premise: the raw gradient really is spiky

        smoothed = calculate_gradient(track)
        half_window = GRADIENT_WINDOW_M / 2
        interior = [
            g for g, a, b in zip(smoothed, track.points, track.points[1:])
            if a.distance_from_start >= half_window and b.distance_from_start <= track.total_distance - half_window
        ]
        assert interior
        # Rounding moves each window end by at most 0.5 m, so the windowed
        # gradient can be off from the true slope by at most 1 m / window.
        max_error = 1.0 / GRADIENT_WINDOW_M
        assert all(abs(g - true_slope) <= max_error + 1e-9 for g in interior)

    def test_sustained_climb_keeps_its_full_gradient_away_from_its_edges(self):
        # Smoothing must only remove noise shorter than the window, not
        # flatten a real climb that's much longer than it.
        def elevation(d: float) -> float:
            return min(max(d - 300.0, 0.0), 400.0) * 0.15

        track = _track([(elevation(d), float(d)) for d in range(0, 1010, 10)])
        gradients = calculate_gradient(track)
        half_window = GRADIENT_WINDOW_M / 2

        for g, a, b in zip(gradients, track.points, track.points[1:]):
            if a.distance_from_start >= 300.0 + half_window and b.distance_from_start <= 700.0 - half_window:
                assert g == pytest.approx(0.15)
            if b.distance_from_start <= 300.0 - half_window or a.distance_from_start >= 700.0 + half_window:
                assert g == pytest.approx(0.0)

    def test_leg_longer_than_the_window_keeps_its_own_gradient(self):
        # A long straight leg (common on planner routes) must not be blended
        # with its neighbors' terrain: its window already lies entirely
        # inside it.
        track = _track([(0.0, 0.0), (10.0, 500.0), (10.0, 1000.0), (30.0, 1010.0)])
        gradients = calculate_gradient(track)
        assert gradients[0] == pytest.approx(0.02)

    def test_window_is_truncated_at_the_track_ends_not_padded(self):
        # A uniform 5% slope: the first and last legs have less than half a
        # window of route on one side, and must still read 5%, not be
        # diluted by some imaginary flat ground beyond the route.
        track = _track([(0.05 * d, float(d)) for d in range(0, 210, 10)])
        gradients = calculate_gradient(track)
        assert gradients[0] == pytest.approx(0.05)
        assert gradients[-1] == pytest.approx(0.05)

    def test_duplicated_stop_point_does_not_distort_neighbouring_gradients(self):
        # pacing.stops duplicates a point (same distance and elevation) to
        # represent a stop; that must not read as a step in the terrain.
        distances = [0.0, 50.0, 100.0, 100.0, 150.0, 200.0]
        track = _track([(0.05 * d, d) for d in distances])
        gradients = calculate_gradient(track)
        assert gradients[2] == 0.0  # the zero-distance stop leg itself
        for index in (0, 1, 3, 4):
            assert gradients[index] == pytest.approx(0.05)


class TestMinettiRawCurve:
    """With both exponents at 1.0, the model is the pure inverted Minetti cost curve."""

    def test_flat_ground_is_the_models_unit_speed(self):
        assert _raw_minetti([0.0]) == pytest.approx([1.0])

    def test_matches_inverted_cost_formula_on_moderate_uphill_and_downhill(self):
        assert _raw_minetti([0.1, -0.1]) == pytest.approx([
            _minetti_cost(0.0) / _minetti_cost(0.1),
            _minetti_cost(0.0) / _minetti_cost(-0.1),
        ])

    def test_gradient_beyond_calibrated_domain_uses_scaled_tobler_not_a_clamp(self):
        # A 100% grade descent (-1.0) is far outside Minetti's calibrated
        # [-0.45, 0.45] domain (and would drive the un-clamped polynomial's
        # cost negative), so it falls back to Tobler's curve instead, scaled
        # to match Minetti's own speed at -0.45 so the transition is continuous.
        assert _minetti_cost(-1.0) <= 0  # sanity-check the premise of this test
        [speed] = _raw_minetti([-1.0])
        boundary_speed = _minetti_cost(0.0) / _minetti_cost(-0.45)
        assert speed == pytest.approx(boundary_speed * _tobler_speed_kmh(-1.0) / _tobler_speed_kmh(-0.45))


class TestModelsShareAFlatGroundReference:
    """Both curves report speed as a multiple of their own flat-ground speed.

    combine.py rescales whichever model it picks to the segment's own
    duration, so this shared reference has no effect on output — it's what
    makes a raw speed value readable (0.68 == 68% of flat pace) and lets the
    two models be compared directly while tuning.
    """

    def test_minetti_is_one_on_flat_ground(self):
        assert minetti_speeds_from_gradients([0.0]) == pytest.approx([1.0])

    def test_tobler_is_one_on_flat_ground(self):
        assert tobler_speeds_from_gradients([0.0]) == pytest.approx([1.0])


class TestMinettiDefaultCurve:
    @pytest.mark.parametrize("boundary", [0.45, -0.45])
    def test_speed_is_continuous_at_the_domain_boundary(self, boundary):
        just_beyond = boundary * 1.002
        [at_boundary_speed, just_beyond_speed] = minetti_speeds_from_gradients([boundary, just_beyond])
        assert just_beyond_speed == pytest.approx(at_boundary_speed, rel=1e-2)

    def test_uphill_speed_keeps_decreasing_as_the_climb_steepens_even_beyond_the_domain(self):
        # Unlike a clamp (which would pin every leg steeper than the
        # boundary to one identical speed — a flat/constant pace stretch),
        # speed keeps decreasing all the way up.
        gradients = [0.0, 0.05, 0.1, 0.2, 0.3, 0.44, 0.46, 0.6, 0.9]
        speeds = minetti_speeds_from_gradients(gradients)
        assert all(later < earlier for earlier, later in zip(speeds, speeds[1:]))

    def test_very_steep_descent_is_slower_than_a_moderate_one(self):
        [moderate, very_steep, steeper] = minetti_speeds_from_gradients([-0.2, -0.6, -0.9])
        assert very_steep < moderate
        assert steeper < very_steep


class TestMinettiSoftening:
    """The default curve is Minetti's shape with a softened amplitude.

    Raw Minetti (constant metabolic power) predicts a 20% descent at twice
    flat speed and a 20% climb at 0.4x — a spread wide enough that, once
    scaled to a slow real-world pace, climbs cross Strava's "resting"
    threshold. The softening narrows that spread, but must not flatten it
    into near-constant pace either.
    """

    GRADIENTS = [-0.4, -0.3, -0.2, -0.1, -0.05, 0.05, 0.1, 0.2, 0.3, 0.4]

    def test_flat_ground_speed_is_unchanged_by_softening(self):
        assert minetti_speeds_from_gradients([0.0]) == pytest.approx(_raw_minetti([0.0]))

    @pytest.mark.parametrize("gradient", GRADIENTS)
    def test_softened_speed_lies_strictly_between_flat_and_raw_minetti(self, gradient):
        soft = _relative_to_flat(minetti_speeds_from_gradients, gradient)
        raw = _relative_to_flat(_raw_minetti, gradient)
        assert raw != pytest.approx(1.0)  # premise: raw Minetti actually deviates from flat here
        assert min(raw, 1.0) < soft < max(raw, 1.0)

    def test_climbs_are_still_clearly_slower_than_flat(self):
        # Guards against over-smoothing: realistic running pace drops
        # substantially on climbs.
        assert _relative_to_flat(minetti_speeds_from_gradients, 0.1) <= 0.8
        assert _relative_to_flat(minetti_speeds_from_gradients, 0.2) <= 0.6
        assert _relative_to_flat(minetti_speeds_from_gradients, 0.3) <= 0.5

    def test_moderate_descents_are_still_clearly_faster_than_flat(self):
        assert _relative_to_flat(minetti_speeds_from_gradients, -0.1) >= 1.15

    def test_descent_speed_gain_is_well_below_raw_minettis_doubling(self):
        peak = max(_relative_to_flat(minetti_speeds_from_gradients, -g / 100) for g in range(0, 46))
        assert peak <= 1.6

    def test_descents_are_softened_more_than_climbs(self):
        # Downhill pace is limited by footing and braking rather than
        # metabolic cost, so the energy model overstates descent speed more
        # than it overstates climb slowdown.
        def retained_share(gradient: float) -> float:
            soft = _relative_to_flat(minetti_speeds_from_gradients, gradient)
            raw = _relative_to_flat(_raw_minetti, gradient)
            return math.log(soft) / math.log(raw)

        assert retained_share(-0.1) < retained_share(0.1)
        assert retained_share(-0.2) < retained_share(0.2)

    def test_exponents_control_how_strongly_speed_reacts_to_gradient(self):
        # The knob a future model selector can turn: a lower exponent means
        # a flatter pace profile, for the same terrain.
        [gentle_response] = minetti_speeds_from_gradients([0.2], uphill_exponent=0.5)
        [strong_response] = minetti_speeds_from_gradients([0.2], uphill_exponent=0.9)
        assert strong_response < gentle_response

    @pytest.mark.parametrize("kwargs", [{"uphill_exponent": 0.0}, {"downhill_exponent": -0.5}])
    def test_non_positive_exponent_raises(self, kwargs):
        with pytest.raises(ValueError):
            minetti_speeds_from_gradients([0.1], **kwargs)


class TestToblerSpeeds:
    def test_matches_hiking_function_on_uphill_and_downhill(self):
        # Normalization is a constant factor, so the published curve's shape
        # must survive it exactly.
        assert tobler_speeds_from_gradients([0.15, -0.15]) == pytest.approx(
            [_tobler_relative(0.15), _tobler_relative(-0.15)]
        )

    def test_peak_speed_is_on_a_gentle_downhill_not_flat_ground(self):
        # Tobler's function peaks at gradient == -0.05, not 0.
        [flat_speed, gentle_downhill_speed] = tobler_speeds_from_gradients([0.0, -0.05])
        assert gentle_downhill_speed > flat_speed


class TestBlendedSpeeds:
    GRADIENTS = [-0.25, -0.1, 0.0, 0.1, 0.25]

    def test_weight_zero_is_exactly_minetti(self):
        assert blended_speeds_from_gradients(self.GRADIENTS, 0.0) == pytest.approx(
            minetti_speeds_from_gradients(self.GRADIENTS)
        )

    def test_weight_one_is_exactly_tobler(self):
        assert blended_speeds_from_gradients(self.GRADIENTS, 1.0) == pytest.approx(
            tobler_speeds_from_gradients(self.GRADIENTS)
        )

    @pytest.mark.parametrize("weight", [0.0, 0.25, 0.5, 0.75, 1.0])
    def test_flat_ground_is_one_at_every_weight(self, weight):
        # Both curves are normalized to flat ground, so no blend of them can
        # move it — this is what makes a weight readable as "x% Tobler"
        # rather than a change of reference speed.
        assert blended_speeds_from_gradients([0.0], weight) == pytest.approx([1.0])

    def test_half_and_half_is_the_geometric_mean_of_the_two_curves(self):
        [minetti] = minetti_speeds_from_gradients([0.2])
        [tobler] = tobler_speeds_from_gradients([0.2])
        assert blended_speeds_from_gradients([0.2], 0.5) == pytest.approx([math.sqrt(minetti * tobler)])

    @pytest.mark.parametrize("gradient", [-0.3, -0.15, 0.15, 0.3])
    def test_blend_moves_monotonically_from_one_curve_to_the_other(self, gradient):
        speeds = [blended_speeds_from_gradients([gradient], w / 10)[0] for w in range(11)]
        deltas = [later - earlier for earlier, later in zip(speeds, speeds[1:])]
        assert all(d > 0 for d in deltas) or all(d < 0 for d in deltas)
        assert min(speeds[0], speeds[-1]) <= min(speeds) and max(speeds) <= max(speeds[0], speeds[-1])

    @pytest.mark.parametrize("weight", [-0.01, 1.01, 2.0])
    def test_weight_outside_the_unit_interval_raises(self, weight):
        with pytest.raises(ValueError):
            blended_speeds_from_gradients([0.1], weight)

    def test_empty_gradients_give_empty_speeds(self):
        assert blended_speeds_from_gradients([], 0.5) == []

    def test_speed_is_symmetric_around_the_peak_offset(self):
        # Gradients equidistant from -0.05 (the peak) should give equal speed.
        [below_speed, above_speed] = tobler_speeds_from_gradients([-0.15, 0.05])
        assert below_speed == pytest.approx(above_speed)
