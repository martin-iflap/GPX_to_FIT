"""Decide which gradient-speed curve a workout should be paced with, and how far its speed may swing.

Separate from combine.py on purpose: choosing a curve and its spread is a
property of the whole activity (how slow, how steep), while combine.py's job
is fitting an already-chosen curve to known anchor timestamps.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass

from gpx2fit.core.models import SportType
from gpx2fit.core.pacing.gradient import (
    CurveExponents,
    CurveShape,
    minetti_speeds_from_gradients,
    tobler_speeds_from_gradients,
)


@dataclass(frozen=True)
class ToblerThresholds:
    """Where each of resolve_tobler_weight's two criteria tips fully over to Tobler.

    Attributes:
        flat_equivalent_mps: Flat-equivalent speed (see resolve_tobler_weight)
            below which the activity is treated as walked.
        verticality: Distance-weighted mean |gradient| above which the terrain
            is treated as walked, however fast the activity is.
    """
    flat_equivalent_mps: float
    verticality: float


# Keyed by sport because running should need stronger signal to tip to walking curve.
TOBLER_THRESHOLDS = {
    SportType.RUNNING: ToblerThresholds(flat_equivalent_mps=1.7, verticality=0.14),
    SportType.HIKING: ToblerThresholds(flat_equivalent_mps=2.2, verticality=0.10),
}

# Half-width of the transition band around each threshold above: the criterion
# ramps smoothly across threshold ± band rather than flipping at it, so one
# second of duration can't swap the whole activity's pacing character.
FLAT_EQUIVALENT_BAND_MPS = 0.4
VERTICALITY_BAND = 0.03


@dataclass(frozen=True)
class SpeedRatioBounds:
    """How far a workout's leg speeds may swing from their typical pace — see resolve_max_speed_ratio.
    Attributes:
        flat: Max speed ratio on flat terrain.
        hilly: Max speed ratio once verticality is past the hilly band.
    """
    flat: float
    hilly: float


# Terrain dominates here and sport only nudges it.
# These are the best guesses from the current data available (2026-09-29).
MAX_SPEED_RATIO_BOUNDS = {
    SportType.RUNNING: SpeedRatioBounds(flat=2.1, hilly=2.7),
    SportType.HIKING: SpeedRatioBounds(flat=1.8, hilly=2.6),
}

# The ratio ramps from flat to hilly across HILLY_VERTICALITY ± band (0.02 … 0.10).
HILLY_VERTICALITY = 0.06
HILLY_VERTICALITY_BAND = 0.04

# The user's pace-smoothness level (the GUI's 1–10 slider), as the power the
# automatic ratio is raised to. That scales log(max_speed_ratio), and with it
# (via resolve_curve_shape) every leg's log-speed swing, by the same factor, so
# the level changes how much the pace varies but not its shape. Relative on
# purpose: the terrain/sport adaptation above still applies at every level.
# The default is the automatic ratio itself; the rough end is kept short
# because the automatic ratio already swings plenty.
DEFAULT_SMOOTHNESS = 5
SMOOTHNESS_SCALES = {
    1: 1.2, 2: 1.15, 3: 1.1, 4: 1.05, 5: 1.0,
    6: 0.8, 7: 0.62, 8: 0.46, 9: 0.32, 10: 0.2,
}

CURVE_REFERENCE_GRADE = 0.25


@dataclass(frozen=True)
class CurveFills:
    """How much of the speed-swing bound a curve may use up to CURVE_REFERENCE_GRADE — see resolve_curve_shape.

    Each is a share of log(max_speed_ratio). Below 1.0, ordinary terrain stays in
    tanh's near-linear range and only real outliers get squashed.
    Attributes:
        uphill: The share climbs may use.
        downhill: The share descents may use.
    """
    uphill: float
    downhill: float


# Keyed by the declared sport: the curves themselves are
# shared, but how hard the terrain pushes pace within the bound is not.

# Fitted by the tuning harness on the whole corpus (2026-09-29, `fit --holdout 0`;
# held-out splits agree within each value's plateau). Hikers barely change pace
# with grade compared to runners: hiking is paced on Tobler, whose raw swing is
# already large, and the corpus's hikes want well under half of it. Running's
# downhill share is biased towards slower speeds by the corpus available.
CURVE_FILLS = {
    SportType.RUNNING: CurveFills(uphill=0.85, downhill=0.79),
    SportType.HIKING: CurveFills(uphill=0.45, downhill=0.17),
}

# How many grades, evenly spaced from flat to ±CURVE_REFERENCE_GRADE, a
# curve's swing is measured on (see _reference_swings). 0.5% apart.
_REFERENCE_SAMPLES = 50


def _smoothstep(value: float, center: float, half_width: float) -> float:
    """Ramp smoothly from 0.0 to 1.0 across `center` ± `half_width`.

    Flat at 0.0 below the band and 1.0 above it, joined with zero slope at
    both ends, so a value drifting across the band moves the result gradually
    instead of in a step. `half_width` must be positive.
    """
    if half_width <= 0:
        raise ValueError(f"half_width must be positive, got {half_width}.")
    position = min(1.0, max(0.0, (value - center) / (2 * half_width) + 0.5))
    return position * position * (3 - 2 * position)


def _verticality(gradients: list[float], leg_distances: list[float]) -> float:
    """Distance-weighted mean |gradient|: total climb and descent per meter traveled.
     - `total_distance` must be positive; callers guard for that.
    """
    total_distance = sum(leg_distances)
    return sum(abs(gradient) * distance for gradient, distance in zip(gradients, leg_distances)) / total_distance


def resolve_tobler_weight(
    gradients: list[float],
    leg_distances: list[float],
    active_seconds: float,
    sport: SportType,
) -> float:
    """Decide how much of Tobler's walking curve this workout should be paced with.
    Minetti's curve is calibrated on running economy, so it misreads slow and
    climb-heavy activities. Two criteria say so, OR'd together via max():

    - **Flat-equivalent speed.** Raw average speed can't tell a slow walker on
      the flat from a strong hiker grinding up 15%, so the terrain is divided
      out first: dividing each leg's distance by its relative Minetti speed
      gives a flat-equivalent distance (the curve is dimensionless, so this is
      meters, not seconds), and dividing that by the moving time answers "what
      flat-ground speed would have produced this?". Below the sport's
      threshold, the activity is being walked.
    - **Verticality**, the distance-weighted mean |gradient| — total climb and
      descent per meter traveled, since Minetti struggles with steep terrain in
      either direction. Above the sport's threshold, the terrain is walked
      however fast the activity is.

    Minetti probes the terrain whatever the answer turns out to be. It is only
    a difficulty normalizer here, not the pacing decision.

    Climb and descent come from `gradients`, already smoothed over
    GRADIENT_WINDOW_M, rather than from raw point-to-point elevation deltas.
    GPX elevation is usually DEM data rounded to whole meters, so raw deltas
    report hundreds of meters of phantom climb on a flat road — which would
    read as mountainous here.

    Args:
        gradients: The whole track's per-leg gradients (N-1 values for N points),
            from pacing.gradient.calculate_gradient.
        leg_distances: The whole track's per-leg distances, same length and order.
        active_seconds: The workout's anchor-to-anchor duration minus all time
            spent stopped (see combine._total_stop_seconds).
        sport: Sport type, which picks the threshold pair.

    Returns:
        0.0 for pure Minetti, 1.0 for pure Tobler, or anything in between —
        passed to pacing.gradient.blended_speeds_from_gradients. A track with
        no distance or no active time falls back to the sport's own default
        (Tobler for hiking, Minetti for running) rather than raising: pacing
        a degenerate track is still better than refusing to convert it.
    """
    thresholds = TOBLER_THRESHOLDS[sport]
    default_weight = 1.0 if sport == SportType.HIKING else 0.0

    total_distance = sum(leg_distances)
    if total_distance <= 0 or active_seconds <= 0:
        return default_weight

    verticality = _verticality(gradients, leg_distances)

    probe_speeds = minetti_speeds_from_gradients(gradients)
    flat_equivalent_distance_m = sum(
        distance / speed
        for distance, speed in zip(leg_distances, probe_speeds)
        if distance > 0 and speed > 0
    )
    if flat_equivalent_distance_m <= 0:
        return default_weight
    flat_equivalent_mps = flat_equivalent_distance_m / active_seconds

    slow_weight = 1.0 - _smoothstep(flat_equivalent_mps, thresholds.flat_equivalent_mps, FLAT_EQUIVALENT_BAND_MPS)
    steep_weight = _smoothstep(verticality, thresholds.verticality, VERTICALITY_BAND)
    return max(slow_weight, steep_weight)


def resolve_max_speed_ratio(
    gradients: list[float],
    leg_distances: list[float],
    sport: SportType,
    smoothness: int = DEFAULT_SMOOTHNESS,
) -> float:
    """Decide how far this workout's leg speeds may swing from their typical pace.

    Feeds combine._compress_speed_toward_typical's soft bound. Flat routes get
    a tighter bound than hilly ones, where real pace legitimately varies more;
    the sport picks the pair of bounds (see MAX_SPEED_RATIO_BOUNDS), and
    verticality ramps smoothly between them rather than switching. The user's
    `smoothness` level then narrows or widens that automatic ratio (see
    SMOOTHNESS_SCALES).

    Like resolve_tobler_weight, this is resolved once for the whole workout:
    anchors are where the user knows a time, not where the terrain changes. It
    reads the same smoothed `gradients`, never raw elevation deltas, so DEM
    rounding on a flat road doesn't read as hilly.

    Args:
        gradients: The whole track's per-leg gradients (N-1 values for N points),
            from pacing.gradient.calculate_gradient.
        leg_distances: The whole track's per-leg distances, same length and order.
        sport: Sport type, which picks the flat/hilly bounds.
        smoothness: The user's level, a key of SMOOTHNESS_SCALES; higher is
            more even pacing. DEFAULT_SMOOTHNESS leaves the automatic ratio as is.
    Returns:
        A ratio greater than 1.0. A track with no distance gets the sport's
        flat bound (scaled by the level like any other).
    Raises:
        ValueError: If `smoothness` isn't one of SMOOTHNESS_SCALES' levels.
    """
    if smoothness not in SMOOTHNESS_SCALES or isinstance(smoothness, float):
        raise ValueError(
            f"smoothness must be an integer from {min(SMOOTHNESS_SCALES)} to {max(SMOOTHNESS_SCALES)}, "
            f"got {smoothness!r}."
        )
    scale = SMOOTHNESS_SCALES[smoothness]

    bounds = MAX_SPEED_RATIO_BOUNDS[sport]
    if sum(leg_distances) <= 0:
        return bounds.flat ** scale

    hilliness = _smoothstep(_verticality(gradients, leg_distances), HILLY_VERTICALITY, HILLY_VERTICALITY_BAND)
    return (bounds.flat + (bounds.hilly - bounds.flat) * hilliness) ** scale


def _reference_swings(
    raw_speeds_of: Callable[[list[float]], list[float]], reference_grade: float
) -> tuple[float, float]:
    """A raw curve's largest |log speed| on climbs and on descents up to `reference_grade`.

    For a curve that keeps moving away from flat speed as the grade steepens,
    this is simply its value at ±reference_grade. A curve that peaks and turns
    back toward flat speed is measured at its peak instead, so it can't read
    as nearly flat when it happens to cross flat speed near the reference grade.

    Args:
        raw_speeds_of: gradients -> the curve's un-softened relative speeds.
        reference_grade: How far from flat to look, e.g. CURVE_REFERENCE_GRADE.
    Returns:
        (uphill swing, downhill swing), both positive for any curve that isn't flat.
    """
    grades = [reference_grade * index / _REFERENCE_SAMPLES for index in range(1, _REFERENCE_SAMPLES + 1)]
    uphill = raw_speeds_of(grades)
    downhill = raw_speeds_of([-grade for grade in grades])
    return max(abs(math.log(speed)) for speed in uphill), max(abs(math.log(speed)) for speed in downhill)


def _fitted_exponents(
    uphill_swing: float, downhill_swing: float, log_limit: float, fills: CurveFills
) -> CurveExponents:
    """Exponents that shrink one curve's reference swings to their fill share of `log_limit`."""
    return CurveExponents(
        uphill=fills.uphill * log_limit / uphill_swing,
        downhill=fills.downhill * log_limit / downhill_swing,
    )


def resolve_curve_shape(max_speed_ratio: float, sport: SportType) -> CurveShape:
    """Fit both speed curves' exponents to this workout's speed-swing bound.

    A lower max_speed_ratio alone would just squash a steep curve against the
    tanh bound in combine._compress_speed_toward_typical, and a long climb or
    descent then comes out as a flat plateau with a sharp edge into the next
    one. So the curves themselves are flattened to fit: between flat and
    ±CURVE_REFERENCE_GRADE, each curve's largest log-speed is exactly the
    sport's CURVE_FILLS share of log(max_speed_ratio) (see _reference_swings),
    which leaves the bound to catch only the steeper outliers. Because
    exponents scale log-speed linearly, the curve and the bound always widen
    and narrow together.

    Args:
        max_speed_ratio: This workout's bound, from resolve_max_speed_ratio. Must be > 1.
        sport: Sport type, which picks the fills (see CURVE_FILLS).
    Returns:
        The exponents for pacing.gradient.blended_speeds_from_gradients.
    """
    log_limit = math.log(max_speed_ratio)
    fills = CURVE_FILLS[sport]
    minetti = _reference_swings(lambda grades: minetti_speeds_from_gradients(grades, 1.0, 1.0), CURVE_REFERENCE_GRADE)
    tobler = _reference_swings(lambda grades: tobler_speeds_from_gradients(grades, 1.0, 1.0), CURVE_REFERENCE_GRADE)
    return CurveShape(
        minetti=_fitted_exponents(*minetti, log_limit, fills),
        tobler=_fitted_exponents(*tobler, log_limit, fills),
    )


# todo:
# TOBLER_THRESHOLDS, FLAT_EQUIVALENT_BAND_MPS and VERTICALITY_BAND are
# first-guess constants - the shape of resolve_tobler_weight is the point, the
# six numbers in it are not calibrated. RUNNING's verticality is the one to
# watch: at 0.14 a sustained 16% climb resolves to ~0.9 Tobler even at an
# elite pace, because the two criteria are OR'd.

# CURVE_FILLS, MAX_SPEED_RATIO_BOUNDS and HILLY_VERTICALITY are fitted by the
# tuning harness (2026-09-29) on limited and slightly biased data;
# the HILLY_VERTICALITY band and SMOOTHNESS_SCALES are still first
# guesses. The smoothness slider only moves max_speed_ratio: resolve_curve_shape
# already moves the exponents with it.
