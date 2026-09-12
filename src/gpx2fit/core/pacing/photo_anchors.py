"""Resolve frontend-extracted photo GPS/timestamp readings against the track.
The frontend only extracts EXIF GPS + timestamp from a dropped photo.
"""

from datetime import datetime, timedelta

from gpx2fit.core.models import Track, RawPhotoAnchor, ResolvedPhotoAnchor
from gpx2fit.core.pacing.anchors import distance_meters, nearest_point_candidates

# Photos whose nearest route point is farther than this from the photo's own
# GPS fix are treated as unrelated to the route (e.g. a photo taken at home
# before the hike) rather than forced onto that point anyway.
MAX_MATCH_DISTANCE_M = 150.0

# A photo's own clock (rather than GPS time) drives its EXIF timestamp and is
# rarely synced to the second, so the activity window is padded by this much
# on each side before a photo is rejected as outside it. This is about
# ordinary clock drift, not about tolerating a wrong date — a photo taken
# hours or days off the activity is still rejected.
ACTIVITY_TIME_TOLERANCE = timedelta(minutes=15) # todo: check this logic


def resolve_photo_anchors(
    track: Track,
    raw_photo_anchors: list[RawPhotoAnchor],
    max_match_distance_m: float = MAX_MATCH_DISTANCE_M,
    activity_start: datetime | None = None,
    activity_end: datetime | None = None,
    activity_time_tolerance: timedelta = ACTIVITY_TIME_TOLERANCE,
) -> list[ResolvedPhotoAnchor]:
    """Match each photo's GPS reading to the nearest point on the track.

    Reuses `nearest_point_candidates` (the same lookup manual map-click
    anchors use) rather than a plain nearest-point search, so a photo taken
    near an out-and-back route's overlap still resolves to whichever pass is
    physically closest to its own GPS fix, not just the first one in route
    order.

    A photo's GPS can coincidentally land near the route even when it has
    nothing to do with this activity (e.g. it was taken on a previous visit
    to the same trailhead, or the frontend's start-time field was simply
    never updated from its "now" default). Nothing about a geographic match
    alone can catch that — only the photo's own capture time, checked
    against the activity's actual time span, can. This is why
    `activity_start`/`activity_end` exist: a mismatched date here is exactly
    what previously let a photo anchor's timestamp land earlier than the
    route's start anchor, which pacing.combine then rejected only much
    later (and less clearly) as anchors with contradictory timestamps.

    Args:
        track: Track to match against. Must contain at least one point.
        raw_photo_anchors: Photo GPS/timestamp readings to resolve.
        max_match_distance_m: Maximum allowed gap between a photo's GPS and
            its matched track point for the match to be trusted.
        activity_start: Start of the activity's planned time span. If given
            together with `activity_end`, a photo captured outside
            [activity_start - activity_time_tolerance, activity_end +
            activity_time_tolerance] is rejected regardless of how well its
            GPS matches the route. Omit (with `activity_end`) to skip this
            check entirely, e.g. before the frontend has a valid start time.
        activity_end: End of the activity's planned time span. See
            `activity_start`.
        activity_time_tolerance: Padding applied to both ends of the
            activity window before rejecting a photo, to absorb ordinary
            camera clock drift rather than an actually-wrong date.

    Returns:
        One ResolvedPhotoAnchor per raw photo anchor, in the same order.
        `status` is "outside_activity_time" when `activity_start`/
        `activity_end` are given and the photo's timestamp falls outside the
        (padded) window — checked first, since a wrong date makes the
        geographic match meaningless either way. Otherwise, `status` is
        "too_far" (rather than raising) when the nearest point is farther
        than max_match_distance_m. In both cases the caller decides whether
        to use the anchor, matching how the frontend already surfaces bad
        matches per-photo instead of failing the whole batch.
    Raises:
        ValueError: If the track has no points.
    """
    results: list[ResolvedPhotoAnchor] = []
    for raw in raw_photo_anchors:
        candidates = nearest_point_candidates(track, raw.lat, raw.lon)
        nearest = min(candidates, key=lambda c: distance_meters(raw.lat, raw.lon, c["lat"], c["lon"]))
        gap = distance_meters(raw.lat, raw.lon, nearest["lat"], nearest["lon"])

        if (
            activity_start is not None
            and activity_end is not None
            and not (activity_start - activity_time_tolerance <= raw.timestamp <= activity_end + activity_time_tolerance)
        ):
            status = "outside_activity_time"
        else:
            status = "ok" if gap <= max_match_distance_m else "too_far"

        results.append(
            ResolvedPhotoAnchor(
                distance_from_start=nearest["distance_from_start"],
                lat=nearest["lat"],
                lon=nearest["lon"],
                timestamp=raw.timestamp,
                status=status,
                gap_m=gap,
            )
        )
    return results