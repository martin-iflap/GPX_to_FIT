from gpx2fit.models import TrackPoint, Track
import gpxpy



def parse_gpx_bytes(gpx_bytes: bytes, device: str | None = None) -> Track:
    """Parse GPX file contents (as bytes) and return a list of TrackPoint objects."""
    xml_text = gpx_bytes.decode('utf-8')
    gpx = gpxpy.parse(xml_text)

    track_points = []
    prev_point = None
    cumulative_distance = 0.0
    device = device or gpx.creator

    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                if prev_point is not None:
                    segment_distance = prev_point.distance_3d(point)
                    if segment_distance is None:
                        segment_distance = prev_point.distance_2d(point) or 0.0
                    cumulative_distance += segment_distance

                track_points.append(TrackPoint(
                    lat=point.latitude,
                    lon=point.longitude,
                    elevation=point.elevation,
                    distance_from_start=cumulative_distance,
                    timestamp=None,
                ))
                prev_point = point

    return Track(points=track_points, device=device)
