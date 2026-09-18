"""Decide which gradient-speed curve a workout should be paced with.

Separate from combine.py on purpose: choosing a curve is a property of the
whole activity (how slow, how steep), while combine.py's job is fitting an
already-chosen curve to known anchor timestamps.
"""

from dataclasses import dataclass

from gpx2fit.core.models import SportType
from gpx2fit.core.pacing.gradient import minetti_speeds_from_gradients


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

    climb = sum(gradient * distance for gradient, distance in zip(gradients, leg_distances) if gradient > 0)
    descent = -sum(gradient * distance for gradient, distance in zip(gradients, leg_distances) if gradient < 0)
    verticality = (climb + descent) / total_distance

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


# todo:
# TOBLER_THRESHOLDS, FLAT_EQUIVALENT_BAND_MPS and VERTICALITY_BAND are
# first-guess constants - the shape of resolve_tobler_weight is the point, the
# six numbers in it are not calibrated. RUNNING's verticality is the one to
# watch: at 0.14 a sustained 16% climb resolves to ~0.9 Tobler even at an
# elite pace, because the two criteria are OR'd.
