"""Per-leg gradient and gradient-based speed models used to shape pacing.

Both speed functions return *relative* speeds only — combine.py scales them
to match each anchor segment's known duration, so absolute magnitude here
doesn't matter, only the shape of speed vs. gradient.
"""

import math
from gpx2fit.core.models import Track

_TOBLER_MAX_SPEED_KMH = 6.0
_TOBLER_SLOPE_FACTOR = 3.5
_TOBLER_SLOPE_OFFSET = 0.05  # shifts peak speed to a gentle ~5% downhill grade
_KMH_PER_MPS = 3.6


def calculate_gradient(track: Track) -> list[float]:
    """Calculate the gradient of each leg between consecutive track points.

    Args:
        track: Track whose points already have elevation and distance_from_start set.

    Returns:
        One gradient per leg (N-1 values for N points), as a dimensionless
        rise/run ratio — e.g. 0.1 for a 10% grade, negative for downhill.
        A leg whose distance_from_start doesn't increase (zero-distance or
        out-of-order points) yields 0.0 rather than dividing by zero.
    """
    gradients = []
    for prev, curr in zip(track.points, track.points[1:]):
        delta_elevation = curr.elevation - prev.elevation
        delta_distance = curr.distance_from_start - prev.distance_from_start
        if delta_distance > 0:
            gradient = delta_elevation / delta_distance
        else:
            gradient = 0.0
        gradients.append(gradient)
    return gradients


def calculate_minetti_speeds(track: Track) -> list[float]:
    """Calculate per-leg Minetti-inspired speeds (m/s) from track gradients.

    Uses Minetti et al.'s (2002) polynomial approximation of the energy cost
    of locomotion C(g) in J/(kg·m) as a function of gradient g (rise/run
    fraction), then converts to speed by inverting cost: speed ~ 1 / C(g).
    Absolute speed is intentionally relative because final time scaling is
    handled in combine.py.

    Args:
        track: Track whose points already have elevation and distance_from_start set.

    Returns:
        One relative speed value per leg. The polynomial is only calibrated
        for gradients roughly within [-0.45, 0.45]; outside that range (or
        wherever it predicts a non-physical cost <= 0) this returns 0.0 for
        that leg instead of a nonsensical or infinite speed.
    """
    gradients = calculate_gradient(track)
    speeds_mps: list[float] = []
    for gradient in gradients:
        cost = (
            155.4 * (gradient ** 5)
            - 30.4 * (gradient ** 4)
            - 43.3 * (gradient ** 3)
            + 46.3 * (gradient ** 2)
            + 19.5 * gradient
            + 3.6
        )
        if cost <= 0:
            speeds_mps.append(0.0)
            continue
        speeds_mps.append(1.0 / cost)
    return speeds_mps


def calculate_tobler_speeds(track: Track) -> list[float]:
    """Calculate per-leg Tobler hiking-function speeds (m/s) from track gradients.

    Implements Tobler's (1993) hiking function:
    speed_kmh = 6 * exp(-3.5 * |gradient + 0.05|), which peaks on a gentle
    downhill (around -5% grade) and falls off for steeper climbs or descents.

    Args:
        track: Track whose points already have elevation and distance_from_start set.

    Returns:
        One relative speed value per leg between consecutive points.
    """
    gradients = calculate_gradient(track)
    speeds_mps: list[float] = []
    for gradient in gradients:
        speed_kmh = _TOBLER_MAX_SPEED_KMH * math.exp(-_TOBLER_SLOPE_FACTOR * abs(gradient + _TOBLER_SLOPE_OFFSET))
        speeds_mps.append(speed_kmh / _KMH_PER_MPS)
    return speeds_mps
