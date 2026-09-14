"""Read GPX route files and convert them into the core Track representation."""

import gpxpy
from gpx2fit.core.models import InputError, Track, TrackPoint


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
            or contains no track points — all signs of a corrupt file or one
            that isn't actually a GPX track export.

    Note:
        If a point is missing elevation, it inherits the previous point's
        elevation (0.0 if it's the very first point) rather than introducing
        a fake cliff that would distort gradient-based pacing.
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
    last_elevation = 0.0
    device = device or gpx.creator

    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                if prev_point is not None:
                    segment_distance = prev_point.distance_3d(point)
                    if segment_distance is None:
                        segment_distance = prev_point.distance_2d(point) or 0.0
                    cumulative_distance += segment_distance

                elevation = point.elevation if point.elevation is not None else last_elevation
                last_elevation = elevation

                track_points.append(TrackPoint(
                    lat=point.latitude,
                    lon=point.longitude,
                    elevation=elevation,
                    distance_from_start=cumulative_distance,
                    timestamp=None,
                ))
                prev_point = point

    if not track_points:
        raise InputError("This GPX file doesn't contain any track points.")

    return Track(points=track_points, device=device)
