"""Limit how fast the paced speed may change from one leg to the next.

Each leg is paced at one constant speed, so wherever the model's speed
changes it changes between two 1 Hz records, which no person does.
This turns every such jump into a ramp no steeper than a per-sport
maximum acceleration, and leaves everything that already changes gently alone.

Acceleration between two legs is measured between their midpoints: at a
constant acceleration `a`, v² changes linearly with distance, so

    |v[j+1]² − v[j]²| ≤ a · (Δs[j] + Δs[j+1])

is exact kinematics.

Each leg's modeled speed is treated as a ceiling: the most its terrain
allows. The limited profile is the fastest one that stays under every
ceiling and within the limit. Onto faster terrain you start speeding up
where it begins. Onto slower terrain, which you see coming, you have
already slowed down by the time you reach it. A short fast patch is only
partly used, since there isn't room to reach its full speed and slow down
again. A short slow patch is crossed at its own speed.
"""

import math

from gpx2fit.core.models import SportType


# the tuning harness's ≥70 m buckets can't see a ramp of a few seconds.
# Both make a typical step (a hiker 1.3 -> 0.8 m/s, a runner 3.0 -> 2.1 m/s)
# take about 3 s, which is about the same relative acceleration (~0.1/s) for both sports.
MAX_ACCELERATION_MPS2 = {
    SportType.RUNNING: 0.30,
    SportType.HIKING: 0.15,
}


def limit_acceleration(
    leg_distances: list[float],
    speeds: list[float],
    max_acceleration: float
) -> list[float]:
    """The fastest speeds, none above its leg's, with no two adjacent legs exceeding `max_acceleration`.

    Works in u = v², where constant acceleration is a straight line, so each
    pair of adjacent legs may differ by at most a · (Δs[j] + Δs[j+1]).

    A forward pass caps each leg at what the leg before it can reach by
    speeding up as hard as allowed. A backward pass then caps each leg at
    what it can slow down from in time for the leg after it. Both passes
    only ever lower a leg, and lowering one leg can't break a constraint
    the earlier pass already met, so after the two passes every pair is
    within the limit. Each lowered leg is exactly at the limit against a
    neighbor, so no leg is slowed more than it has to be.

    A zero-distance leg (a stop) keeps its speed and splits the track: there
    is no constraint across it, so a stop still starts and ends instantly.
    Legs that already satisfy the limit come back unchanged, bit for bit.

    Args:
        leg_distances: Each leg's length in metres, all >= 0.
        speeds: Each leg's absolute speed in m/s, all >= 0. Not modified.
        max_acceleration: The steepest allowed change of speed, in m/s².
    Returns:
        A new list of speeds, each <= the input's.
    Raises:
        ValueError: If the two lists differ in length or max_acceleration isn't positive.
    """
    if len(leg_distances) != len(speeds):
        raise ValueError(f"Got {len(leg_distances)} leg distances but {len(speeds)} speeds.")
    if max_acceleration <= 0:
        raise ValueError(f"max_acceleration must be positive, got {max_acceleration}.")

    squares = [speed * speed for speed in speeds]
    caps = [
        max_acceleration * (d0 + d1) if d0 > 0 and d1 > 0 else None
        for d0, d1 in zip(leg_distances, leg_distances[1:])
    ]
    for j, cap in enumerate(caps):
        if cap is not None and squares[j + 1] > squares[j] + cap:
            squares[j + 1] = squares[j] + cap
    for j in range(len(caps) - 1, -1, -1):
        cap = caps[j]
        if cap is not None and squares[j] > squares[j + 1] + cap:
            squares[j] = squares[j + 1] + cap

    # A correctly rounded square root of a rounded square gives back the
    # original float, so untouched legs come back the same.
    return [math.sqrt(square) for square in squares]

# todo: max accelerations are first guesses (2026-10-08)
