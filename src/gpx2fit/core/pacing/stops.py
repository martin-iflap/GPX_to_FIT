"""Resolve Stops (real pauses at one location) into either a concrete
arrival/departure Anchor pair (Mode B) or a pending distance+duration fact
(Mode A, timed later by pacing.combine). And expand a track's points so a
stop shows up as a genuine zero-movement gap rather than folding into an
adjacent leg's pace.
"""

from dataclasses import replace

from gpx2fit.core.models import Anchor, AnchorSource, InputError, ModeAStop, RawStop, ResolvedStop, Track, TrackPoint
from gpx2fit.core.pacing.anchors import route_end_collision_message


def resolve_stops(
    track: Track,
    raw_stops: list[RawStop],
    hard_anchors: list[Anchor],
) -> tuple[list[ResolvedStop], list[ModeAStop]]:
    """Check frontend-provided stops against the route and the anchors, splitting them by mode.

    Each raw stop is either Mode B (explicit `start_timestamp`/
    `end_timestamp`): resolved directly to a `ResolvedStop`, or Mode A
    (`duration` only): its arrival time isn't known yet — it's derived later
    by pacing.combine from the normal gradient-based pacing model, as if it
    were an ordinary unconstrained point, with departure = arrival +
    duration. This function only checks each stop's distance and returns
    the Mode A stops as plain distance+duration facts (`ModeAStop`).

    Args:
        track: The track the stops belong to, for the bounds check. Not mutated.
        raw_stops: Frontend-provided stops.
        hard_anchors: Boundary + user anchors already resolved for this
            conversion, used only to reject a stop coinciding with one.

    Returns:
        (mode_b, mode_a): Mode B stops resolved to concrete ResolvedStops,
        and Mode A stops as pending ModeAStops — each list sorted by
        distance_from_start.
    Raises:
        ValueError: If a distance falls outside [0, track.total_distance],
            or neither/both of duration and (start_timestamp, end_timestamp)
            are given.
        InputError: If end_timestamp isn't later than start_timestamp, or a
            stop's distance coincides with a hard anchor's or another
            stop's — both user-fixable data problems, not bugs.
    """
    mode_a: list[ModeAStop] = []
    mode_b: list[ResolvedStop] = []

    # Distance → source of the anchor already there, or None for another stop.
    seen_distances: dict[float, AnchorSource | None] = {a.distance_from_start: a.source for a in hard_anchors}

    for raw in raw_stops:
        distance = raw.distance_from_start
        if distance < 0 or distance > track.total_distance:
            raise ValueError("Stop distance_from_start is outside track bounds.")
        if distance in seen_distances:
            raise InputError(
                route_end_collision_message("A stop", seen_distances[distance])
                or f"A stop at {distance / 1000:.2f} km lands on the exact same point as another anchor or stop. "
                "Move it slightly, or remove the duplicate."
            )
        seen_distances[distance] = None

        has_duration = raw.duration is not None
        has_start_end = raw.start_timestamp is not None and raw.end_timestamp is not None
        if has_duration == has_start_end:
            raise ValueError(
                "Each raw stop must provide exactly one of duration or (start_timestamp, end_timestamp)."
            )

        if raw.duration is not None:
            mode_a.append(ModeAStop(distance, raw.duration))
        elif raw.start_timestamp is not None and raw.end_timestamp is not None:
            if raw.end_timestamp <= raw.start_timestamp:
                raise InputError(
                    f"The stop at {distance / 1000:.2f} km has a departure time that isn't later than its "
                    "arrival time. Fix its arrival/departure times."
                )
            mode_b.append(ResolvedStop(distance, raw.start_timestamp, raw.end_timestamp))

    mode_a.sort(key=lambda s: s.distance_from_start)
    mode_b.sort(key=lambda s: s.distance_from_start)
    return mode_b, mode_a


def expand_track_with_stops(points: list[TrackPoint], stops: list[ResolvedStop | ModeAStop]) -> list[TrackPoint]:
    """Return a copy of the points with one duplicate point inserted per stop.

    Each stop's resolved distance must match an existing point's
    distance_from_start exactly (guaranteed by resolve_stops, since stops
    are always resolved against the same track's points); a fresh duplicate
    with the same lat/lon/elevation/distance_from_start (and no timestamp)
    is inserted immediately after it, so combine() can stamp the original
    with the arrival time and the duplicate with the departure time.

    Every point is copied, not just the duplicates: combine() stamps
    timestamps onto the result in place, and `points` is typically the
    bridge's persistent `_track`, which must stay untimed between conversions.

    Args:
        points: The track's points, in route order. Not mutated, and shares
            no point object with the result.
        stops: Resolved and/or pending stops, in any order.

    Returns:
        A new list of new points, one longer per stop.
    Raises:
        ValueError: If a stop's distance matches no point in `points`.
    """
    pending = {s.distance_from_start: s for s in stops}
    result: list[TrackPoint] = []
    for point in points:
        result.append(replace(point))
        if pending.pop(point.distance_from_start, None) is not None:
            result.append(replace(point, timestamp=None))
    if pending:
        raise ValueError(f"No track point found at stop distance {next(iter(pending))}m.")
    return result


def expand_multipliers_with_stops(
    points: list[TrackPoint],
    multipliers: list[float],
    stops: list[ResolvedStop | ModeAStop],
) -> list[float]:
    """Duplicate one multiplier entry per stop, mirroring expand_track_with_stops.

    expand_track_with_stops splits the leg leaving a stop's point in two: a
    new zero-distance leg (arrival -> departure), followed by the original
    leg now starting one point later. This duplicates that leg's multiplier
    for both, keeping the result aligned 1:1 with
    expand_track_with_stops(points, stops)'s legs — the zero-distance leg's
    copy is never actually used (its modeled time is zero regardless of
    speed, see gradient.py's zero-distance guard), and the real leg keeps
    the original surface multiplier.

    Args:
        points: The same (unexpanded) track points `multipliers` was
            computed against — one more point than `multipliers` has
            entries.
        multipliers: Per-leg multipliers, aligned to `points`.
        stops: Resolved and/or pending stops, in any order. A stop's
            distance is guaranteed (by resolve_stops, which rejects a stop
            coinciding with an anchor) to never fall on `points[0]` or
            `points[-1]`, so every stop always duplicates a real entry of
            `multipliers`.
    Returns:
        A new multipliers list, one longer per stop. Where the GPX repeats a
        stop's point, only the first copy's leg is duplicated, as only the
        first copy gets a duplicate point.
    """
    pending = {s.distance_from_start for s in stops}
    result: list[float] = []
    for point, multiplier in zip(points, multipliers):
        result.append(multiplier)
        if point.distance_from_start in pending:
            pending.remove(point.distance_from_start)
            result.append(multiplier)
    return result
