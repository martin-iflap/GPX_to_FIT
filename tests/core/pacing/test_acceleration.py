import math
import random

import pytest

from gpx2fit.core.models import SportType
from gpx2fit.core.pacing.acceleration import MAX_ACCELERATION_MPS2, limit_acceleration

HIKING_A = MAX_ACCELERATION_MPS2[SportType.HIKING]
RUNNING_A = MAX_ACCELERATION_MPS2[SportType.RUNNING]


def _caps(distances: list[float], max_acceleration: float) -> list[float]:
    """The largest allowed |Δv²| between each pair of adjacent legs (inf across a zero-distance leg)."""
    return [
        max_acceleration * (d0 + d1) if d0 > 0 and d1 > 0 else math.inf
        for d0, d1 in zip(distances, distances[1:])
    ]


def _assert_within_limit(distances: list[float], speeds: list[float], max_acceleration: float) -> None:
    for index, (v0, v1, cap) in enumerate(zip(speeds, speeds[1:], _caps(distances, max_acceleration))):
        assert abs(v1 * v1 - v0 * v0) <= cap * (1 + 1e-9), f"legs {index}/{index + 1}"


def _changed(before: list[float], after: list[float]) -> list[int]:
    return [index for index, (old, new) in enumerate(zip(before, after)) if old != new]


def _step(count: int, before: float, after: float) -> list[float]:
    return [before] * (count // 2) + [after] * (count - count // 2)


def _patch(count: int, base: float, patch: float, start: int, length: int) -> list[float]:
    speeds = [base] * count
    for index in range(start, start + length):
        speeds[index] = patch
    return speeds


class TestLeavesFeasibleProfilesAlone:
    def test_constant_speed_is_unchanged(self):
        distances = [5.0] * 50
        speeds = [1.3] * 50
        assert limit_acceleration(distances, speeds, HIKING_A) == speeds

    def test_gentle_wander_is_unchanged_exactly(self):
        # Like pacing.variation's: ±6% over a few hundred metres, nowhere near the limit.
        distances = [5.0] * 400
        speeds = [1.3 * math.exp(0.06 * math.sin(index * 5.0 / 100.0)) for index in range(400)]
        assert limit_acceleration(distances, speeds, HIKING_A) == speeds

    def test_legs_away_from_a_ramp_come_back_bit_for_bit(self):
        # Arbitrary floats, where √(v²) often isn't v to the last bit.
        rng = random.Random(5)
        distances = [rng.uniform(1.0, 2.0) for _ in range(400)]
        speeds = [rng.uniform(1.29, 1.31) for _ in range(200)] + [rng.uniform(0.79, 0.81) for _ in range(200)]
        limited = limit_acceleration(distances, speeds, HIKING_A)
        assert limited != speeds  # premise: the step is ramped
        assert limited[:190] == speeds[:190]
        assert limited[200:] == speeds[200:]

    def test_a_step_under_the_cap_on_long_legs_is_unchanged(self):
        # 15 m legs: the cap is a * 30 m of v², so a 1.3 -> 1.0 step (Δv² 0.69) fits under 0.15 * 30 = 4.5.
        distances = [15.0] * 20
        speeds = _step(20, 1.3, 1.0)
        assert limit_acceleration(distances, speeds, HIKING_A) == speeds


class TestTheTargetIsACeiling:
    """The modeled speed is the most the terrain allows, so limiting only ever slows a leg."""

    @pytest.mark.parametrize("seed", range(10))
    def test_no_leg_gets_faster(self, seed):
        rng = random.Random(seed)
        distances = [rng.uniform(0.5, 3.0) for _ in range(300)]
        speeds = [rng.uniform(0.5, 5.0) for _ in range(300)]
        limited = limit_acceleration(distances, speeds, HIKING_A)
        assert all(new <= old for old, new in zip(speeds, limited))

    @pytest.mark.parametrize("seed", range(10))
    def test_it_slows_as_little_as_it_can(self, seed):
        # The fastest profile under the target: each slowed leg is exactly at
        # the limit against a neighbor, or it could have stayed faster.
        rng = random.Random(seed)
        distances = [rng.uniform(0.5, 3.0) for _ in range(300)]
        speeds = [rng.uniform(0.5, 5.0) for _ in range(300)]
        limited = limit_acceleration(distances, speeds, HIKING_A)
        squares = [v * v for v in limited]
        caps = _caps(distances, HIKING_A)
        for index in _changed(speeds, limited):
            tight = [
                squares[index] == pytest.approx(squares[neighbour] + cap, rel=1e-12)
                for neighbour, cap in ((index - 1, caps[index - 1] if index > 0 else math.inf),
                                       (index + 1, caps[index] if index < len(caps) else math.inf))
                if cap != math.inf
            ]
            assert any(tight), f"leg {index} was slowed more than needed"

    @pytest.mark.parametrize("seed", range(10))
    def test_every_pair_is_within_the_limit(self, seed):
        rng = random.Random(seed)
        distances = [rng.uniform(0.5, 3.0) for _ in range(300)]
        speeds = [rng.uniform(0.5, 5.0) for _ in range(300)]
        _assert_within_limit(distances, limit_acceleration(distances, speeds, HIKING_A), HIKING_A)


class TestStepDown:
    """Onto slower terrain: you see it coming, so you have slowed down by the time you reach it."""

    distances = [1.0] * 200
    speeds = _step(200, 1.3, 0.8)

    def limited(self) -> list[float]:
        return limit_acceleration(self.distances, self.speeds, HIKING_A)

    def test_every_pair_is_within_the_limit(self):
        _assert_within_limit(self.distances, self.limited(), HIKING_A)

    def test_the_slow_terrain_is_untouched(self):
        assert self.limited()[100:] == self.speeds[100:]

    def test_the_ramp_ends_where_the_slow_terrain_begins(self):
        changed = _changed(self.speeds, self.limited())
        assert changed[-1] == 99
        assert self.limited()[99] ** 2 == pytest.approx(0.8 ** 2 + HIKING_A * 2.0, rel=1e-12)

    def test_it_actually_ramps(self):
        # Δv² = 1.05 at 2 * 0.15 m/s² of v² per metre spans about 3.5 m, i.e. several 1 m legs.
        assert len(_changed(self.speeds, self.limited())) >= 3

    def test_the_ramp_is_monotone(self):
        limited = self.limited()
        assert all(later <= earlier for earlier, later in zip(limited, limited[1:]))


class TestStepUp:
    """Onto faster terrain: you start speeding up when you reach it, not before."""

    distances = [1.0] * 200
    speeds = _step(200, 0.8, 1.3)

    def limited(self) -> list[float]:
        return limit_acceleration(self.distances, self.speeds, HIKING_A)

    def test_every_pair_is_within_the_limit(self):
        _assert_within_limit(self.distances, self.limited(), HIKING_A)

    def test_the_slow_terrain_is_untouched(self):
        assert self.limited()[:100] == self.speeds[:100]

    def test_the_ramp_starts_where_the_fast_terrain_begins(self):
        changed = _changed(self.speeds, self.limited())
        assert changed[0] == 100
        assert self.limited()[100] ** 2 == pytest.approx(0.8 ** 2 + HIKING_A * 2.0, rel=1e-12)

    def test_the_ramp_is_monotone(self):
        limited = self.limited()
        assert all(later >= earlier for earlier, later in zip(limited, limited[1:]))


class TestShortPatches:
    def test_a_short_fast_patch_is_only_partly_used(self):
        # 10 m of +30% for a runner: there isn't room to reach the full speed
        # and slow down again, so it peaks lower, and only on the patch.
        distances = [2.0] * 200
        speeds = _patch(200, 3.0, 3.9, 100, 5)
        limited = limit_acceleration(distances, speeds, RUNNING_A)
        _assert_within_limit(distances, limited, RUNNING_A)
        assert set(_changed(speeds, limited)) <= set(range(100, 105))
        assert 3.0 < max(limited) < 3.9

    def test_a_short_slow_patch_is_kept_whole(self):
        # 10 m of -40% for a runner: the whole patch is crossed at its own speed,
        # slowing before it and speeding up after it.
        distances = [2.0] * 200
        speeds = _patch(200, 3.0, 1.8, 100, 5)
        limited = limit_acceleration(distances, speeds, RUNNING_A)
        _assert_within_limit(distances, limited, RUNNING_A)
        assert limited[100:105] == speeds[100:105]
        changed = _changed(speeds, limited)
        assert min(changed) < 100 and max(changed) >= 105


class TestIrregularLegs:
    """A GPX's spacing is uneven, so each pair's cap depends on both of its legs."""

    distances = [0.5 + (index * 7 % 11) * 0.9 for index in range(300)]
    speeds = [1.3 if (index // 23) % 2 else 0.7 + 0.01 * (index % 5) for index in range(300)]

    def test_every_pair_is_within_the_limit(self):
        _assert_within_limit(self.distances, limit_acceleration(self.distances, self.speeds, HIKING_A), HIKING_A)

    def test_it_stays_inside_the_input_range(self):
        limited = limit_acceleration(self.distances, self.speeds, HIKING_A)
        assert all(min(self.speeds) <= v <= max(self.speeds) for v in limited)


class TestZeroDistanceLegs:
    def test_a_step_across_a_stop_is_left_alone(self):
        distances = [1.0] * 20 + [0.0] + [1.0] * 20
        speeds = [1.3] * 20 + [0.0] + [0.8] * 20
        assert limit_acceleration(distances, speeds, HIKING_A) == speeds

    def test_runs_either_side_of_a_stop_are_limited_independently(self):
        distances = [1.0] * 40 + [0.0] + [1.0] * 40
        speeds = _step(40, 1.3, 0.8) + [0.0] + _step(40, 0.8, 1.3)
        limited = limit_acceleration(distances, speeds, HIKING_A)
        _assert_within_limit(distances, limited, HIKING_A)
        assert limited[40] == 0.0
        assert limited[:40] == limit_acceleration(distances[:40], speeds[:40], HIKING_A)
        assert limited[41:] == limit_acceleration(distances[41:], speeds[41:], HIKING_A)


class TestDensity:
    def test_the_same_profile_at_two_densities_differs_only_by_where_half_a_leg_falls(self):
        # Acceleration is measured from leg midpoint to leg midpoint, so the
        # ramp ends half a slow leg past the step: a coarse ramp sits
        # (coarse - fine) / 2 metres further out, which at the ramp's slope of
        # 2a in v² per metre is a · (coarse - fine). Vanishes as legs shrink.
        def profile(spacing: float) -> tuple[list[float], list[float], list[float]]:
            count = int(200 / spacing)
            midpoints = [(index + 0.5) * spacing for index in range(count)]
            speeds = [1.3 if m < 100 else 0.8 for m in midpoints]
            return [spacing] * count, speeds, midpoints

        fine_d, fine_v, fine_m = profile(0.25)
        coarse_d, coarse_v, coarse_m = profile(1.0)
        fine = limit_acceleration(fine_d, fine_v, HIKING_A)
        coarse = limit_acceleration(coarse_d, coarse_v, HIKING_A)
        assert coarse != coarse_v  # premise: the step is ramped
        for midpoint, speed in zip(coarse_m, coarse):
            # The fine legs covering this coarse leg, averaged in v².
            inside = [v * v for m, v in zip(fine_m, fine) if abs(m - midpoint) < 0.5]
            assert abs(speed * speed - sum(inside) / len(inside)) <= HIKING_A * (1.0 - 0.25) * (1 + 1e-9)


class TestEdges:
    def test_empty(self):
        assert limit_acceleration([], [], HIKING_A) == []

    def test_single_leg(self):
        assert limit_acceleration([5.0], [1.3], HIKING_A) == [1.3]

    def test_does_not_mutate_its_input(self):
        distances = [1.0] * 10
        speeds = _step(10, 1.3, 0.8)
        snapshot = list(speeds)
        limit_acceleration(distances, speeds, HIKING_A)
        assert speeds == snapshot

    def test_a_large_step_on_short_legs(self):
        # Δv² ≈ 15 at 0.3 m/s² of v² per metre: a ramp of ~50 one-metre legs.
        distances = [1.0] * 400
        speeds = _step(400, 4.0, 1.0)
        limited = limit_acceleration(distances, speeds, HIKING_A)
        _assert_within_limit(distances, limited, HIKING_A)
        assert len(_changed(speeds, limited)) >= 45

    def test_mismatched_lengths_raise(self):
        with pytest.raises(ValueError):
            limit_acceleration([1.0, 1.0], [1.0], HIKING_A)

    def test_non_positive_acceleration_raises(self):
        with pytest.raises(ValueError):
            limit_acceleration([1.0, 1.0], [1.0, 1.0], 0.0)


def test_running_allows_more_than_hiking():
    assert MAX_ACCELERATION_MPS2[SportType.RUNNING] > MAX_ACCELERATION_MPS2[SportType.HIKING]
