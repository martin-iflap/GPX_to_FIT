import math
from gpx2fit.core.models import Track



def calculate_gradient(track: Track) -> list[float]:
    """Calculate the gradient of the track based on elevation and distance between points."""
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

    Uses a gradient-cost curve C(g), then converts to speed by inverting cost:
    speed ~ 1 / C(g). Absolute speed is intentionally relative because final
    time scaling is handled in combine.py.
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
    """Calculate per-leg Tobler speeds (m/s) from track gradients.

    Returns one speed value per leg between consecutive points.
    """
    gradients = calculate_gradient(track)
    speeds_mps: list[float] = []
    for gradient in gradients:
        speed_kmh = 6.0 * math.exp(-3.5 * abs(gradient + 0.05))
        speeds_mps.append(speed_kmh / 3.6)
    return speeds_mps
