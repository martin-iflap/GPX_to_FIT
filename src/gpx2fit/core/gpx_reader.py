"""Read GPX route files and convert them into the core Track representation."""

import math

import gpxpy
from gpxpy.geo import distance as geo_distance

from gpx2fit.core.models import InputError, Track, TrackPoint

# An elevation outside this range can't be on Earth's surface (the Dead Sea
# shore is about -430 m, Everest 8849 m) It's treated as missing.
MIN_PLAUSIBLE_ELEVATION_M = -500.0
MAX_PLAUSIBLE_ELEVATION_M = 9000.0

# Successive <trk>/<trkseg>s are joined with a straight leg. A join longer than
# this is reported to the user. A paused recording's export usually restarts
# where it stopped, so a real gap is something else (a lift, a car, a missing
# chunk). A first guess.
SEGMENT_GAP_WARNING_M = 100.0


def _usable_elevation(elevation: float | None) -> float | None:
    """The elevation if it's a plausible terrain height, otherwise None (missing)."""
    if elevation is None or not math.isfinite(elevation):
        return None
    if not MIN_PLAUSIBLE_ELEVATION_M <= elevation <= MAX_PLAUSIBLE_ELEVATION_M:
        return None
    return elevation


def _gap_warning(gaps_m: list[float]) -> str:
    count = len(gaps_m)
    noun = "gap" if count == 1 else "gaps"
    width = f"{max(gaps_m) / 1000:.2f} km wide" if count == 1 else f"the widest {max(gaps_m) / 1000:.2f} km"
    joined = "It's" if count == 1 else "Each is"
    return (
        f"This GPX has {count} {noun} between its track segments, {width}. {joined} joined with a straight "
        "line and paced like the rest of the route. If that's not where you went, fix the route before converting."
    )


# Parsing splits every leg longer than this, so pacing, anchors and stops are
# as accurate on a sparse GPX file as on a dense one. 2 m is about 1.5 s for a
# hiker and 0.7 s for a runner: short enough for pacing.acceleration to ramp a
# change of speed over a few legs, rather than leaving it a jump between two
# 1 Hz records. The tuning harness densifies its recordings the same way.
MAX_LEG_M = 2.0

# Slack for floating-point distances, so a leg of exactly n x max_leg_m isn't cut
# into n + 1 pieces.
_LEG_TOLERANCE_M = 1e-6


def densify_points(points: list[TrackPoint], max_leg_m: float) -> list[TrackPoint]:
    """Split every leg longer than `max_leg_m` into equal pieces with straight-line points.

    A GPX says nothing about the path between two of its points, so the
    straight line is the best guess: latitude, longitude, elevation and
    distance_from_start are all interpolated linearly, by the same fraction.
    Equal pieces rather than full ones plus a remainder, so no leg ends up a
    sliver. Legs at or under the limit, including zero-distance ones, get no
    points.

    Args:
        points: Points in route order, with distance_from_start set.
        max_leg_m: The longest leg to leave alone, in metres.
    Returns:
        A new list holding every original point object, unchanged and in
        order, with the inserted points (no timestamp) between them. The
        input list isn't modified.
    Raises:
        ValueError: If max_leg_m isn't positive.
    """
    if max_leg_m <= 0:
        raise ValueError(f"max_leg_m must be positive, got {max_leg_m}.")

    dense = points[:1]
    for start, end in zip(points, points[1:]):
        leg_m = end.distance_from_start - start.distance_from_start
        pieces = math.ceil((leg_m - _LEG_TOLERANCE_M) / max_leg_m)
        for piece in range(1, pieces):
            fraction = piece / pieces
            dense.append(TrackPoint(
                lat=start.lat + (end.lat - start.lat) * fraction,
                lon=start.lon + (end.lon - start.lon) * fraction,
                elevation=start.elevation + (end.elevation - start.elevation) * fraction,
                distance_from_start=start.distance_from_start + leg_m * fraction,
            ))
        dense.append(end)
    return dense


def parse_gpx_bytes(
    gpx_bytes: bytes,
    device: str | None = None,
    *,
    max_leg_m: float | None = MAX_LEG_M,
    warnings: list[str] | None = None,
) -> Track:
    """Parse GPX file contents into a Track.

    Args:
        gpx_bytes: Raw contents of a .gpx file.
        device: Device name to embed in the resulting Track. If not given,
            falls back to the GPX file's <creator> attribute.
        max_leg_m: Legs longer than this are split with straight-line points
            (see densify_points). None keeps exactly the GPX's own points.
        warnings: If given, a user-facing message is appended when segments
            are joined across a gap longer than SEGMENT_GAP_WARNING_M. The
            parse itself is the same either way.
    Returns:
        A Track with one TrackPoint per GPX trackpoint, in file order, plus
        any points densifying inserts between them, with distance_from_start
        computed as the cumulative 3D distance from the first point, using
        the cleaned elevations (see Note), so a route without any is
        measured flat. Points have no timestamp yet — that's assigned later
        by pacing.
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
    gaps_m: list[float] = []  # joins between segments wider than SEGMENT_GAP_WARNING_M
    device = device or gpx.creator

    for track in gpx.tracks:
        for segment in track.segments:
            for index, point in enumerate(segment.points):
                elevation = _usable_elevation(point.elevation)
                if elevation is None:
                    elevation = last_elevation
                if elevation is None:
                    leading_missing += 1

                if prev_point is not None:
                    # gpxpy's 3D distance, but from the cleaned elevations: the file's own would turn a
                    # -32768 "no data" value into a 33 km leg, and a nan into a nan route. A leading
                    # point without one (None) is measured flat, as its back-filled value will be.
                    cumulative_distance += geo_distance(
                        prev_point.latitude, prev_point.longitude, last_elevation,
                        point.latitude, point.longitude, elevation,
                    )
                    if index == 0:
                        gap_m = geo_distance(
                            prev_point.latitude, prev_point.longitude, None, point.latitude, point.longitude, None
                        )
                        if gap_m > SEGMENT_GAP_WARNING_M:
                            gaps_m.append(gap_m)
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

    if gaps_m and warnings is not None:
        warnings.append(_gap_warning(gaps_m))

    # After the elevation back-fill, so inserted points interpolate real values.
    if max_leg_m is not None:
        track_points = densify_points(track_points, max_leg_m)

    return Track(points=track_points, device=device, has_elevation=has_elevation)

# todo: max_leg_m is a first guess.