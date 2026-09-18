import pytest

from gpx2fit.core.models import SportType, Track
from gpx2fit.core.pacing.curve_selection import TOBLER_THRESHOLDS, resolve_tobler_weight
from tests.core.conftest import flat_track, point, resolved_weight, rolling_track


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
