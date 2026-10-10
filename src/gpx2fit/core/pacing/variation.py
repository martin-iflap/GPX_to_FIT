"""Natural pace variation on stretches where the gradient model alone would hold one speed.

The gradient curves map terrain to speed, so a flat road or a long even climb
comes out at one constant speed for kilometres, which no recorded activity
does. This adds slow, smooth wander to the pace there, and only there: where
the modeled speed already varies with the terrain, the terrain is variation
enough and nothing is added.

The noise is deterministic. It is seeded from the route and laid out by
distance along it, so converting the same route again, or after moving an
anchor or adding a stop, keeps the same pattern.
"""

import math
import random
import struct
import zlib
from bisect import bisect_left, bisect_right
from dataclasses import dataclass

from gpx2fit.core.models import TrackPoint
from gpx2fit.core.pacing.curve_selection import smoothstep


# todo: All settings are first guesses (2026-10-02) but they do look good also against the corpus.
@dataclass(frozen=True)
class VariationSettings:
    """How much the pace wanders on steady stretches, and what counts as steady.

    Attributes:
        sigma: Standard deviation of the added log-speed on a fully steady
            stretch. 0.06 is about ±12% at two standard deviations.
        steady_spread: The local log-speed spread (see local_spread) at the
            middle of the ramp from "steady, full noise" to "varied, none".
        steady_band: Half-width of that ramp: full noise at or below
            steady_spread - steady_band, none at or above steady_spread + steady_band.
        window_m: Route distance the spread is measured over, i.e. how long a
            stretch has to hold one speed before it counts as steady.
        long_correlation_m: Correlation length of the slow component, the
            drift in pace over several hundred metres.
        short_correlation_m: Correlation length of the quick component.
        long_share: The slow component's share of the variance.
    """
    sigma: float = 0.06
    steady_spread: float = 0.03
    steady_band: float = 0.02
    window_m: float = 150.0
    long_correlation_m: float = 600.0
    short_correlation_m: float = 80.0
    long_share: float = 0.8


DEFAULT_VARIATION = VariationSettings()

# Coordinates are rounded to this many decimal degrees (about 0.1 m) for the seed.
_SEED_COORDINATE_SCALE = 1e6


def route_seed(points: list[TrackPoint]) -> int:
    """A seed that depends only on where the route goes.

    zlib.crc32 rather than hash(), which Python salts per process. Points at
    the same distance as the one before (a stop's duplicated point) are left
    out, so adding a stop doesn't change the seed.

    Args:
        points: The track's points, in route order.
    Returns:
        A non-negative 32-bit integer.
    """
    packed = bytearray()
    previous_distance = None
    for p in points:
        if p.distance_from_start == previous_distance:
            continue
        previous_distance = p.distance_from_start
        packed += struct.pack(
            "<qq", round(p.lat * _SEED_COORDINATE_SCALE), round(p.lon * _SEED_COORDINATE_SCALE)
        )
    return zlib.crc32(bytes(packed))


def route_noise(points: list[TrackPoint], settings: VariationSettings = DEFAULT_VARIATION) -> list[float]:
    """Unit-variance noise per leg, correlated over distance along the route.

    The sum of two Ornstein–Uhlenbeck processes stepped by each leg's
    distance, one slow (long_correlation_m) and one quick
    (short_correlation_m). Stepping by distance with the exact discretization,
    x ← x·e^(−d/L) + sqrt(1 − e^(−2d/L))·N(0, 1), keeps the result's
    statistics the same however densely the route is sampled. A
    zero-distance leg keeps the value before it and draws nothing, so a stop
    doesn't shift the pattern after it.

    Args:
        points: The track's points, in route order.
        settings: The correlation lengths and their mix.
    Returns:
        One value per leg (N-1 values for N points).
    """
    rng = random.Random(route_seed(points))
    slow = rng.gauss(0.0, 1.0)
    quick = rng.gauss(0.0, 1.0)
    slow_weight = math.sqrt(settings.long_share)
    quick_weight = math.sqrt(1.0 - settings.long_share)

    def step(value: float, dist: float, correlation_m: float) -> float:
        return (
                value * math.exp(-dist / correlation_m)
                + math.sqrt(-math.expm1(-2.0 * dist / correlation_m)) * rng.gauss(0.0, 1.0)
        )

    noise = []
    for earlier, later in zip(points, points[1:]):
        distance = later.distance_from_start - earlier.distance_from_start
        if distance > 0:
            slow = step(slow, distance, settings.long_correlation_m)
            quick = step(quick, distance, settings.short_correlation_m)
        noise.append(slow_weight * slow + quick_weight * quick)
    return noise


def _spread_windows(leg_distances: list[float], window_m: float) -> tuple[list[int], list[int]]:
    """Each leg's window, as a half-open range [lo, hi) of leg indexes.

    The window is centred on the leg and keeps its full length at the track's
    ends by sliding inward, as the gradient window does. A leg belongs to a
    window when its midpoint does. These depend only on the distances, so the
    tuning harness computes them once per track.
    Returns:
        Two lists of indexes, one per leg, the low and high ends of the window.
    """
    midpoints = []
    start = 0.0
    for distance in leg_distances:
        midpoints.append(start + distance / 2)
        start += distance
    track_length = start

    half = window_m / 2
    los, his = [], []
    for middle in midpoints:
        low, high = middle - half, middle + half
        if window_m >= track_length:
            low, high = 0.0, track_length
        elif low < 0.0:
            low, high = 0.0, window_m
        elif high > track_length:
            low, high = track_length - window_m, track_length
        los.append(bisect_left(midpoints, low))
        his.append(bisect_right(midpoints, high))
    return los, his


def local_spread(leg_distances: list[float], speeds: list[float], window_m: float) -> list[float]:
    """Each leg's distance-weighted RMS of log-speed around its local mean, over a window centred on it.

    See _spread_windows for the window. Legs with no distance or no speed
    carry no weight.

    Args:
        leg_distances: Per-leg distances in metres.
        speeds: Per-leg speeds, any unit, same length and order.
        window_m: The window's length in metres.
    Returns:
        One spread per leg. 0.0 means the speed is constant across the window.
    """
    weights = [d if d > 0 and s > 0 else 0.0 for d, s in zip(leg_distances, speeds)]
    logs = [math.log(s) if w > 0 else 0.0 for w, s in zip(weights, speeds)]
    total_weight = math.fsum(weights)
    if total_weight <= 0:
        return [0.0] * len(leg_distances)
    # Centred first, so a constant speed sums to exactly zero instead of
    # leaving rounding residue in the variance.
    reference = math.fsum(w * x for w, x in zip(weights, logs)) / total_weight

    prefix_weight = [0.0]
    prefix_sum = [0.0]
    prefix_squares = [0.0]
    for weight, value in zip(weights, logs):
        centred = value - reference
        prefix_weight.append(prefix_weight[-1] + weight)
        prefix_sum.append(prefix_sum[-1] + weight * centred)
        prefix_squares.append(prefix_squares[-1] + weight * centred * centred)

    spreads = []
    for lo, hi in zip(*_spread_windows(leg_distances, window_m)):
        weight = prefix_weight[hi] - prefix_weight[lo]
        if weight <= 0:
            spreads.append(0.0)
            continue
        mean = (prefix_sum[hi] - prefix_sum[lo]) / weight
        variance = (prefix_squares[hi] - prefix_squares[lo]) / weight - mean * mean
        spreads.append(math.sqrt(max(0.0, variance)))
    return spreads


def pace_variation(
    leg_distances: list[float],
    base_speeds: list[float],
    noise: list[float],
    scale: float,
    settings: VariationSettings = DEFAULT_VARIATION,
) -> list[float]:
    """Per-leg speed multipliers that vary the pace only where it would otherwise stay steady.

    A leg's amplitude ramps from settings.sigma, where the base speeds around
    it hold one value, down to nothing where they already vary (see
    VariationSettings.steady_spread). It is the *speed* that has to be steady,
    so a long even climb gets the same variation as a flat road.

    Args:
        leg_distances: Per-leg distances in metres.
        base_speeds: The modeled per-leg speeds before any variation (the
            gradient curve times any surface multipliers).
        noise: Per-leg unit-variance noise, from route_noise.
        scale: Multiplies every leg's log-speed deviation: the user's
            smoothness level (curve_selection.SMOOTHNESS_SCALES). 0.0 adds none.
        settings: The amplitude and what counts as steady.
    Returns:
        One multiplier per leg, exactly 1.0 wherever no variation applies.
    Raises:
        ValueError: If the three lists don't have the same length.
    """
    if not len(leg_distances) == len(base_speeds) == len(noise):
        raise ValueError(
            f"Need one base speed and one noise value per leg: got {len(leg_distances)} legs, "
            f"{len(base_speeds)} speeds and {len(noise)} noise values."
        )
    spreads = local_spread(leg_distances, base_speeds, settings.window_m)
    return [
        math.exp(scale * settings.sigma * (1.0 - smoothstep(spread, settings.steady_spread, settings.steady_band)) * value)
        for spread, value in zip(spreads, noise)
    ]
