"""The pacing model with its constants as arguments instead of module globals.

`core/`'s tunable constants are module-level names with no injection point, and
`gradient.py` snapshots `_TOBLER_FLAT_SHAPE` / `_MINETTI_FLAT_COST` at import —
so monkeypatching them is both invasive and subtly wrong (a patched slope factor
would never reach the snapshot). Instead, this module restates the same
arithmetic with every constant as a parameter, calling `core/`'s public curve
functions to do the actual work. `core/` is never modified or reached into.

The obvious risk is that this drifts from `combine()` after a later refactor and
the harness starts tuning a model the app doesn't run.
`tests/tuning/test_model_mirror.py` is the guard: it asserts `predict_elapsed`
at default `PacingParams` reproduces `combine()`'s timestamps exactly.
"""

import math
import statistics
from dataclasses import dataclass

from gpx2fit.core.models import SportType, Track
from gpx2fit.core.pacing.curve_selection import (
    CURVE_REFERENCE_GRADE,
    DOWNHILL_FILL,
    FLAT_EQUIVALENT_BAND_MPS,
    HILLY_VERTICALITY,
    HILLY_VERTICALITY_BAND,
    MAX_SPEED_RATIO_BOUNDS,
    TOBLER_THRESHOLDS,
    UPHILL_FILL,
    VERTICALITY_BAND,
)
from gpx2fit.core.pacing.gradient import (
    GRADIENT_WINDOW_M,
    CurveExponents,
    CurveShape,
    blended_speeds_from_gradients,
    calculate_gradient,
    minetti_speeds_from_gradients,
    tobler_speeds_from_gradients,
)

# combine._MIN_SURFACE_MULTIPLIER, restated so a surface multiplier can never
# zero out a leg. Unused until the surface phase, but the arithmetic has to
# match now or predict_elapsed would silently diverge once multipliers arrive.
_MIN_SURFACE_MULTIPLIER = 0.05


@dataclass(frozen=True)
class PacingParams:
    """Every constant the pacing model is being calibrated on, as one value.

    Defaults are exactly today's shipped constants, so `PacingParams()` means
    "the model as it stands" and any tuned result is a diff against that.

    Attributes:
        gradient_window_m: gradient.GRADIENT_WINDOW_M.
        curve_reference_grade: curve_selection.CURVE_REFERENCE_GRADE.
        uphill_fill: curve_selection.UPHILL_FILL.
        downhill_fill: curve_selection.DOWNHILL_FILL.
        ratio_flat: Overrides MAX_SPEED_RATIO_BOUNDS[sport].flat when set.
        ratio_hilly: Overrides MAX_SPEED_RATIO_BOUNDS[sport].hilly when set.
        hilly_verticality: curve_selection.HILLY_VERTICALITY.
        hilly_verticality_band: curve_selection.HILLY_VERTICALITY_BAND.
        tobler_flat_equivalent_mps: Overrides TOBLER_THRESHOLDS[sport]
            .flat_equivalent_mps when set. Phase two.
        tobler_verticality: Overrides TOBLER_THRESHOLDS[sport].verticality
            when set. Phase two.
        flat_equivalent_band_mps: curve_selection.FLAT_EQUIVALENT_BAND_MPS.
        verticality_band: curve_selection.VERTICALITY_BAND.
        tobler_weight: Pins the Minetti/Tobler blend instead of resolving it,
            which is how stage one sweeps an activity's empirical optimum.
        max_speed_ratio: Pins the speed-swing bound the same way.
    """
    gradient_window_m: float = GRADIENT_WINDOW_M
    curve_reference_grade: float = CURVE_REFERENCE_GRADE
    uphill_fill: float = UPHILL_FILL
    downhill_fill: float = DOWNHILL_FILL
    ratio_flat: float | None = None
    ratio_hilly: float | None = None
    hilly_verticality: float = HILLY_VERTICALITY
    hilly_verticality_band: float = HILLY_VERTICALITY_BAND
    tobler_flat_equivalent_mps: float | None = None
    tobler_verticality: float | None = None
    flat_equivalent_band_mps: float = FLAT_EQUIVALENT_BAND_MPS
    verticality_band: float = VERTICALITY_BAND
    tobler_weight: float | None = None
    max_speed_ratio: float | None = None


@dataclass(frozen=True)
class ResolvedSettings:
    """What the model decided for one workout, plus the features it decided from.

    The features are what stage two regresses the constants against, so they're
    reported rather than recomputed.

    Attributes:
        tobler_weight: The Minetti/Tobler blend, 0.0 pure Minetti to 1.0 pure Tobler.
        max_speed_ratio: The soft bound on a leg's swing from the segment median.
        curve_shape: Both curves' fitted exponents.
        verticality: Distance-weighted mean |gradient| — climb and descent per
            metre traveled.
        flat_equivalent_mps: What flat-ground speed would have produced this
            activity's moving time over this terrain.
    """
    tobler_weight: float
    max_speed_ratio: float
    curve_shape: CurveShape
    verticality: float
    flat_equivalent_mps: float


def _smoothstep(value: float, center: float, half_width: float) -> float:
    """curve_selection._smoothstep: ramp 0.0 to 1.0 across `center` +/- `half_width`."""
    position = min(1.0, max(0.0, (value - center) / (2 * half_width) + 0.5))
    return position * position * (3 - 2 * position)


def _compress_speed_toward_typical(speed: float, typical_speed: float, max_ratio: float) -> float:
    """combine._compress_speed_toward_typical: soft-bound `speed` within `max_ratio` of typical."""
    log_limit = math.log(max_ratio)
    log_ratio = math.log(speed / typical_speed)
    return typical_speed * math.exp(log_limit * math.tanh(log_ratio / log_limit))


def verticality(gradients: list[float], leg_distances: list[float]) -> float:
    """curve_selection._verticality: distance-weighted mean |gradient|.

    Returns 0.0 for a track with no distance, where core's version would
    divide by zero — the callers below guard that case anyway, but a feature
    that's reported in every table shouldn't be able to raise.
    """
    total_distance = sum(leg_distances)
    if total_distance <= 0:
        return 0.0
    return sum(abs(gradient) * distance for gradient, distance in zip(gradients, leg_distances)) / total_distance


def flat_equivalent_mps(gradients: list[float], leg_distances: list[float], active_seconds: float) -> float:
    """The flat-ground speed that would have produced this moving time over this terrain.

    `resolve_tobler_weight`'s first criterion, lifted out so it can be reported
    as a feature. Dividing each leg's distance by its relative Minetti speed
    gives a flat-equivalent distance — the curve is dimensionless. So the sum
    is metres, not seconds — and dividing that by the moving time answers "how
    fast was this really, with the terrain divided out?".

    Uses `minetti_speeds_from_gradients`'s own default exponents, not the
    resolved curve shape, exactly as core does: Minetti is only a difficulty
    normalizer here, so the answer must not depend on the bound being fitted.
    """
    if active_seconds <= 0:
        return 0.0
    probe_speeds = minetti_speeds_from_gradients(gradients)
    equivalent_distance = sum(
        distance / speed
        for distance, speed in zip(leg_distances, probe_speeds)
        if distance > 0 and speed > 0
    )
    return equivalent_distance / active_seconds


def tobler_weight_for(
    gradients: list[float],
    leg_distances: list[float],
    active_seconds: float,
    sport: SportType,
    params: PacingParams,
) -> float:
    """curve_selection.resolve_tobler_weight, with its thresholds as parameters."""
    if params.tobler_weight is not None:
        return params.tobler_weight

    thresholds = TOBLER_THRESHOLDS[sport]
    flat_threshold = (
        params.tobler_flat_equivalent_mps
        if params.tobler_flat_equivalent_mps is not None
        else thresholds.flat_equivalent_mps
    )
    steep_threshold = (
        params.tobler_verticality if params.tobler_verticality is not None else thresholds.verticality
    )
    default_weight = 1.0 if sport == SportType.HIKING else 0.0

    if sum(leg_distances) <= 0 or active_seconds <= 0:
        return default_weight

    equivalent = flat_equivalent_mps(gradients, leg_distances, active_seconds)
    if equivalent <= 0:
        return default_weight

    slow_weight = 1.0 - _smoothstep(equivalent, flat_threshold, params.flat_equivalent_band_mps)
    steep_weight = _smoothstep(verticality(gradients, leg_distances), steep_threshold, params.verticality_band)
    return max(slow_weight, steep_weight)


def max_speed_ratio_for(
    gradients: list[float],
    leg_distances: list[float],
    sport: SportType,
    params: PacingParams,
) -> float:
    """curve_selection.resolve_max_speed_ratio, with its bounds as parameters.

    Smoothness is deliberately absent: it's a user-facing preference that
    scales whatever this returns, not something to calibrate from recordings.
    The harness always works at DEFAULT_SMOOTHNESS, whose scale is 1.0.
    """
    if params.max_speed_ratio is not None:
        return params.max_speed_ratio

    bounds = MAX_SPEED_RATIO_BOUNDS[sport]
    flat = params.ratio_flat if params.ratio_flat is not None else bounds.flat
    hilly = params.ratio_hilly if params.ratio_hilly is not None else bounds.hilly

    if sum(leg_distances) <= 0:
        return flat

    hilliness = _smoothstep(
        verticality(gradients, leg_distances), params.hilly_verticality, params.hilly_verticality_band
    )
    return flat + (hilly - flat) * hilliness


def curve_shape_for(max_speed_ratio: float, params: PacingParams) -> CurveShape:
    """curve_selection.resolve_curve_shape, with the reference grade and fills as parameters."""
    log_limit = math.log(max_speed_ratio)
    reference = [params.curve_reference_grade, -params.curve_reference_grade]
    minetti_up, minetti_down = minetti_speeds_from_gradients(reference, uphill_exponent=1.0, downhill_exponent=1.0)
    tobler_up, tobler_down = tobler_speeds_from_gradients(reference, uphill_exponent=1.0, downhill_exponent=1.0)

    def fitted(raw_uphill: float, raw_downhill: float) -> CurveExponents:
        return CurveExponents(
            uphill=params.uphill_fill * log_limit / abs(math.log(raw_uphill)),
            downhill=params.downhill_fill * log_limit / abs(math.log(raw_downhill)),
        )

    return CurveShape(minetti=fitted(minetti_up, minetti_down), tobler=fitted(tobler_up, tobler_down))


def leg_distances_of(track: Track) -> list[float]:
    """Per-leg distances, exactly as combine() derives them."""
    return [
        later.distance_from_start - earlier.distance_from_start
        for earlier, later in zip(track.points, track.points[1:])
    ]


def resolve(
    track: Track,
    sport: SportType,
    active_seconds: float,
    params: PacingParams,
    gradients: list[float] | None = None,
) -> ResolvedSettings:
    """Resolve the three per-workout decisions combine() makes, plus the features behind them.

    Args:
        track: The route.
        sport: Picks the per-sport constants.
        active_seconds: Moving time.
        params: The constants to resolve with.
        gradients: Already-computed gradients for this track and window, if the
            caller has them. A parameter sweep resolves the same track
            thousands of times, and the gradients don't depend on any parameter
            being swept — only on `gradient_window_m`, which the caller is
            responsible for having matched.
    """
    if gradients is None:
        gradients = calculate_gradient(track, params.gradient_window_m)
    distances = leg_distances_of(track)
    weight = tobler_weight_for(gradients, distances, active_seconds, sport, params)
    ratio = max_speed_ratio_for(gradients, distances, sport, params)
    return ResolvedSettings(
        tobler_weight=weight,
        max_speed_ratio=ratio,
        curve_shape=curve_shape_for(ratio, params),
        verticality=verticality(gradients, distances),
        flat_equivalent_mps=flat_equivalent_mps(gradients, distances, active_seconds),
    )


def leg_speeds(
    track: Track,
    sport: SportType,
    active_seconds: float,
    params: PacingParams,
    multipliers: list[float] | None = None,
    gradients: list[float] | None = None,
) -> tuple[list[float], ResolvedSettings]:
    """The model's per-leg relative speeds, after blending, surface and compression.

    This is `_pace_segment` steps 4-6 for the single-segment case the harness
    uses (start and end are the only anchors, which is what the converter gets
    when the user gives just a start time and a duration). Relative, not m/s:
    the absolute scale comes from `active_seconds` in `predict_elapsed`.

    `gradients` may be passed in when the caller already has them for this
    track and window — see `resolve`.
    """
    if gradients is None:
        gradients = calculate_gradient(track, params.gradient_window_m)
    settings = resolve(track, sport, active_seconds, params, gradients)
    speeds = blended_speeds_from_gradients(gradients, settings.tobler_weight, settings.curve_shape)

    if multipliers is not None:
        if len(multipliers) != len(speeds):
            raise ValueError(f"Expected {len(speeds)} multipliers, got {len(multipliers)}.")
        speeds = [
            speed * max(multiplier, _MIN_SURFACE_MULTIPLIER)
            for speed, multiplier in zip(speeds, multipliers)
        ]

    positive_speeds = [speed for speed in speeds if speed > 0]
    if positive_speeds:
        typical_speed = statistics.median(positive_speeds)
        speeds = [
            _compress_speed_toward_typical(speed, typical_speed, settings.max_speed_ratio) if speed > 0 else speed
            for speed in speeds
        ]

    return speeds, settings


def predict_elapsed(
    track: Track,
    sport: SportType,
    active_seconds: float,
    params: PacingParams,
    multipliers: list[float] | None = None,
    gradients: list[float] | None = None,
) -> tuple[list[float], ResolvedSettings]:
    """Predicted elapsed seconds at every track point, starting at 0.0.

    `_pace_segment` steps 4-9 for a single segment: model each leg's time from
    its relative speed, then scale all of them by one factor so the total comes
    to `active_seconds` exactly. That single rescale is why only the *shape* of
    this curve is a prediction — the total is matched by construction, and no
    parameter can change it.

    Args:
        track: A track with `distance_from_start` set on every point.
        sport: Picks the per-sport constants.
        active_seconds: The activity's moving time, which the result sums to.
        params: The constants to run with.
        multipliers: Optional per-leg surface multipliers, one per leg.
        gradients: Already-computed gradients for this track and window, if the
            caller has them — see `resolve`.

    Returns:
        The cumulative seconds per point (length equal to `track.points`), and
        the settings the model resolved for this workout.

    Raises:
        ValueError: If the track has fewer than two points, `active_seconds`
            isn't positive, or the multipliers don't match the leg count.
    """
    if len(track.points) < 2:
        raise ValueError(f"Need at least 2 points to pace, got {len(track.points)}.")
    if active_seconds <= 0:
        raise ValueError(f"active_seconds must be positive, got {active_seconds}.")

    speeds, settings = leg_speeds(track, sport, active_seconds, params, multipliers, gradients)
    distances = leg_distances_of(track)
    modeled = [
        (distance / speed) if speed > 0 else 0.0
        for distance, speed in zip(distances, speeds)
    ]
    modeled_total = sum(modeled)
    if modeled_total <= 0:
        raise ValueError("The model gave this track zero total time; it has no positive-distance legs.")

    scale = active_seconds / modeled_total
    elapsed = [0.0]
    for leg_time in modeled:
        elapsed.append(elapsed[-1] + leg_time * scale)
    return elapsed, settings

# todo: compare this file to the real files it mirrors and make sure it still matches, also read the test file for this one.