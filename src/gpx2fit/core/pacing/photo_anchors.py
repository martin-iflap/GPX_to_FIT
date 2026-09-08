"""Resolve frontend-extracted photo GPS/timestamp readings against the track.
The frontend only extracts EXIF GPS + timestamp from a dropped photo.
"""

from gpx2fit.core.models import Track, RawPhotoAnchor, ResolvedPhotoAnchor
from gpx2fit.core.pacing.anchors import distance_meters, nearest_point_candidates

# Photos whose nearest route point is farther than this from the photo's own
# GPS fix are treated as unrelated to the route (e.g. a photo taken at home
# before the hike) rather than forced onto that point anyway.
MAX_MATCH_DISTANCE_M = 150.0


def resolve_photo_anchors(
    track: Track,
    raw_photo_anchors: list[RawPhotoAnchor],
    max_match_distance_m: float = MAX_MATCH_DISTANCE_M,
) -> list[ResolvedPhotoAnchor]:
    """Match each photo's GPS reading to the nearest point on the track.

    Reuses `nearest_point_candidates` (the same lookup manual map-click
    anchors use) rather than a plain nearest-point search, so a photo taken
    near an out-and-back route's overlap still resolves to whichever pass is
    physically closest to its own GPS fix, not just the first one in route
    order.

    Args:
        track: Track to match against. Must contain at least one point.
        raw_photo_anchors: Photo GPS/timestamp readings to resolve.
        max_match_distance_m: Maximum allowed gap between a photo's GPS and
            its matched track point for the match to be trusted.

    Returns:
        One ResolvedPhotoAnchor per raw photo anchor, in the same order.
        `status` is "too_far" (rather than raising) when the nearest point is
        farther than max_match_distance_m — the caller decides whether to use
        it, matching how the frontend already surfaces bad matches per-photo
        instead of failing the whole batch.
    Raises:
        ValueError: If the track has no points.
    """
    results: list[ResolvedPhotoAnchor] = []
    for raw in raw_photo_anchors:
        candidates = nearest_point_candidates(track, raw.lat, raw.lon)
        nearest = min(candidates, key=lambda c: distance_meters(raw.lat, raw.lon, c["lat"], c["lon"]))
        gap = distance_meters(raw.lat, raw.lon, nearest["lat"], nearest["lon"])
        results.append(
            ResolvedPhotoAnchor(
                distance_from_start=nearest["distance_from_start"],
                lat=nearest["lat"],
                lon=nearest["lon"],
                timestamp=raw.timestamp,
                status="ok" if gap <= max_match_distance_m else "too_far",
                gap_m=gap,
            )
        )
    return results