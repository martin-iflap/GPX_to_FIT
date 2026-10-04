"""Read GPX route files and convert them into the core Track representation."""

import math

import gpxpy
from gpx2fit.core.models import InputError, Track, TrackPoint

# An elevation outside this range can't be on Earth's surface (the Dead Sea
# shore is about -430 m, Everest 8849 m) It's treated as missing.
MIN_PLAUSIBLE_ELEVATION_M = -500.0
MAX_PLAUSIBLE_ELEVATION_M = 9000.0


def _usable_elevation(elevation: float | None) -> float | None:
    """The elevation if it's a plausible terrain height, otherwise None (missing)."""
    if elevation is None or not math.isfinite(elevation):
        return None
    if not MIN_PLAUSIBLE_ELEVATION_M <= elevation <= MAX_PLAUSIBLE_ELEVATION_M:
        return None
    return elevation


def parse_gpx_bytes(gpx_bytes: bytes, device: str | None = None) -> Track:
    """Parse GPX file contents into a Track.

    Args:
        gpx_bytes: Raw contents of a .gpx file.
        device: Device name to embed in the resulting Track. If not given,
            falls back to the GPX file's <creator> attribute.
    Returns:
        A Track with one TrackPoint per GPX trackpoint, in file order, with
        distance_from_start computed as the cumulative 3D (falling back to
        2D) distance from the first point. Points have no timestamp yet —
        that's assigned later by pacing.
    Raises:
        InputError: If gpx_bytes isn't valid UTF-8, isn't parseable as GPX,
            contains no track points, or describes a route with no length
            (a single point, or every point in the same place) — all signs of
            a corrupt file or one that isn't actually a GPX track export.

    Note:
        A missing elevation, or one outside MIN/MAX_PLAUSIBLE_ELEVATION_M,
        inherits the previous point's elevation rather than introducing a fake
        cliff that would distort gradient-based pacing. Points before the
        first usable elevation take that first value. If no point has one, every
        elevation is 0.0 and the Track's has_elevation is False.
    """
    try:
        xml_text = gpx_bytes.decode('utf-8')
    except UnicodeDecodeError as e:
        raise InputError("This doesn't look like a valid GPX file (not readable as UTF-8 text).") from e

    try:
        gpx = gpxpy.parse(xml_text)
    except Exception as e:
        raise InputError(f"Could not parse this file as GPX: {e}") from e

    track_points = []
    prev_point = None
    cumulative_distance = 0.0
    last_elevation: float | None = None
    leading_missing = 0  # points before the first usable elevation
    device = device or gpx.creator

    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                if prev_point is not None:
                    segment_distance = prev_point.distance_3d(point)
                    if segment_distance is None:
                        segment_distance = prev_point.distance_2d(point) or 0.0
                    cumulative_distance += segment_distance

                elevation = _usable_elevation(point.elevation)
                if elevation is None:
                    elevation = last_elevation
                if elevation is None:
                    leading_missing += 1
                last_elevation = elevation

                track_points.append(TrackPoint(
                    lat=point.latitude,
                    lon=point.longitude,
                    elevation=0.0 if elevation is None else elevation,
                    distance_from_start=cumulative_distance,
                    timestamp=None,
                ))
                prev_point = point

    if not track_points:
        raise InputError("This GPX file doesn't contain any track points.")
    if len(track_points) < 2:
        raise InputError(
            "This GPX file has only one point, so there's no route to pace. Export the full track and try again."
        )
    if cumulative_distance <= 0:
        raise InputError(
            "This GPX route has no length: every point is in the same place, so there's nothing to pace. "
            "Export the full track and try again."
        )

    has_elevation = last_elevation is not None
    if has_elevation:
        first_elevation = track_points[leading_missing].elevation
        for track_point in track_points[:leading_missing]:
            track_point.elevation = first_elevation

    return Track(points=track_points, device=device, has_elevation=has_elevation)
