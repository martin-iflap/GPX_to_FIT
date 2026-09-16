"""Per-leg gradient and gradient-based speed models used to shape pacing.

Both speed functions return speed as a multiple of the same model's own
flat-ground speed — 1.0 on the flat, whichever model is used. They are
*relative* only: combine.py scales them to match each anchor segment's known
duration, so absolute magnitude here would be discarded anyway; normalizing
both to the same reference is what makes a value like 0.68 directly readable
as "68% of flat pace" in either model.
"""

import math
from bisect import bisect_left, bisect_right

from gpx2fit.core.models import Track


_TOBLER_SLOPE_FACTOR = 3.5
_TOBLER_SLOPE_OFFSET = 0.05  # shifts peak speed to a gentle ~5% downhill grade

# Gradient is averaged over this much route distance, centred on the leg,
# rather than taken point-to-point. GPX elevation is usually DEM data
# rounded to whole metres, so on a gentle even slope, point-to-point
# rise/run alternates between flat 0% legs and single-metre "steps" read
# as 20%+ grades; averaging smooths that out while still capturing real
# climbs and descents longer than the window. DEM resolution is rarely
# finer than ~30 m anyway, so shorter features aren't real terrain.
GRADIENT_WINDOW_M = 70.0

# Minetti's polynomial is only calibrated within roughly this gradient
# range; beyond it the un-clamped quintic turns back upward instead of
# decreasing. Rather than clamp the gradient itself (which would pin every
# leg steeper than this boundary to one identical speed), legs beyond this
# range follow a scaled Tobler curve instead — see _raw_minetti_relative_speed.
_MINETTI_MAX_GRADIENT = 0.45

# Minetti's C(g) is an energy cost, so 1/cost swings harder than real pace
# does (a 20% descent at 2x flat speed, a 20% climb at 0.4x) — scaled to a
# slow real-world activity, that pushes climbs below Strava's "resting"
# threshold (their time vanishes from moving time) and turns descents into
# sprints. So relative speed is raised to these exponents instead: 1.0 is
# raw Minetti, lower is flatter. Descents are softened more than climbs
# because downhill pace is limited by footing and braking, not metabolic
# cost. With these values a 10% climb runs at ~0.68x flat speed, a 20%
# climb at ~0.50x, and the fastest descent at ~1.4x (TestMinettiSoftening
# in test_gradient.py pins these bands). They're arguments, not hard-coded,
# so a future model selector can tune the response per activity.
MINETTI_UPHILL_EXPONENT = 0.6
MINETTI_DOWNHILL_EXPONENT = 0.5


def _elevation_at(
    distances: list[float],
    elevations: list[float],
    distance: float,
    last_at_distance: bool
) -> float:
    """Elevation at `distance` along the route, linearly interpolated between points.

    Args:
        distances: Non-decreasing distance_from_start values, one per point.
        elevations: Elevation per point, same length and order as distances.
        distance: Target distance; must lie within [distances[0], distances[-1]].
            Callers are responsible for clamping — calculate_gradient does.
        last_at_distance: Where several points share exactly this distance
            (a stop's duplicated point), pick the last instead of the first.

    Returns:
        The interpolated (or exact, if `distance` matches a point) elevation.
    """
    index = bisect_left(distances, distance)
    if distances[index] == distance:
        if last_at_distance:
            index = bisect_right(distances, distance) - 1
        return elevations[index]

    before = index - 1
    fraction = (distance - distances[before]) / (distances[index] - distances[before])
    return elevations[before] + fraction * (elevations[index] - elevations[before])


def calculate_gradient(track: Track, window_m: float = GRADIENT_WINDOW_M) -> list[float]:
    """Calculate each leg's gradient, averaged over a distance window centred on the leg.

    Args:
        track: Track whose points already have elevation and distance_from_start
            set, with distance_from_start non-decreasing.
        window_m: Route distance, in meters, the gradient is averaged over (see
            GRADIENT_WINDOW_M). The window is widened to cover the leg itself
            if the leg is longer, and truncated at the track's ends rather
            than padded. 0 gives plain point-to-point rise/run.

    Returns:
        One gradient per leg (N-1 values for N points), as a dimensionless
        rise/run ratio — e.g. 0.1 for a 10% grade, negative for downhill.
        A leg whose distance_from_start doesn't increase (a zero-distance
        stop leg) yields 0.0 rather than dividing by zero.

    Raises:
        ValueError: If window_m is negative.
    """
    if window_m < 0:
        raise ValueError(f"window_m must not be negative, got {window_m}.")

    distances = [p.distance_from_start for p in track.points]
    elevations = [p.elevation for p in track.points]
    half_window = window_m / 2

    gradients = []
    for leg_start, leg_end in zip(distances, distances[1:]):
        if leg_end <= leg_start:
            gradients.append(0.0)
            continue
        middle = (leg_start + leg_end) / 2
        # Widened to cover the leg itself, then clamped to the track's extent.
        window_low = max(distances[0], min(leg_start, middle - half_window))
        window_high = min(distances[-1], max(leg_end, middle + half_window))
        rise = (
            _elevation_at(distances, elevations, window_high, last_at_distance=False)
            - _elevation_at(distances, elevations, window_low, last_at_distance=True)
        )
        gradients.append(rise / (window_high - window_low))
    return gradients


def _tobler_shape(gradient: float) -> float:
    """Unnormalized Tobler exponential term, for any gradient — smooth and always positive.
    Args:
        gradient: Rise/run ratio for one leg, e.g. 0.1 for a 10% grade.
    Returns:
        The exponential term of Tobler's hiking function. Normally you'd multiply this by 6 km/h
        to get absolute speed, but here it's used only for its shape and normalized against its
        own flat-ground value.
    """
    return math.exp(-_TOBLER_SLOPE_FACTOR * abs(gradient + _TOBLER_SLOPE_OFFSET))


_TOBLER_FLAT_SHAPE = _tobler_shape(0.0)


def tobler_speeds_from_gradients(gradients: list[float]) -> list[float]:
    """Per-leg Tobler hiking-function speeds for already-calculated gradients.

    Implements the shape of Tobler's (1993) hiking function,
    speed_kmh = 6 * exp(-3.5 * |gradient + 0.05|), which peaks on a gentle
    downhill (around -5% grade) and falls off for steeper climbs or descents,
    normalized so flat ground is 1.0 — matching minetti_speeds_from_gradients'
    reference, since combine.py rescales either model to its segment's
    duration anyway.

    Args:
        gradients: Per-leg gradients, e.g. from calculate_gradient.

    Returns:
        One speed per gradient, as a multiple of flat-ground speed.
    """
    def _tobler_relative_speed(gradient: float) -> float:
        """Tobler's hiking-function speed as a multiple of its own flat-ground speed."""
        return _tobler_shape(gradient) / _TOBLER_FLAT_SHAPE

    return [_tobler_relative_speed(gradient) for gradient in gradients]


def _minetti_cost(gradient: float) -> float:
    """Minetti et al. (2002) energy-cost polynomial C(g) in J/(kg·m), for gradient g within its calibrated domain.
    Returns:
        The cost of moving one kilogram of body mass one meter along a slope with the given gradient, which is
        inverse of speed.
    """
    return (
        155.4 * (gradient ** 5)
        - 30.4 * (gradient ** 4)
        - 43.3 * (gradient ** 3)
        + 46.3 * (gradient ** 2)
        + 19.5 * gradient
        + 3.6
    )


_MINETTI_FLAT_COST = _minetti_cost(0.0)


def _raw_minetti_relative_speed(gradient: float) -> float:
    """Un-softened Minetti speed as a multiple of flat-ground speed (C(0) / C(g),
    because the cost is inversely related to the speed).

    Beyond _MINETTI_MAX_GRADIENT, follows Tobler's curve instead, scaled to
    match Minetti's own value at the boundary — continuous there, and still
    decreasing (never pinned to one value) as the grade keeps steepening.

    Args:
        gradient: Rise/run ratio for one leg, e.g. 0.1 for a 10% grade.

    Returns:
        Relative speed, or 0.0 if the cost is somehow non-positive within
        the domain (defensive only; it isn't for any gradient in [-0.45, 0.45]).
    """
    if abs(gradient) <= _MINETTI_MAX_GRADIENT:
        cost = _minetti_cost(gradient)
        return _MINETTI_FLAT_COST / cost if cost > 0 else 0.0
    boundary = math.copysign(_MINETTI_MAX_GRADIENT, gradient)
    boundary_speed = _MINETTI_FLAT_COST / _minetti_cost(boundary)
    # Multiply the boundary speed by Tobler's shape ratio between the actual gradient
    # and the boundary, so the curve continues to fall off beyond the calibrated range.
    return boundary_speed * _tobler_shape(gradient) / _tobler_shape(boundary)


def minetti_speeds_from_gradients(
    gradients: list[float],
    uphill_exponent: float = MINETTI_UPHILL_EXPONENT,
    downhill_exponent: float = MINETTI_DOWNHILL_EXPONENT,
) -> list[float]:
    """Per-leg Minetti-based speeds for already-calculated gradients.

    Each leg's speed is (C(0) / C(g)) ** exponent: Minetti et al.'s (2002)
    energy cost C(g) inverted to a speed relative to flat ground, softened by
    the uphill or downhill exponent (see MINETTI_UPHILL_EXPONENT). Flat ground
    is always exactly 1.0, whatever the exponents, and both exponents at 1.0
    give the pure inverted cost curve. Absolute speed is intentionally
    relative because final time scaling is handled in combine.py.

    Args:
        gradients: Per-leg gradients, e.g. from calculate_gradient.
        uphill_exponent: Softening applied to legs with a positive gradient.
        downhill_exponent: Softening applied to legs with a negative gradient.

    Returns:
        One speed per gradient, as a multiple of flat-ground speed.

    Raises:
        ValueError: If either exponent isn't positive.
    """
    if uphill_exponent <= 0 or downhill_exponent <= 0:
        raise ValueError(
            f"Minetti exponents must be positive, got uphill={uphill_exponent}, downhill={downhill_exponent}."
        )
    # gradient == 0 takes the downhill branch, but the raw speed there is
    # exactly 1.0, so either exponent leaves it at 1.0.
    speeds = []
    for gradient in gradients:
        exponent = uphill_exponent if gradient > 0 else downhill_exponent
        speeds.append(_raw_minetti_relative_speed(gradient) ** exponent)
    return speeds


# TODO: Tobler has no tuning knob of its own the way Minetti has its
# exponents. Now that both curves share a flat-ground reference they can be
# compared directly: across ±30% grade Tobler spans 0.35-1.19 (3.4x) and the
# softened Minetti 0.39-1.42 (3.6x), so hiking isn't currently the flatter
# of the two despite being the slower activity. An exponent on Tobler (or a
# smaller _TOBLER_SLOPE_FACTOR) is the knob to add if that needs fixing.

# keep an eye on Tobler, we might want to tune it to our liking.