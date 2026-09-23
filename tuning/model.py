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

**Why this is numpy and `core/` is not.** A fit evaluates this model thousands
of times over the same handful of tracks, where `core/` runs it once per
conversion — and `core/` has to stay pure Python because it runs inside Pyodide
(see CLAUDE.md). The split is therefore deliberate, and it is drawn so that
nothing about the *curves* is restated: `TrackModel` calls `core/`'s own
`minetti_speeds_from_gradients` / `tobler_speeds_from_gradients` once per
track, at exponent 1.0, and every later evaluation only softens, blends and
scales those raw curves. Softening is `raw ** exponent`, which is exactly what
`gradient._soften` does, so a change to the Minetti polynomial or Tobler's
slope factor still reaches the harness without anything here being touched.

`TrackModel` is also where the fit's speedup comes from: everything it holds
depends on the track and the gradient window alone, so a sweep computes it once
and reuses it for every trial. That includes the two features the resolvers
read — verticality and flat-equivalent speed — which the un-cached version
recomputed (Minetti probe and all) on every single evaluation.
"""

import math
from dataclasses import dataclass

import numpy as np

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
        gradient_window_m: gradient.GRADIENT_WINDOW_M. Unlike every other field
            here, this one changes the gradients themselves, so a TrackModel
            built for one value can't be reused with another — see TrackModel.
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

    Attributes:
        gradient_window_m: The window the gradients were smoothed over.
        gradients: Per-leg gradient, as `calculate_gradient` returns it.
        leg_distances: Per-leg distance in metres.
        uphill: Per-leg mask, True where the gradient is positive — the branch
            `gradient._soften` takes between its two exponents.
        raw_minetti: Per-leg Minetti speed at exponent 1.0, relative to flat.
        raw_tobler: The same for Tobler.
        total_distance_m: Sum of `leg_distances`.
        verticality: curve_selection._verticality — the distance-weighted mean
            |gradient|. 0.0 for a track with no distance, where core's version
            would divide by zero; every caller guards that case anyway, but a
            feature printed in every table shouldn't be able to raise.
        flat_equivalent_distance_m: Σ leg_distance / probe_speed, the numerator
            of `resolve_tobler_weight`'s first criterion. The probe is Minetti
            at its own default exponents, exactly as core does: it is only a
            difficulty normalizer there, so the answer must not depend on the
            bound being fitted.
    """
    gradient_window_m: float
    gradients: np.ndarray
    leg_distances: np.ndarray
    uphill: np.ndarray
    raw_minetti: np.ndarray
    raw_tobler: np.ndarray
    total_distance_m: float
    verticality: float
    flat_equivalent_distance_m: float

    @classmethod
    def from_legs(
        cls, gradients: list[float] | np.ndarray, leg_distances: list[float] | np.ndarray,
        gradient_window_m: float = GRADIENT_WINDOW_M,
    ) -> "TrackModel":
        """Build from already-computed per-leg gradients and distances."""
        grades = np.asarray(gradients, dtype=float)
        distances = np.asarray(leg_distances, dtype=float)
        if len(grades) != len(distances):
            raise ValueError(f"Got {len(grades)} gradients for {len(distances)} legs.")

        as_list = grades.tolist()
        raw_minetti = np.asarray(minetti_speeds_from_gradients(as_list, 1.0, 1.0), dtype=float)
        raw_tobler = np.asarray(tobler_speeds_from_gradients(as_list, 1.0, 1.0), dtype=float)
        probe = np.asarray(minetti_speeds_from_gradients(as_list), dtype=float)

        total_distance = float(distances.sum())
        usable = (distances > 0) & (probe > 0)
        return cls(
            gradient_window_m=gradient_window_m,
            gradients=grades,
            leg_distances=distances,
            uphill=grades > 0,
            raw_minetti=raw_minetti,
            raw_tobler=raw_tobler,
            total_distance_m=total_distance,
            verticality=(
                float((np.abs(grades) * distances).sum() / total_distance) if total_distance > 0 else 0.0
            ),
            flat_equivalent_distance_m=float((distances[usable] / probe[usable]).sum()),
        )

    @classmethod
    def build(
        cls, track: Track, params: PacingParams | None = None, gradients: list[float] | None = None
    ) -> "TrackModel":
        """Build for a whole track, computing its gradients unless they're supplied.

        Args:
            track: A track with `distance_from_start` and elevation set.
            params: Supplies `gradient_window_m`. Defaults to today's values.
            gradients: Already-computed gradients for this track *and window*,
                if the caller has them.
        """
        params = params or PacingParams()
        if gradients is None:
            gradients = calculate_gradient(track, params.gradient_window_m)
        distances = [
            later.distance_from_start - earlier.distance_from_start
            for earlier, later in zip(track.points, track.points[1:])
        ]
        return cls.from_legs(gradients, distances, params.gradient_window_m)

    def flat_equivalent_mps(self, active_seconds: float) -> float:
        """The flat-ground speed that would have produced this moving time over this terrain.

        `resolve_tobler_weight`'s first criterion, exposed so it can be
        reported as a feature. Dividing each leg's distance by its relative
        Minetti speed gives a flat-equivalent distance — the curve is
        dimensionless, so the sum is metres, not seconds — and dividing that by
        the moving time answers "how fast was this really, with the terrain
        divided out?".
        """
        return self.flat_equivalent_distance_m / active_seconds if active_seconds > 0 else 0.0


def _smoothstep(value: float, center: float, half_width: float) -> float:
    """curve_selection._smoothstep: ramp 0.0 to 1.0 across `center` +/- `half_width`."""
    position = min(1.0, max(0.0, (value - center) / (2 * half_width) + 0.5))
    return position * position * (3 - 2 * position)


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
    default_weight = 1.0 if sport == SportType.HIKING else 0.0

    if model.total_distance_m <= 0 or active_seconds <= 0:
        return default_weight

    equivalent = model.flat_equivalent_mps(active_seconds)
    if equivalent <= 0:
        return default_weight

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


def resolve(
    model: TrackModel, sport: SportType, active_seconds: float, params: PacingParams
) -> ResolvedSettings:
    """Resolve the three per-workout decisions combine() makes, plus the features behind them."""
    weight = tobler_weight_for(model, active_seconds, sport, params)
    ratio = max_speed_ratio_for(model, sport, params)
    return ResolvedSettings(
        tobler_weight=weight,
        max_speed_ratio=ratio,
        curve_shape=curve_shape_for(ratio, params),
        verticality=model.verticality,
        flat_equivalent_mps=model.flat_equivalent_mps(active_seconds),
    )


def _softened(raw_speeds: np.ndarray, uphill: np.ndarray, exponents: CurveExponents) -> np.ndarray:
    """gradient._soften: each raw speed to the uphill or downhill exponent, by its leg's gradient.

    gradient == 0 takes the downhill branch, but the raw speed there is exactly
    1.0 for both curves, so either exponent leaves it at 1.0.

    Raises:
        ValueError: If either exponent isn't positive, as core's does.
    """
    if exponents.uphill <= 0 or exponents.downhill <= 0:
        raise ValueError(
            f"Curve exponents must be positive, got uphill={exponents.uphill}, "
            f"downhill={exponents.downhill}."
        )
    return raw_speeds ** np.where(uphill, exponents.uphill, exponents.downhill)


def _blended(model: TrackModel, tobler_weight: float, shape: CurveShape) -> np.ndarray:
    """gradient.blended_speeds_from_gradients: the geometric blend of both softened curves.

    Keeps core's two exact endpoints rather than letting a weight of 0.0 or 1.0
    fall through the general formula, so a sweep that lands on pure Minetti or
    pure Tobler gets the same numbers the app would.
    """
    if not 0.0 <= tobler_weight <= 1.0:
        raise ValueError(f"tobler_weight must be within [0.0, 1.0], got {tobler_weight}.")
    if tobler_weight == 0.0:
        return _softened(model.raw_minetti, model.uphill, shape.minetti)
    if tobler_weight == 1.0:
        return _softened(model.raw_tobler, model.uphill, shape.tobler)

    minetti = _softened(model.raw_minetti, model.uphill, shape.minetti)
    tobler = _softened(model.raw_tobler, model.uphill, shape.tobler)
    return minetti ** (1.0 - tobler_weight) * tobler ** tobler_weight


def _compressed(speeds: np.ndarray, max_ratio: float) -> np.ndarray:
    """combine's median-and-tanh step: soft-bound every leg within `max_ratio` of typical.

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


def leg_speeds(
    model: TrackModel,
    sport: SportType,
    active_seconds: float,
    params: PacingParams,
    multipliers: list[float] | np.ndarray | None = None,
) -> tuple[np.ndarray, ResolvedSettings]:
    """The model's per-leg relative speeds, after blending, surface and compression.

    This is `_pace_segment` steps 4-6 for the single-segment case the harness
    uses (start and end are the only anchors, which is what the converter gets
    when the user gives just a start time and a duration). Relative, not m/s:
    the absolute scale comes from `active_seconds` in `predict_elapsed`.
    """
    settings = resolve(model, sport, active_seconds, params)
    speeds = _blended(model, settings.tobler_weight, settings.curve_shape)

    if multipliers is not None:
        multipliers = np.asarray(multipliers, dtype=float)
        if len(multipliers) != len(speeds):
            raise ValueError(f"Expected {len(speeds)} multipliers, got {len(multipliers)}.")
        speeds = speeds * np.maximum(multipliers, _MIN_SURFACE_MULTIPLIER)

    return _compressed(speeds, settings.max_speed_ratio), settings


def elapsed_from_model(
    model: TrackModel,
    sport: SportType,
    active_seconds: float,
    params: PacingParams,
    multipliers: list[float] | np.ndarray | None = None,
) -> tuple[np.ndarray, ResolvedSettings]:
    """Predicted elapsed seconds at every track point, starting at 0.0.

    `_pace_segment` steps 4-9 for a single segment: model each leg's time from
    its relative speed, then scale all of them by one factor so the total comes
    to `active_seconds` exactly. That single rescale is why only the *shape* of
    this curve is a prediction — the total is matched by construction, and no
    parameter can change it.

    This is the form the search uses, taking a prepared TrackModel and giving
    back an array; `predict_elapsed` is the same thing from a Track.

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

    speeds, settings = leg_speeds(model, sport, active_seconds, params, multipliers)
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


def predict_elapsed(
    track: Track,
    sport: SportType,
    active_seconds: float,
    params: PacingParams,
    multipliers: list[float] | None = None,
    gradients: list[float] | None = None,
) -> tuple[list[float], ResolvedSettings]:
    """Predicted elapsed seconds at every point of a track — see `elapsed_from_model`.

    The entry point the drift guard uses, and the one to reach for when there
    is a Track rather than a prepared TrackModel in hand. A search should build
    the TrackModel once and call `elapsed_from_model` instead, since this
    rebuilds it (curves and all) on every call.

    Args:
        track: A track with `distance_from_start` set on every point.
        sport: Picks the per-sport constants.
        active_seconds: The activity's moving time, which the result sums to.
        params: The constants to run with.
        multipliers: Optional per-leg surface multipliers, one per leg.
        gradients: Already-computed gradients for this track and
            `params.gradient_window_m`, if the caller has them.
    """
    if len(track.points) < 2:
        raise ValueError(f"Need at least 2 points to pace, got {len(track.points)}.")

    model = TrackModel.build(track, params, gradients)
    elapsed, settings = elapsed_from_model(model, sport, active_seconds, params, multipliers)
    return elapsed.tolist(), settings
