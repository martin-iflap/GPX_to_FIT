"""Build and resolve Anchors: the one mechanism for "known timestamp at some distance along the route"."""

import math
from datetime import datetime, timedelta

from gpx2fit.core.models import Anchor, InputError, RawAnchor, Track, TrackPoint

_EARTH_RADIUS_M = 6371000.0


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


def nearest_point_distance_from_start(track: Track, lat: float, lon: float) -> float:
    """Find the track point nearest to a lat/lon and return its distance_from_start.

    Args:
        track: Track to search. Must contain at least one point.
        lat: Latitude to match against, in degrees.
        lon: Longitude to match against, in degrees.

    Returns:
        The distance_from_start of the nearest point in the track.
    Raises:
        ValueError: If the track has no points.

    Note:
        On an out-and-back route, several points can be equally close to
        (lat, lon) — one on the outbound leg, one on the return. Ties are
        broken by taking the first such point in track order. Use
        `nearest_point_candidates` instead if the caller needs to
        disambiguate between such passes.
    """
    if not track.points:
        raise ValueError("Track must contain points to resolve anchor position.")
    nearest_point = min(track.points, key=lambda p: distance_meters(p.lat, p.lon, lat, lon))
    return nearest_point.distance_from_start


def nearest_point_candidates(
    track: Track,
    lat: float,
    lon: float,
    proximity_radius_m: float = 25.0,
    cluster_gap_m: float = 50.0,
    max_candidates: int = 4,
) -> list[dict]:
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

    A normal (non-overlapping) route always yields exactly one candidate, so
    this is a drop-in replacement for a plain nearest-point lookup when the
    caller only wants a single result.

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
        A list of at most max_candidates dicts, each with keys
        "distance_from_start", "lat", "lon", sorted by distance_from_start.
    Raises:
        ValueError: If the track has no points.
    """
    if not track.points:
        raise ValueError("Track must contain points to resolve anchor position.")

    nearest_point = min(track.points, key=lambda p: distance_meters(p.lat, p.lon, lat, lon))

    nearby_points = [
        p for p in track.points
        if distance_meters(p.lat, p.lon, nearest_point.lat, nearest_point.lon) <= proximity_radius_m
    ]
    nearby_points.sort(key=lambda p: p.distance_from_start)

    clusters: list[list[TrackPoint]] = []
    for point in nearby_points:
        if clusters and point.distance_from_start - clusters[-1][-1].distance_from_start <= cluster_gap_m:
            clusters[-1].append(point)
        else:
            clusters.append([point])

    representatives = [
        min(cluster, key=lambda p: distance_meters(p.lat, p.lon, lat, lon)) for cluster in clusters
    ]
    representatives.sort(key=lambda p: p.distance_from_start)

    return [
        {"distance_from_start": p.distance_from_start, "lat": p.lat, "lon": p.lon}
        for p in representatives[:max_candidates]
    ]


def add_start_end_anchors(
    track: Track,
    start_time: datetime,
    end_time: datetime | None = None,
    duration: timedelta | None = None,
) -> list[Anchor]:
    """Build the two boundary anchors every track needs: its start and end.

    Args:
        track: Track to anchor. Must contain at least one point.
        start_time: Timestamp for the track's first point.
        end_time: Timestamp for the track's last point. Takes priority over
            duration if both are given.
        duration: Elapsed time from start_time to the track's last point.
            Only used if end_time is not given.

    Returns:
        A two-element list: an Anchor at distance 0.0 (start_time) with
        source="start", and an Anchor at the track's total_distance
        (end_time, or start_time + duration) with source="end". The sources
        let error messages name "the start"/"the finish" instead of a bare
        distance.
    Raises:
        ValueError: If the track has no points, or if neither end_time nor
            duration is provided.
    """
    if not track.points:
        raise ValueError("Track must contain points to build start/end anchors.")

    if end_time is not None:
        resolved_end_time = end_time
    elif duration is not None:
        resolved_end_time = start_time + duration
    else:
        raise ValueError("Either end_time or duration must be provided.")

    return [
        Anchor(distance_from_start=0.0, timestamp=start_time, source="start"),
        Anchor(distance_from_start=track.total_distance, timestamp=resolved_end_time, source="end"),
    ]


def route_end_collision_message(what: str, source: str) -> str | None:
    """User-facing InputError text for `what` landing on the route's start or finish.

    Args:
        what: What collided, as the sentence's subject (e.g. "An anchor").
        source: The existing anchor's source it collided with.
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
    """Resolve frontend-provided anchor candidates to concrete Anchor objects.

    For each RawAnchor: distance_from_start is used directly if given,
    otherwise it's resolved from lat/lon via a nearest-point lookup
    (see `nearest_point_distance_from_start`). Two anchors resolving to the
    same distance would later make pacing.combine's anchor-to-point mapping
    ambiguous (which anchor's timestamp belongs to which point?), so that
    coincidence is rejected here instead — mirroring how
    pacing.stops.resolve_stops guards stop distances against `hard_anchors`.

    Args:
        track: Track the anchors belong to, used for lat/lon resolution and
            bounds validation.
        raw_anchors: Frontend-provided anchor candidates.
        existing_anchors: Anchors already fixed for this conversion (for
            example the boundary anchors from add_start_end_anchors),
            checked only to reject a raw anchor whose resolved distance
            coincides with one of them.

    Returns:
        One Anchor per raw anchor, sorted by (distance_from_start, timestamp).
    Raises:
        ValueError: If a raw anchor provides neither distance_from_start nor
            lat/lon, or if a resolved distance falls outside
            [0, track.total_distance].
        InputError: If a raw anchor's resolved distance coincides with
            another raw anchor's or an existing anchor's distance — a
            user-fixable problem (e.g. two anchors placed on top of each
            other), unlike the ValueError cases above.
    """
    anchors: list[Anchor] = []
    # Distance → source of whatever already sits there, so a collision with
    # the start or finish can be named as such.
    seen_distances = {a.distance_from_start: a.source for a in (existing_anchors or [])}
    for raw in raw_anchors:
        if raw.distance_from_start is not None:
            distance = raw.distance_from_start
        elif raw.lat is not None and raw.lon is not None:
            distance = nearest_point_distance_from_start(track, raw.lat, raw.lon)
        else:
            raise ValueError("Each raw anchor must provide distance_from_start or lat/lon.")

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
