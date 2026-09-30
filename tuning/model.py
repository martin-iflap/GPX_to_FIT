"""The pacing model with its constants as arguments instead of module globals.

`core/`'s tunable constants are module-level names with no injection point, and
`gradient.py` snapshots `_TOBLER_FLAT_SHAPE` / `_MINETTI_FLAT_COST` at import —
so monkeypatching them is both invasive and subtly wrong (a patched slope factor
would never reach the snapshot). The one exception is Minetti's descent cost
slope: it lives inside the curve, so `minetti_speeds_from_gradients` takes it as
an argument (`downhill_cost_slope`) rather than have the harness restate the curve.

What this module restates is exactly what can't be borrowed:

- **The three per-workout decisions** (`tobler_weight_for`,
  `max_speed_ratio_for`, `curve_shape_for`). Their constants *are* the knobs
  being fitted, and core reads them as globals, so a core call would always
  answer with the shipped values. The helpers underneath them — `_smoothstep`,
  `_verticality`, the curve functions — are core's own, imported.
- **The per-leg arithmetic** (softening, blending, compression, scaling), in
  numpy. A fit evaluates these thousands of times over the same tracks, where
  `core/` runs it once per conversion and has to stay pure Python for Pyodide.

Nothing about the *curves* is restated: `TrackModel` calls `core/`'s own
`minetti_speeds_from_gradients` / `tobler_speeds_from_gradients` once per
track (and Minetti once per descent cost slope), at exponent 1.0, and every
later evaluation only softens, blends and
scales those raw curves. Softening is `raw ** exponent`, which is exactly what
`gradient._soften` does, so a change to the Minetti polynomial or Tobler's
slope factor still reaches the harness without anything here being touched.

The obvious risk is that the restated parts drift from `combine()` after a
later refactor and the harness starts tuning a model the app doesn't run.
`tests/tuning/test_model_mirror.py` is the guard: it asserts
`elapsed_from_model` at default `PacingParams` reproduces `combine()`'s
timestamps exactly.
"""

import functools
import math
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace

import numpy as np

from gpx2fit.core.models import SportType, Track
from gpx2fit.core.pacing.combine import _MIN_SURFACE_MULTIPLIER
from gpx2fit.core.pacing.curve_selection import (
    CURVE_FILLS,
    CURVE_REFERENCE_GRADE,
    FLAT_EQUIVALENT_BAND_MPS,
    HILLY_VERTICALITY,
    HILLY_VERTICALITY_BAND,
    MAX_SPEED_RATIO_BOUNDS,
    TOBLER_THRESHOLDS,
    VERTICALITY_BAND,
    _reference_swings,
    _smoothstep,
    _verticality,
)
from gpx2fit.core.pacing.gradient import (
    GRADIENT_WINDOW_M,
    MINETTI_DOWNHILL_COST_SLOPE,
    CurveExponents,
    CurveShape,
    calculate_gradient,
    minetti_speeds_from_gradients,
    tobler_speeds_from_gradients,
)


@dataclass(frozen=True)
class PacingParams:
    """Every constant the pacing model is being calibrated on, as one value.

    Defaults are exactly today's shipped constants, so `PacingParams()` means
    "the model as it stands" and any tuned result is a diff against that.

    Attributes:
        gradient_window_m: gradient.GRADIENT_WINDOW_M. Unlike every other field
            here, this one changes the gradients themselves, so a TrackModel
            built for one value can't be reused with another — see TrackModel.
        curve_reference_grade: curve_selection.CURVE_REFERENCE_GRADE.
        uphill_fill: Overrides CURVE_FILLS[sport].uphill when set.
        downhill_fill: Overrides CURVE_FILLS[sport].downhill when set.
        minetti_downhill_cost_slope: gradient.MINETTI_DOWNHILL_COST_SLOPE.
            Changes the raw Minetti curve, which TrackModel keeps per value.
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
            which is how `sweep` finds the blend one activity wanted.
        max_speed_ratio: Pins the speed-swing bound the same way.
    """
    gradient_window_m: float = GRADIENT_WINDOW_M
    curve_reference_grade: float = CURVE_REFERENCE_GRADE
    uphill_fill: float | None = None
    downhill_fill: float | None = None
    minetti_downhill_cost_slope: float = MINETTI_DOWNHILL_COST_SLOPE
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


_PARAM_NAMES = frozenset(param.name for param in fields(PacingParams))


@dataclass(frozen=True)
class ParamsBySport:
    """Constants shared by every sport, plus per-sport overrides on top.

    `core/` ships one value of most constants for all sports, but `fit` fits
    some of them per sport (the fills, the speed bounds) to show whether the
    sports really disagree. This carries both, so `fit`'s `proposed.json` can
    be fed back into any command and run exactly as it was fitted.

    Attributes:
        shared: What every sport runs with unless overridden.
        per_sport: sport -> {PacingParams field: value}, applied over `shared`.
    Raises:
        ValueError: If an override names a field PacingParams doesn't have.
    """
    shared: PacingParams = field(default_factory=PacingParams)
    per_sport: Mapping[SportType, Mapping[str, float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for sport, overrides in self.per_sport.items():
            unknown = set(overrides) - _PARAM_NAMES
            if unknown:
                raise ValueError(f"Unknown constant(s) for {sport.value}: {', '.join(sorted(unknown))}.")

    def for_sport(self, sport: SportType) -> PacingParams:
        """The complete constants one sport runs with."""
        return replace(self.shared, **self.per_sport.get(sport, {}))


@dataclass(frozen=True)
class ResolvedSettings:
    """What the model decided for one workout, plus the features it decided from.

    The features are what the resolvers read, so they're reported rather than
    recomputed.

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


@dataclass(frozen=True)
class TrackModel:
    """One track reduced to everything about it that no PacingParams can change.

    A fit evaluates the same track under thousands of parameter sets. What
    varies between them is only how the curves are softened, blended, bounded
    and scaled — the gradients, the leg distances, the raw curve values and the
    two terrain features are the same every time. Computing them once here is
    what makes the search cheap, and it is also the only place `core/`'s curve
    functions are called, so the harness keeps pacing on the app's own curves.

    The one parameter it *does* depend on is `gradient_window_m`, since that
    changes the gradients themselves. A TrackModel therefore records the window
    it was built for, and callers reusing one across a search must check it (as
    `compare.ActivityContext` does).

    `minetti_downhill_cost_slope` changes the raw Minetti curve too, but not
    the gradients, so the Minetti values are computed once per slope on first
    use and kept (`raw_minetti`, `flat_equivalent_mps`).

    Attributes:
        gradient_window_m: The window the gradients were smoothed over.
        gradients: Per-leg gradient, as `calculate_gradient` returns it.
        leg_distances: Per-leg distance in metres.
        uphill: Per-leg mask, True where the gradient is positive — the branch
            `gradient._soften` takes between its two exponents.
        raw_tobler: Per-leg Tobler speed at exponent 1.0, relative to flat.
        total_distance_m: Sum of `leg_distances`.
        verticality: curve_selection._verticality. 0.0 for a track with no
            distance, where core's version would divide by zero; every caller
            guards that case anyway, but a feature printed in every table
            shouldn't be able to raise.
    """
    gradient_window_m: float
    gradients: np.ndarray
    leg_distances: np.ndarray
    uphill: np.ndarray
    raw_tobler: np.ndarray
    total_distance_m: float
    verticality: float
    # downhill cost slope -> (raw Minetti speeds, flat-equivalent distance).
    _minetti: dict[float, tuple[np.ndarray, float]] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def build(cls, track: Track, gradient_window_m: float = GRADIENT_WINDOW_M) -> "TrackModel":
        """Build for a whole track.

        Args:
            track: A track with `distance_from_start` and elevation set.
            gradient_window_m: The window to smooth the gradients over.
        """
        gradients = calculate_gradient(track, gradient_window_m)
        distances = [
            later.distance_from_start - earlier.distance_from_start
            for earlier, later in zip(track.points, track.points[1:])
        ]
        total_distance = sum(distances)

        return cls(
            gradient_window_m=gradient_window_m,
            gradients=np.asarray(gradients, dtype=float),
            leg_distances=np.asarray(distances, dtype=float),
            uphill=np.asarray(gradients, dtype=float) > 0,
            raw_tobler=np.asarray(tobler_speeds_from_gradients(gradients, 1.0, 1.0), dtype=float),
            total_distance_m=total_distance,
            verticality=_verticality(gradients, distances) if total_distance > 0 else 0.0,
        )

    def _minetti_at(self, downhill_cost_slope: float) -> tuple[np.ndarray, float]:
        """Core's Minetti values for one descent cost slope, computed on first use."""
        cached = self._minetti.get(downhill_cost_slope)
        if cached is None:
            gradients = self.gradients.tolist()
            distances = self.leg_distances.tolist()
            raw = minetti_speeds_from_gradients(gradients, 1.0, 1.0, downhill_cost_slope=downhill_cost_slope)
            # resolve_tobler_weight's probe: Minetti at its default exponents,
            # exactly as core does, so the answer doesn't depend on the fitted bound.
            probe = minetti_speeds_from_gradients(gradients, downhill_cost_slope=downhill_cost_slope)
            flat_equivalent_distance = sum(
                distance / speed for distance, speed in zip(distances, probe) if distance > 0 and speed > 0
            )
            cached = (np.asarray(raw, dtype=float), flat_equivalent_distance)
            self._minetti[downhill_cost_slope] = cached
        return cached

    def raw_minetti(self, downhill_cost_slope: float) -> np.ndarray:
        """Per-leg Minetti speed at exponent 1.0, relative to flat."""
        return self._minetti_at(downhill_cost_slope)[0]

    def flat_equivalent_mps(self, active_seconds: float, downhill_cost_slope: float) -> float:
        """The flat-ground speed that would have produced this moving time over this terrain.

        `resolve_tobler_weight`'s first criterion, exposed so it can be
        reported as a feature. Dividing each leg's distance by its relative
        Minetti speed gives a flat-equivalent distance. The curve is
        dimensionless, so the sum is metres, not seconds and dividing that by
        the moving time answers "how fast was this really, with the terrain
        divided out?".
        """
        if active_seconds <= 0:
            return 0.0
        return self._minetti_at(downhill_cost_slope)[1] / active_seconds


def tobler_weight_for(
    model: TrackModel, active_seconds: float, sport: SportType, params: PacingParams
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

    equivalent = model.flat_equivalent_mps(active_seconds, params.minetti_downhill_cost_slope)
    if equivalent <= 0:
        return 1.0 if sport == SportType.HIKING else 0.0

    slow_weight = 1.0 - _smoothstep(equivalent, flat_threshold, params.flat_equivalent_band_mps)
    steep_weight = _smoothstep(model.verticality, steep_threshold, params.verticality_band)
    return max(slow_weight, steep_weight)


def max_speed_ratio_for(model: TrackModel, sport: SportType, params: PacingParams) -> float:
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

    if model.total_distance_m <= 0:
        return flat

    hilliness = _smoothstep(model.verticality, params.hilly_verticality, params.hilly_verticality_band)
    return flat + (hilly - flat) * hilliness


@functools.cache
def _minetti_swings(reference_grade: float, downhill_cost_slope: float) -> tuple[float, float]:
    """curve_selection._reference_swings of the raw Minetti curve, kept per knob pair."""
    return _reference_swings(
        lambda grades: minetti_speeds_from_gradients(grades, 1.0, 1.0, downhill_cost_slope=downhill_cost_slope),
        reference_grade,
    )


@functools.cache
def _tobler_swings(reference_grade: float) -> tuple[float, float]:
    """curve_selection._reference_swings of the raw Tobler curve, kept per reference grade."""
    return _reference_swings(lambda grades: tobler_speeds_from_gradients(grades, 1.0, 1.0), reference_grade)


def curve_shape_for(max_speed_ratio: float, sport: SportType, params: PacingParams) -> CurveShape:
    """curve_selection.resolve_curve_shape, with the reference grade, fills and descent cost slope as parameters."""
    log_limit = math.log(max_speed_ratio)
    fills = CURVE_FILLS[sport]
    uphill_fill = params.uphill_fill if params.uphill_fill is not None else fills.uphill
    downhill_fill = params.downhill_fill if params.downhill_fill is not None else fills.downhill

    def fitted(swings: tuple[float, float]) -> CurveExponents:
        uphill_swing, downhill_swing = swings
        return CurveExponents(
            uphill=uphill_fill * log_limit / uphill_swing,
            downhill=downhill_fill * log_limit / downhill_swing,
        )

    return CurveShape(
        minetti=fitted(_minetti_swings(params.curve_reference_grade, params.minetti_downhill_cost_slope)),
        tobler=fitted(_tobler_swings(params.curve_reference_grade)),
    )


def resolve(
    model: TrackModel, sport: SportType, active_seconds: float, params: PacingParams
) -> ResolvedSettings:
    """Resolve the three per-workout decisions combine() makes, plus the features behind them."""
    weight = tobler_weight_for(model, active_seconds, sport, params)
    ratio = max_speed_ratio_for(model, sport, params)
    return ResolvedSettings(
        tobler_weight=weight,
        max_speed_ratio=ratio,
        curve_shape=curve_shape_for(ratio, sport, params),
        verticality=model.verticality,
        flat_equivalent_mps=model.flat_equivalent_mps(active_seconds, params.minetti_downhill_cost_slope),
    )


def _blended(model: TrackModel, raw_minetti: np.ndarray, tobler_weight: float, shape: CurveShape) -> np.ndarray:
    """gradient.blended_speeds_from_gradients over the precomputed raw curves.

    Each curve is softened as `gradient._soften` does (gradient == 0 takes the
    downhill branch, where both raw curves are exactly 1.0 anyway), then the
    two are blended geometrically. The general formula is already exact at a
    weight of 0.0 or 1.0 (`x ** 0.0` is 1.0, `x ** 1.0` is x); the shortcuts
    are only there to skip the unused curve, since resolved weights often land
    exactly on either end and this runs thousands of times per fit.
    """
    def softened(raw_speeds: np.ndarray, exponents: CurveExponents) -> np.ndarray:
        return raw_speeds ** np.where(model.uphill, exponents.uphill, exponents.downhill)

    if tobler_weight == 0.0:
        return softened(raw_minetti, shape.minetti)
    if tobler_weight == 1.0:
        return softened(model.raw_tobler, shape.tobler)
    minetti = softened(raw_minetti, shape.minetti)
    tobler = softened(model.raw_tobler, shape.tobler)
    return minetti ** (1.0 - tobler_weight) * tobler ** tobler_weight


def _compressed(speeds: np.ndarray, max_ratio: float) -> np.ndarray:
    """combine._compress_speed_toward_typical over every leg, around the median positive speed.

    Raises:
        ValueError: If `max_ratio` isn't above 1.0, where the bound has no room
            to swing and core's own version divides by a zero log.
    """
    if max_ratio <= 1.0:
        raise ValueError(f"max_speed_ratio must be above 1.0, got {max_ratio}.")

    positive = speeds > 0
    if not positive.any():
        return speeds

    typical = float(np.median(speeds[positive]))
    log_limit = math.log(max_ratio)
    bounded = speeds.copy()
    bounded[positive] = typical * np.exp(
        log_limit * np.tanh(np.log(speeds[positive] / typical) / log_limit)
    )
    return bounded


def elapsed_from_model(
    model: TrackModel,
    sport: SportType,
    active_seconds: float,
    params: PacingParams,
    multipliers: np.ndarray | None = None,
) -> tuple[np.ndarray, ResolvedSettings]:
    """Predicted elapsed seconds at every track point, starting at 0.0.

    `_pace_segment` for the single-segment case the harness uses (start and
    end are the only anchors, which is what the converter gets when the user
    gives just a start time and a duration): blend the curves, apply any
    surface multipliers, compress toward the median, model each leg's time,
    then scale all of them by one factor so the total comes to
    `active_seconds` exactly. That single rescale is why only the *shape* of
    this curve is a prediction — the total is matched by construction, and no
    parameter can change it.

    Args:
        model: The prepared track, from `TrackModel.build`.
        sport: Picks the per-sport constants.
        active_seconds: The activity's moving time, which the result sums to.
        params: The constants to run with.
        multipliers: Optional per-leg surface multipliers, one per leg.

    Returns:
        The cumulative seconds per point (one more than there are legs), and
        the settings the model resolved for this workout.
    Raises:
        ValueError: If the track has fewer than two points, `active_seconds`
            isn't positive, or the multipliers don't match the leg count.
    """
    if len(model.leg_distances) < 1:
        raise ValueError("Need at least 2 points to pace.")
    if active_seconds <= 0:
        raise ValueError(f"active_seconds must be positive, got {active_seconds}.")

    settings = resolve(model, sport, active_seconds, params)
    raw_minetti = model.raw_minetti(params.minetti_downhill_cost_slope)
    speeds = _blended(model, raw_minetti, settings.tobler_weight, settings.curve_shape)
    if multipliers is not None:
        if len(multipliers) != len(speeds):
            raise ValueError(f"Expected {len(speeds)} multipliers, got {len(multipliers)}.")
        speeds = speeds * np.maximum(multipliers, _MIN_SURFACE_MULTIPLIER)
    speeds = _compressed(speeds, settings.max_speed_ratio)

    moving = speeds > 0
    modeled = np.zeros(len(speeds))
    modeled[moving] = model.leg_distances[moving] / speeds[moving]

    modeled_total = modeled.sum()
    if modeled_total <= 0:
        raise ValueError("The model gave this track zero total time; it has no positive-distance legs.")

    elapsed = np.empty(len(modeled) + 1)
    elapsed[0] = 0.0
    np.cumsum(modeled * (active_seconds / modeled_total), out=elapsed[1:])
    return elapsed, settings
