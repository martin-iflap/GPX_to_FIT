import math
import statistics

import pytest

from gpx2fit.core.models import TrackPoint
from gpx2fit.core.pacing.variation import (
    DEFAULT_VARIATION,
    local_spread,
    pace_variation,
    route_noise,
    route_seed,
)
from tests.core.conftest import point


def _route(length_m: float, spacing_m: float, heading: float = 1.0) -> list[TrackPoint]:
    """A straight route with distinct coordinates, so its seed depends on where it is."""
    count = int(length_m / spacing_m) + 1
    return [
        point(lat=46.0 + heading * index * spacing_m * 1e-5, lon=14.0, distance_from_start=index * spacing_m)
        for index in range(count)
    ]


def _with_stop_duplicate(points: list[TrackPoint], index: int) -> list[TrackPoint]:
    """What pacing.stops.expand_track_with_stops produces: a zero-distance copy right after points[index]."""
    original = points[index]
    duplicate = point(
        lat=original.lat, lon=original.lon, elevation=original.elevation,
        distance_from_start=original.distance_from_start,
    )
    return points[: index + 1] + [duplicate] + points[index + 1:]


def _legs(points: list[TrackPoint]) -> list[float]:
    return [b.distance_from_start - a.distance_from_start for a, b in zip(points, points[1:])]


def _correlation(values: list[float], lag: int) -> float:
    return statistics.correlation(values[:-lag], values[lag:])


class TestRouteSeed:
    def test_the_same_route_gives_the_same_seed(self):
        assert route_seed(_route(2000.0, 10.0)) == route_seed(_route(2000.0, 10.0))

    def test_a_stop_duplicate_does_not_change_the_seed(self):
        points = _route(2000.0, 10.0)
        assert route_seed(_with_stop_duplicate(points, 50)) == route_seed(points)

    def test_a_different_route_gives_a_different_seed(self):
        assert route_seed(_route(2000.0, 10.0)) != route_seed(_route(2000.0, 10.0, heading=-1.0))


class TestRouteNoise:
    def test_one_value_per_leg(self):
        points = _route(1000.0, 10.0)
        assert len(route_noise(points)) == len(points) - 1

    def test_the_same_route_gives_the_same_noise(self):
        # Converting again, or after moving an anchor, must not reshuffle the pace.
        assert route_noise(_route(5000.0, 10.0)) == route_noise(_route(5000.0, 10.0))

    def test_a_different_route_gives_different_noise(self):
        assert route_noise(_route(5000.0, 10.0)) != route_noise(_route(5000.0, 10.0, heading=-1.0))

    def test_a_stop_duplicate_leaves_the_pattern_untouched(self):
        # The zero-distance leg keeps the value before it and draws nothing,
        # so every leg after the stop still gets the noise it had without it.
        points = _route(5000.0, 10.0)
        stop_index = 200
        original = route_noise(points)
        expanded = route_noise(_with_stop_duplicate(points, stop_index))

        assert expanded[:stop_index] == original[:stop_index]
        assert expanded[stop_index] == original[stop_index - 1]
        assert expanded[stop_index + 1:] == original[stop_index:]

    @pytest.mark.parametrize("spacing_m", [5.0, 20.0])
    def test_unit_variance_whatever_the_point_spacing(self, spacing_m):
        noise = route_noise(_route(300_000.0, spacing_m))
        assert statistics.fmean(noise) == pytest.approx(0.0, abs=0.2)
        assert statistics.pstdev(noise) == pytest.approx(1.0, abs=0.15)

    def test_neighbouring_legs_move_together(self):
        # Correlated over distance, not white noise: per-leg jitter on 5 m legs
        # would average out invisibly and only make the speed field noisy.
        noise = route_noise(_route(100_000.0, 5.0))
        assert _correlation(noise, lag=1) > 0.95

    def test_legs_far_apart_are_independent(self):
        noise = route_noise(_route(300_000.0, 20.0))
        assert abs(_correlation(noise, lag=150)) < 0.2  # 3 km apart


class TestLocalSpread:
    def test_constant_speed_has_no_spread(self):
        legs = [20.0] * 200
        assert local_spread(legs, [1.3] * 200, 400.0) == pytest.approx([0.0] * 200, abs=1e-9)

    def test_alternating_speed_has_a_large_spread(self):
        # 100 m at 1.2, 100 m at 0.8, repeated: log-speed swings about ±0.2.
        legs = [20.0] * 200
        speeds = [1.2 if (index // 5) % 2 == 0 else 0.8 for index in range(200)]
        spreads = local_spread(legs, speeds, 400.0)
        assert min(spreads) > 0.15

    def test_a_steady_stretch_far_from_varied_terrain_reads_as_steady(self):
        legs = [20.0] * 300
        speeds = [1.0] * 150 + [1.2 if (index // 5) % 2 == 0 else 0.8 for index in range(150)]
        spreads = local_spread(legs, speeds, 400.0)
        assert spreads[:130] == pytest.approx([0.0] * 130, abs=1e-9)
        assert min(spreads[170:]) > 0.15

    def test_the_window_keeps_its_length_at_the_track_ends(self):
        # Clipped at the start, the first leg would only see the steady part;
        # slid inward like the gradient window, it sees the change 300 m in.
        legs = [20.0] * 100
        speeds = [1.0] * 15 + [1.5] * 85
        assert local_spread(legs, speeds, 400.0)[0] > 0.1

    def test_zero_distance_legs_carry_no_weight(self):
        legs = [20.0] * 100
        speeds = [1.0] * 100
        assert local_spread(legs[:50] + [0.0] + legs[50:], speeds[:50] + [50.0] + speeds[50:], 400.0) == (
            pytest.approx([0.0] * 101, abs=1e-9)
        )


class TestPaceVariation:
    @staticmethod
    def _noise(count: int) -> list[float]:
        return [math.sin(index / 7.0) for index in range(count)]

    def test_scale_zero_changes_nothing(self):
        legs = [20.0] * 100
        assert pace_variation(legs, [1.0] * 100, self._noise(100), scale=0.0) == [1.0] * 100

    def test_steady_terrain_gets_the_full_amplitude(self):
        legs = [20.0] * 100
        noise = self._noise(100)
        multipliers = pace_variation(legs, [1.0] * 100, noise, scale=1.0)
        expected = [DEFAULT_VARIATION.sigma * value for value in noise]
        assert [math.log(m) for m in multipliers] == pytest.approx(expected)

    def test_a_steep_but_even_climb_counts_as_steady(self):
        # It's the speed that has to stay constant, not the gradient that has to be zero.
        legs = [20.0] * 100
        noise = self._noise(100)
        multipliers = pace_variation(legs, [0.55] * 100, noise, scale=1.0)
        assert [math.log(m) for m in multipliers] == pytest.approx([DEFAULT_VARIATION.sigma * v for v in noise])

    def test_varied_terrain_gets_none(self):
        legs = [20.0] * 200
        speeds = [1.2 if (index // 5) % 2 == 0 else 0.8 for index in range(200)]
        assert pace_variation(legs, speeds, self._noise(200), scale=1.0) == [1.0] * 200

    def test_the_scale_multiplies_every_deviation(self):
        legs = [20.0] * 100
        noise = self._noise(100)
        single = pace_variation(legs, [1.0] * 100, noise, scale=1.0)
        doubled = pace_variation(legs, [1.0] * 100, noise, scale=2.0)
        assert [math.log(m) for m in doubled] == pytest.approx([2 * math.log(m) for m in single])

    def test_rejects_noise_of_the_wrong_length(self):
        with pytest.raises(ValueError):
            pace_variation([20.0] * 10, [1.0] * 10, [0.0] * 9, scale=1.0)
