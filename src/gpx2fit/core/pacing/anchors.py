"""Build and resolve Anchors: the one mechanism for "known timestamp at some distance along the route"."""

import math
from datetime import datetime, timedelta

from gpx2fit.core.models import Anchor, AnchorSource, Candidate, InputError, RawAnchor, Track

_EARTH_RADIUS_M = 6371000.0

# Slack on nearest_point_candidates' prefilter, so rounding can never drop a
# point the exact check after it would keep. Far above any rounding error, and
# costs nothing, since the exact check still decides.
_REACH_ROUNDING_MARGIN_M = 1.0


def distance_meters(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    """Calculate the great-circle (Haversine) distance between two lat/lon points.
     - Also used in pacing.photo_anchors to match photos to the nearest track point.

    Args:
        lat_a: Latitude of the first point, in degrees.
        lon_a: Longitude of the first point, in degrees.
        lat_b: Latitude of the second point, in degrees.
        lon_b: Longitude of the second point, in degrees.
    Returns:
        Distance between the two points in meters, assuming a spherical Earth.
    """
    phi1, phi2 = math.radians(lat_a), math.radians(lat_b)
    d_phi = math.radians(lat_b - lat_a)
    d_lambda = math.radians(lon_b - lon_a)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def nearest_point_candidates(
    track: Track,
    lat: float,
    lon: float,
    proximity_radius_m: float = 25.0,
    cluster_gap_m: float = 50.0,
    max_candidates: int = 4,
) -> list[Candidate]:
    """Find every distinct pass of the route near (lat, lon).

    Disambiguates out-and-back routes, where the outbound and return legs run
    through the same physical spot but are far apart in distance_from_start:

    - Finds the single closest point, then every other point within
      proximity_radius_m of that same physical spot.
    - Groups those points by distance_from_start, starting a new cluster
      whenever consecutive points (in route order) are more than
      cluster_gap_m apart — each cluster is one distinct visit to this spot.
    - Returns one representative per cluster (the point in that cluster
      closest to the original click), sorted by distance_from_start, capped
      at max_candidates.

    A normal (non-overlapping) route always yields exactly one candidate.
    There is deliberately no single-match lookup: on an out-and-back it
    would quietly take one pass, so the caller has to choose.

    Args:
        track: Track to search. Must contain at least one point.
        lat: Latitude to match against, in degrees.
        lon: Longitude to match against, in degrees.
        proximity_radius_m: Points within this distance of the closest point
            are considered part of the same physical spot.
        cluster_gap_m: Minimum gap, in distance_from_start, between two
            nearby points for them to be treated as separate passes.
        max_candidates: Maximum number of candidates to return.

    Returns:
        At most max_candidates Candidates, sorted by distance_from_start.
    Raises:
        ValueError: If the track has no points.
    """
    points = track.points
    if not points:
        raise ValueError("Track must contain points to resolve anchor position.")

    # Measured once: it picks both the nearest point and each pass's representative.
    to_click = [distance_meters(p.lat, p.lon, lat, lon) for p in points]
    nearest_index = min(range(len(points)), key=to_click.__getitem__)
    nearest = points[nearest_index]

    # A point within proximity_radius_m of the nearest point is, by the
    # triangle inequality, within `reach` of the click, so only the few
    # points that close need measuring against the nearest one. They stay in
    # route order, which is distance_from_start order.
    reach = to_click[nearest_index] + proximity_radius_m + _REACH_ROUNDING_MARGIN_M
    nearby = [
        i for i, d in enumerate(to_click)
        if d <= reach and distance_meters(points[i].lat, points[i].lon, nearest.lat, nearest.lon) <= proximity_radius_m
    ]

    clusters: list[list[int]] = []
    for i in nearby:
        if clusters and points[i].distance_from_start - points[clusters[-1][-1]].distance_from_start <= cluster_gap_m:
            clusters[-1].append(i)
        else:
            clusters.append([i])

    # The clusters follow each other along the route, so their representatives are in route order already.
    representatives = [points[min(cluster, key=to_click.__getitem__)] for cluster in clusters[:max_candidates]]
    return [Candidate(distance_from_start=p.distance_from_start, lat=p.lat, lon=p.lon) for p in representatives]


def add_start_end_anchors(track: Track, start_time: datetime, duration: timedelta) -> list[Anchor]:
    """Build the two boundary anchors every track needs: its start and end.

    Args:
        track: Track to anchor. Must contain at least one point.
        start_time: Timestamp for the track's first point.
        duration: Elapsed time from start_time to the track's last point.

    Returns:
        A two-element list: an Anchor at distance 0.0 (start_time) with
        source="start", and an Anchor at the track's total_distance
        (start_time + duration) with source="end". The sources let error
        messages name "the start"/"the finish" instead of a bare distance.
    Raises:
        ValueError: If the track has no points.
        InputError: If the route has no length (one point, or every point in
            the same place): the end anchor would sit on the start, leaving
            nothing to pace.
    """
    if not track.points:
        raise ValueError("Track must contain points to build start/end anchors.")
    if track.total_distance <= 0:
        raise InputError(
            "This route has no length (it's a single point, or every point is in the same place), "
            "so there's nothing to pace. Load a GPX file with the full track."
        )

    return [
        Anchor(distance_from_start=0.0, timestamp=start_time, source="start"),
        Anchor(distance_from_start=track.total_distance, timestamp=start_time + duration, source="end"),
    ]


def route_end_collision_message(what: str, source: AnchorSource | None) -> str | None:
    """User-facing InputError text for `what` landing on the route's start or finish.

    Args:
        what: What collided, as the sentence's subject (e.g. "An anchor").
        source: The source of the anchor it collided with, or None for a stop.
    Returns:
        The message when `source` is "start" or "end", otherwise None (a
        collision between two mid-route entries keeps its caller's wording).
    """
    if source == "start":
        return (
            f"{what} lands on the route's start, which already has the start time you entered. "
            "Move it a little further along the route, or change the start time instead."
        )
    if source == "end":
        return (
            f"{what} lands on the route's finish, which already has the activity's end time. "
            "Move it a little earlier along the route, or change the end time instead."
        )
    return None


def build_user_anchors(
    track: Track,
    raw_anchors: list[RawAnchor],
    existing_anchors: list[Anchor] | None = None,
) -> list[Anchor]:
    """Check frontend-provided anchors against the route and turn them into Anchors.

    Two anchors at the same distance would later make pacing.combine's
    anchor-to-point mapping ambiguous (which anchor's timestamp belongs to
    which point?), so that coincidence is rejected here instead — mirroring how
    pacing.stops.resolve_stops guards stop distances against `hard_anchors`.

    Args:
        track: Track the anchors belong to, for the bounds check.
        raw_anchors: Frontend-provided anchors.
        existing_anchors: Anchors already fixed for this conversion (for
            example the boundary anchors from add_start_end_anchors),
            checked only to reject a raw anchor whose distance coincides
            with one of them.

    Returns:
        One Anchor per raw anchor, sorted by (distance_from_start, timestamp).
    Raises:
        ValueError: If a distance falls outside [0, track.total_distance].
        InputError: If a raw anchor's distance coincides with another raw
            anchor's or an existing anchor's — a user-fixable problem (e.g.
            two anchors placed on top of each other), unlike the ValueError.
    """
    anchors: list[Anchor] = []
    # Distance → source of whatever already sits there, so a collision with
    # the start or finish can be named as such.
    seen_distances: dict[float, AnchorSource] = {a.distance_from_start: a.source for a in (existing_anchors or [])}
    for raw in raw_anchors:
        distance = raw.distance_from_start
        if distance < 0 or distance > track.total_distance:
            raise ValueError("Anchor distance_from_start is outside track bounds.")
        if distance in seen_distances:
            what = "A photo anchor" if raw.source == "photo" else "An anchor"
            raise InputError(
                route_end_collision_message(what, seen_distances[distance])
                or f"Two anchors land on the exact same point on the route ({distance / 1000:.2f} km from the "
                "start). Move one of them slightly, or remove the duplicate."
            )
        seen_distances[distance] = raw.source

        anchors.append(Anchor(distance_from_start=distance, timestamp=raw.timestamp, source=raw.source))

    anchors.sort(key=lambda anchor: (anchor.distance_from_start, anchor.timestamp))
    return anchors
