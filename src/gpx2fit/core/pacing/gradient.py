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
from dataclasses import dataclass

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

# A window that straddles a hilltop or valley floor averages the climb
# against the descent and reads the crest as flat — always biased toward 0%,
# never away from it. So the route is split into climbs and descents at its
# turning points, and near one a leg's window shrinks, still centred on the
# leg, until it ends at the turning point.

# A turning point only counts once the route has moved this far up or down from it.
# This is a hysteresis to ignore small wiggles in the elevation data.
TURNING_POINT_MIN_RISE_M = 5.0

# The shrinking stops here, about the resolution of the DEM data behind most
# GPX elevation: 1 m of rounding over a shorter window is a bigger error than
# the crest bias it would remove. Within half of this of a turning point the
# window keeps this length and slides back from the crest instead.
TURNING_POINT_MIN_WINDOW_M = 30.0

# Minetti's polynomial is only calibrated within roughly this gradient
# range; beyond it the un-clamped quintic turns back upward instead of
# decreasing. Rather than clamp the gradient itself (which would pin every
# leg steeper than this boundary to one identical speed), legs beyond this
# range follow a scaled Tobler curve instead — see _raw_minetti_relative_speed.
_MINETTI_MAX_GRADIENT = 0.45

# Extra cost per unit of descent added to Minetti's polynomial — see
# _minetti_cost. It decides where descents turn slower than flat: at 10
# (Strava's HR-based GAP) the curve peaks at -9% and crosses flat at -17%; at 20
# it is slower than flat on every descent, which is what the tuning corpus fits
# best (2026-09-29). The fills only scale that shape, they can't move the crossing.
MINETTI_DOWNHILL_COST_SLOPE = 20.0

# Minetti's C(g) is an energy cost, so 1/cost swings harder than real pace
# does (a 20% climb at 0.4x flat speed) — scaled to a slow real-world
# activity, that pushes climbs below Strava's "resting" threshold (their time
# vanishes from moving time). So relative speed is raised to these exponents
# instead: 1.0 is the raw curve, lower is flatter. With these values a 10%
# climb runs at ~0.74x flat speed, a 20% climb at ~0.58x, a 10% descent at
# ~0.93x and a 20% descent at ~0.79x (TestMinettiSoftening in test_gradient.py
# pins these bands). Paced output doesn't use these defaults:
# curve_selection.resolve_curve_shape fits both exponents per workout, and
# these only survive as the defaults of resolve_tobler_weight's probe.
MINETTI_UPHILL_EXPONENT = 0.6
MINETTI_DOWNHILL_EXPONENT = 0.5


@dataclass(frozen=True)
class CurveExponents:
    """Softening applied to one speed curve: each leg's relative speed ** exponent.
    1.0 is the raw curve, lower is flatter; flat ground stays exactly 1.0 either way.

    Attributes:
        uphill: Exponent for legs with a positive gradient.
        downhill: Exponent for every other leg.
    """
    uphill: float
    downhill: float


@dataclass(frozen=True)
class CurveShape:
    """The exponents both speed curves are paced with — see blended_speeds_from_gradients."""
    minetti: CurveExponents
    tobler: CurveExponents


# Softened Minetti and raw Tobler: the shape used when nothing else decides it.
DEFAULT_CURVE_SHAPE = CurveShape(
    minetti=CurveExponents(uphill=MINETTI_UPHILL_EXPONENT, downhill=MINETTI_DOWNHILL_EXPONENT),
    tobler=CurveExponents(uphill=1.0, downhill=1.0),
)


def _soften(
    raw_speeds: list[float],
    gradients: list[float],
    uphill_exponent: float,
    downhill_exponent: float,
    curve_name: str,
) -> list[float]:
    """Raise each raw relative speed to the uphill or downhill exponent, by its leg's gradient.
    gradient == 0 takes the downhill branch, but the raw speed there is
    exactly 1.0 for both curves, so either exponent leaves it at 1.0.

    Args:
        raw_speeds: Per-leg relative speeds
        gradients: Per-leg gradients
        uphill_exponent: Softening applied to legs with a positive gradient.
        downhill_exponent: Softening applied to every other leg.
        curve_name: Name of the curve being processed.
    Returns:
        A list of softened speeds.
    Raises:
        ValueError: If either exponent isn't positive.
    """
    if uphill_exponent <= 0 or downhill_exponent <= 0:
        raise ValueError(
            f"{curve_name} exponents must be positive, got uphill={uphill_exponent}, downhill={downhill_exponent}."
        )
    return [
        speed ** (uphill_exponent if gradient > 0 else downhill_exponent)
        for speed, gradient in zip(raw_speeds, gradients)
    ]


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


def _turning_points(elevations: list[float], min_rise: float) -> list[int]:
    """Indexes of the route's hilltops and valley floors, in order.

    A zigzag with hysteresis: the lowest (or highest) point seen since the
    last turning point becomes one only once the route has climbed (or
    descended) at least `min_rise` back from it, so wiggles smaller than
    that never split a run. Consecutive turning points therefore alternate
    between peak and valley, each at least `min_rise` from the last. The
    track's own first and last points are never returned.

    Where the extreme is a flat top (several points at the same elevation,
    common with whole-metre DEM data), the middle of them is used, so the
    flat is shared between the climb and the descent either side of it.

    Args:
        elevations: Elevation per point, in route order.
        min_rise: Metres the route must move back from an extreme to confirm it.
    Returns:
        Point indexes, strictly increasing.
    """
    turns = []
    direction = 0  # +1 climbing, -1 descending, 0 not yet known
    # First and last index of the lowest/highest elevation since the last turning point.
    low_first = low_last = high_first = high_last = 0
    for index in range(1, len(elevations)):
        elevation = elevations[index]
        if elevation > elevations[high_first]:
            high_first = high_last = index
        elif elevation == elevations[high_first]:
            high_last = index
        if elevation < elevations[low_first]:
            low_first = low_last = index
        elif elevation == elevations[low_first]:
            low_last = index

        if direction <= 0 and elevation - elevations[low_first] >= min_rise:
            if direction < 0:
                turns.append((low_first + low_last) // 2)
            direction = 1
            high_first = high_last = index
        elif direction >= 0 and elevations[high_first] - elevation >= min_rise:
            if direction > 0:
                turns.append((high_first + high_last) // 2)
            direction = -1
            low_first = low_last = index
    return turns


def calculate_gradient(track: Track, window_m: float = GRADIENT_WINDOW_M) -> list[float]:
    """Calculate each leg's gradient, averaged over a distance window around the leg.

    The window is centred on the leg, but never crosses a turning point
    (see TURNING_POINT_MIN_RISE_M) or the track's ends. Near a turning point
    it shrinks symmetrically to end there, down to TURNING_POINT_MIN_WINDOW_M;
    past that floor, and at the track's ends, it keeps its length and slides
    back inside the leg's own climb or descent. It is only shorter than that
    where the whole run is.

    Args:
        track: Track whose points already have elevation and distance_from_start
            set, with distance_from_start non-decreasing.
        window_m: Route distance, in meters, the gradient is averaged over (see
            GRADIENT_WINDOW_M). The window is widened to cover the leg itself
            if the leg is longer. 0 gives plain point-to-point rise/run.

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
    # Point indexes where each climb or descent ends; every leg lies inside exactly one run.
    run_ends = _turning_points(elevations, TURNING_POINT_MIN_RISE_M) + [len(distances) - 1]
    run = 0
    run_low = distances[0] if distances else 0.0

    gradients = []
    for leg_index, (leg_start, leg_end) in enumerate(zip(distances, distances[1:])):
        if leg_index == run_ends[run]:
            run_low = distances[leg_index]
            run += 1
        run_high = distances[run_ends[run]]
        if leg_end <= leg_start:
            gradients.append(0.0)
            continue
        middle = (leg_start + leg_end) / 2
        full_half = max(window_m, leg_end - leg_start) / 2
        # Room on each side before a turning point; the track's own ends aren't one.
        # The leg lies inside its run, so this never shrinks below the leg itself.
        room_low = middle - run_low if run > 0 else math.inf
        room_high = run_high - middle if run < len(run_ends) - 1 else math.inf
        half_window = min(full_half, max(min(room_low, room_high), TURNING_POINT_MIN_WINDOW_M / 2))
        # Then slid back inside the run rather than cut short at its edge.
        window_low = middle - half_window
        window_high = middle + half_window
        # can happen within TURNING_POINT_MIN_WINDOW_M / 2 of a turning point, or at the track's start
        if window_low < run_low:
            window_high = min(run_high, window_high + (run_low - window_low))
            window_low = run_low
        elif window_high > run_high:
            window_low = max(run_low, window_low - (window_high - run_high))
            window_high = run_high
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


def tobler_speeds_from_gradients(
    gradients: list[float],
    uphill_exponent: float = 1.0,
    downhill_exponent: float = 1.0,
) -> list[float]:
    """Per-leg Tobler hiking-function speeds for already-calculated gradients.

    Implements the shape of Tobler's (1993) hiking function,
    speed_kmh = 6 * exp(-3.5 * |gradient + 0.05|), which peaks on a gentle
    downhill (around -5% grade) and falls off for steeper climbs or descents,
    normalized so flat ground is 1.0 — matching minetti_speeds_from_gradients'
    reference, since combine.py rescales either model to its segment's
    duration anyway. Softened by the exponents the same way Minetti is;
    because the curve is an exponential, an exponent is equivalent to scaling
    its 3.5 slope factor.

    Args:
        gradients: Per-leg gradients, e.g. from calculate_gradient.
        uphill_exponent: Softening applied to legs with a positive gradient.
            1.0 (the default) is Tobler's own curve.
        downhill_exponent: Softening applied to every other leg.
    Returns:
        One speed per gradient, as a multiple of flat-ground speed.
    Raises:
        ValueError: If either exponent isn't positive.
    """
    raw_speeds = [_tobler_shape(gradient) / _TOBLER_FLAT_SHAPE for gradient in gradients]
    return _soften(raw_speeds, gradients, uphill_exponent, downhill_exponent, "Tobler")


def _minetti_cost(gradient: float, downhill_cost_slope: float) -> float:
    """Minetti et al. (2002) energy-cost polynomial C(g) in J/(kg·m), for gradient g within its calibrated domain,
    plus downhill_cost_slope * |g| on descents (see MINETTI_DOWNHILL_COST_SLOPE).

    Minetti's cost keeps falling down to about -18%, so its inverse has descents
    at twice flat speed. Nobody runs downhill at a constant metabolic cost, so
    that shape can't be fixed by softening, which scales the curve but keeps the
    fastest point at -18%. The linear term moves it. At a slope of 10 it
    matches Strava's heart-rate-based grade-adjusted pace (reverse-engineered by
    Aaron Schroeder's `specialsauce`) within ~0.01 from -30% to 0%: fastest at
    -8 to -10% and 1.14x flat, back to flat speed by about -18%. Above 19.5 (the
    polynomial's own slope at 0%) every descent is slower than flat, which is
    what the tuning corpus shows. Climbs are left as Minetti's.
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
        + downhill_cost_slope * max(0.0, -gradient)
    )


# The descent term is zero on the flat, so this holds for every slope.
_MINETTI_FLAT_COST = _minetti_cost(0.0, MINETTI_DOWNHILL_COST_SLOPE)


def _raw_minetti_relative_speed(gradient: float, downhill_cost_slope: float) -> float:
    """Un-softened Minetti speed as a multiple of flat-ground speed (C(0) / C(g),
    because the cost is inversely related to the speed).

    Beyond _MINETTI_MAX_GRADIENT, follows Tobler's curve instead, scaled to
    match Minetti's own value at the boundary — continuous there, and still
    decreasing (never pinned to one value) as the grade keeps steepening.

    Args:
        gradient: Rise/run ratio for one leg, e.g. 0.1 for a 10% grade.
        downhill_cost_slope: The descent term of _minetti_cost.
    Returns:
        Relative speed, or 0.0 if the cost is somehow non-positive within
        the domain (defensive only; it isn't for any gradient in [-0.45, 0.45]).
    """
    if abs(gradient) <= _MINETTI_MAX_GRADIENT:
        cost = _minetti_cost(gradient, downhill_cost_slope)
        return _MINETTI_FLAT_COST / cost if cost > 0 else 0.0
    boundary = math.copysign(_MINETTI_MAX_GRADIENT, gradient)
    boundary_speed = _MINETTI_FLAT_COST / _minetti_cost(boundary, downhill_cost_slope)
    # Multiply the boundary speed by Tobler's shape ratio between the actual gradient
    # and the boundary, so the curve continues to fall off beyond the calibrated range.
    return boundary_speed * _tobler_shape(gradient) / _tobler_shape(boundary)


def minetti_speeds_from_gradients(
    gradients: list[float],
    uphill_exponent: float = MINETTI_UPHILL_EXPONENT,
    downhill_exponent: float = MINETTI_DOWNHILL_EXPONENT,
    downhill_cost_slope: float = MINETTI_DOWNHILL_COST_SLOPE,
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
        downhill_cost_slope: Extra cost per unit of descent, which sets where
            descents turn slower than flat (see MINETTI_DOWNHILL_COST_SLOPE).
            The app always uses the default; the tuning harness varies it.
    Returns:
        One speed per gradient, as a multiple of flat-ground speed.
    Raises:
        ValueError: If either exponent isn't positive, or downhill_cost_slope is negative.
    """
    if downhill_cost_slope < 0:
        raise ValueError(f"downhill_cost_slope must not be negative, got {downhill_cost_slope}.")
    raw_speeds = [_raw_minetti_relative_speed(gradient, downhill_cost_slope) for gradient in gradients]
    return _soften(raw_speeds, gradients, uphill_exponent, downhill_exponent, "Minetti")


def blended_speeds_from_gradients(
    gradients: list[float],
    tobler_weight: float,
    shape: CurveShape = DEFAULT_CURVE_SHAPE,
) -> list[float]:
    """Per-leg speeds from a geometric blend of the Minetti and Tobler curves.

    Each leg's speed is ``minetti ** (1 - tobler_weight) * tobler ** tobler_weight``,
    i.e. straight-line interpolation in log-space — the same space combine.py's
    speed compression works in, so a half-and-half blend lands on the geometric
    mean of the two curves rather than being dragged toward whichever one
    happens to be larger. Both curves are normalized to flat ground, so every
    blend is exactly 1.0 there too.
    The weight is resolved once per workout by pacing.curve_selection.resolve_tobler_weight,
    and the shape by pacing.curve_selection.resolve_curve_shape.

    Args:
        gradients: Per-leg gradients, e.g. from calculate_gradient.
        tobler_weight: 0.0 for pure Minetti, 1.0 for pure Tobler, anything in
            between for a blend. Must be within [0.0, 1.0].
        shape: The exponents each curve is softened with.
    Returns:
        One speed per gradient, as a multiple of flat-ground speed.
    Raises:
        ValueError: If tobler_weight isn't within [0.0, 1.0], or an exponent isn't positive.
    """
    if not 0.0 <= tobler_weight <= 1.0:
        raise ValueError(f"tobler_weight must be within [0.0, 1.0], got {tobler_weight}.")

    def minetti() -> list[float]:
        return minetti_speeds_from_gradients(gradients, shape.minetti.uphill, shape.minetti.downhill)

    def tobler() -> list[float]:
        return tobler_speeds_from_gradients(gradients, shape.tobler.uphill, shape.tobler.downhill)

    if tobler_weight == 0.0:
        return minetti()
    if tobler_weight == 1.0:
        return tobler()

    return [
        minetti_speed ** (1.0 - tobler_weight) * tobler_speed ** tobler_weight
        for minetti_speed, tobler_speed in zip(minetti(), tobler())
    ]