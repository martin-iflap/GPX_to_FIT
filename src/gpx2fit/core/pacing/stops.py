"""Resolve Stops (real pauses at one location) into either a concrete
arrival/departure Anchor pair (Mode B) or a pending distance+duration fact
(Mode A, timed later by pacing.combine). And expand a track's points so a
stop shows up as a genuine zero-movement gap rather than folding into an
adjacent leg's pace.
"""

from dataclasses import replace

from gpx2fit.core.models import Anchor, ModeAStop, RawStop, ResolvedStop, Track, TrackPoint
from gpx2fit.core.pacing.anchors import nearest_point_distance_from_start


def _resolve_distance(track: Track, raw: RawStop) -> float:
    """Resolve a raw stop's distance_from_start from either its explicit value or its lat/lon.
    Args:
        track: The track the stop belongs to.
        raw: The raw stop to resolve.
    Returns:
        The resolved distance_from_start in meters.
    Raises:
        ValueError: If the raw stop provides neither distance_from_start nor lat/lon.
    """
    if raw.distance_from_start is not None:
        distance = raw.distance_from_start
    elif raw.lat is not None and raw.lon is not None:
        distance = nearest_point_distance_from_start(track, raw.lat, raw.lon)
    else:
        raise ValueError("Each raw stop must provide distance_from_start or lat/lon.")

    if distance < 0 or distance > track.total_distance:
        raise ValueError("Stop distance_from_start is outside track bounds.")
    return distance


def resolve_stops(
    track: Track,
    raw_stops: list[RawStop],
    hard_anchors: list[Anchor],
) -> tuple[list[ResolvedStop], list[ModeAStop]]:
    """Resolve frontend-provided stop candidates to distances, splitting by mode.

    Each raw stop is either Mode B (explicit `start_timestamp`/
    `end_timestamp`): resolved directly to a `ResolvedStop`, or Mode A
    (`duration` only): its arrival time isn't known yet — it's derived later
    by pacing.combine from the normal gradient-based pacing model, as if it
    were an ordinary unconstrained point, with departure = arrival +
    duration. This function only resolves each stop's distance and returns
    the Mode A stops as plain distance+duration facts (`ModeAStop`).

    Args:
        track: The track the stops belong to (used for distance resolution
            via nearest-point lookup). Not mutated.
        raw_stops: Frontend-provided stop candidates.
        hard_anchors: Boundary + user anchors already resolved for this
            conversion, used only to reject a stop coinciding with one.

    Returns:
        (mode_b, mode_a): Mode B stops resolved to concrete ResolvedStops,
        and Mode A stops as pending ModeAStops — each list sorted by
        distance_from_start.
    Raises:
        ValueError: If a raw stop provides neither distance_from_start nor
            lat/lon, a resolved distance falls outside track bounds,
            neither/both of duration and (start_timestamp, end_timestamp)
            are given, end_timestamp isn't later than start_timestamp, or a
            stop's distance coincides with a hard anchor's or another
            stop's.
    """
    mode_a: list[ModeAStop] = []
    mode_b: list[ResolvedStop] = []

    seen_distances = {a.distance_from_start for a in hard_anchors}

    for raw in raw_stops:
        distance = _resolve_distance(track, raw)
        if distance in seen_distances:
            raise ValueError(f"Stop distance {distance}m coincides with an existing anchor or stop.")
        seen_distances.add(distance)

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
                raise ValueError("Stop end_timestamp must be later than start_timestamp.")
            mode_b.append(ResolvedStop(distance, raw.start_timestamp, raw.end_timestamp))

    mode_a.sort(key=lambda s: s.distance_from_start)
    mode_b.sort(key=lambda s: s.distance_from_start)
    return mode_b, mode_a


def expand_track_with_stops(points: list[TrackPoint], stops: list[ResolvedStop | ModeAStop]) -> list[TrackPoint]:
    """Return a new point list with one duplicate point inserted per stop.

    Each stop's resolved distance must match an existing point's
    distance_from_start exactly (guaranteed by resolve_stops, since stops
    are always resolved against the same track's points); a fresh duplicate
    with the same lat/lon/elevation/distance_from_start (and no timestamp)
    is inserted immediately after it, so combine() can stamp the original
    with the arrival time and the duplicate with the departure time.

    Args:
        points: The track's points, in route order. Not mutated.
        stops: Resolved and/or pending stops, in any order.

    Returns:
        A new list of points, one longer per stop.
    Raises:
        ValueError: If a stop's distance matches no point in `points`.
    """
    pending = {s.distance_from_start: s for s in stops}
    result: list[TrackPoint] = []
    for point in points:
        result.append(point)
        if pending.pop(point.distance_from_start, None) is not None:
            result.append(replace(point, timestamp=None))
    if pending:
        raise ValueError(f"No track point found at stop distance {next(iter(pending))}m.")
    return result
