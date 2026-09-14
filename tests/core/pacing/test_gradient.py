import math
import pytest
from gpx2fit.core.models import Track
from gpx2fit.core.pacing.gradient import calculate_gradient, calculate_minetti_speeds, calculate_tobler_speeds
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
        gradients = calculate_gradient(track)
        assert gradients[0] == pytest.approx(0.1)
        assert gradients[1] == pytest.approx(-0.05)

    def test_zero_distance_leg_does_not_divide_by_zero(self):
        # Two points at the same distance_from_start but different elevation.
        track = _track([(0.0, 0.0), (50.0, 0.0), (50.0, 100.0)])
        gradients = calculate_gradient(track)
        assert gradients[0] == 0.0


class TestCalculateMinettiSpeeds:
    def test_matches_inverted_cost_formula_on_flat_ground(self):
        track = _track([(0.0, 0.0), (0.0, 100.0)])
        [speed] = calculate_minetti_speeds(track)
        assert speed == pytest.approx(1.0 / _minetti_cost(0.0))

    def test_matches_inverted_cost_formula_on_moderate_uphill(self):
        track = _track([(0.0, 0.0), (10.0, 100.0)])  # 10% grade
        [speed] = calculate_minetti_speeds(track)
        assert speed == pytest.approx(1.0 / _minetti_cost(0.1))

    def test_matches_inverted_cost_formula_on_moderate_downhill(self):
        track = _track([(10.0, 0.0), (0.0, 100.0)])  # -10% grade
        [speed] = calculate_minetti_speeds(track)
        assert speed == pytest.approx(1.0 / _minetti_cost(-0.1))

    def test_out_of_range_gradient_with_nonpositive_cost_yields_zero_speed(self):
        # A 100% grade descent (-1.0) drives Minetti's polynomial cost negative.
        track = _track([(10.0, 0.0), (0.0, 10.0)])
        assert _minetti_cost(-1.0) <= 0  # sanity-check the premise of this test
        [speed] = calculate_minetti_speeds(track)
        assert speed == 0.0

    def test_steeper_uphill_is_slower_than_gentle_uphill(self):
        gentle = _track([(0.0, 0.0), (5.0, 100.0)])  # 5% grade
        steep = _track([(0.0, 0.0), (20.0, 100.0)])  # 20% grade
        [gentle_speed] = calculate_minetti_speeds(gentle)
        [steep_speed] = calculate_minetti_speeds(steep)
        assert steep_speed < gentle_speed


class TestCalculateToblerSpeeds:
    def test_matches_hiking_function_on_flat_ground(self):
        track = _track([(0.0, 0.0), (0.0, 100.0)])
        [speed] = calculate_tobler_speeds(track)
        assert speed == pytest.approx(_tobler_speed_kmh(0.0) / 3.6)

    def test_matches_hiking_function_on_uphill_and_downhill(self):
        uphill = _track([(0.0, 0.0), (15.0, 100.0)])
        downhill = _track([(15.0, 0.0), (0.0, 100.0)])
        [uphill_speed] = calculate_tobler_speeds(uphill)
        [downhill_speed] = calculate_tobler_speeds(downhill)
        assert uphill_speed == pytest.approx(_tobler_speed_kmh(0.15) / 3.6)
        assert downhill_speed == pytest.approx(_tobler_speed_kmh(-0.15) / 3.6)

    def test_peak_speed_is_on_a_gentle_downhill_not_flat_ground(self):
        # Tobler's function peaks at gradient == -0.05, not 0.
        flat = _track([(0.0, 0.0), (0.0, 100.0)])
        gentle_downhill = _track([(5.0, 0.0), (0.0, 100.0)])
        [flat_speed] = calculate_tobler_speeds(flat)
        [gentle_downhill_speed] = calculate_tobler_speeds(gentle_downhill)
        assert gentle_downhill_speed > flat_speed

    def test_speed_is_symmetric_around_the_peak_offset(self):
        # Gradients equidistant from -0.05 (the peak) should give equal speed.
        below_peak = _track([(15.0, 0.0), (0.0, 100.0)])  # gradient -0.15, 0.10 below peak
        above_peak = _track([(0.0, 0.0), (5.0, 100.0)])  # gradient 0.05, 0.10 above peak
        [below_speed] = calculate_tobler_speeds(below_peak)
        [above_speed] = calculate_tobler_speeds(above_peak)
        assert below_speed == pytest.approx(above_speed)
