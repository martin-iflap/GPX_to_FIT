"""Reduce a fully-paced Track to a compact distance/time/speed/elevation profile for charting."""

from gpx2fit.core.models import ProfileSample, Track, TrackPoint

# Enough points for a smooth line across a wide screen, few enough that the
# GUI's SVG chart and the Pyodide -> JS conversion stay cheap on dense GPX files.
DEFAULT_MAX_SAMPLES = 1000


def build_activity_profile(track: Track, max_samples: int = DEFAULT_MAX_SAMPLES) -> list[ProfileSample]:
    """Downsample a fully-paced track into chartable samples.

    Legs are grouped into consecutive buckets of roughly
    `total_distance / max_samples` metres each. Every bucket becomes one
    sample at its last point, carrying the bucket's true average speed
    (distance / time). That smooths while downsampling, so no separate
    smoothing pass is needed.

    A stop (a zero-distance leg that still takes time, the same test
    fit_writer.write_fit uses for timer pauses) closes the current bucket and
    adds two zero-speed samples, one at arrival and one at departure. On a
    time axis that draws the stop as a flat stretch at zero, and on a
    distance axis as a drop to zero at one point.

    Args:
        track: A track whose points all have a timestamp, in route order (as
            pacing.combine leaves it).
        max_samples: Target number of moving samples. Each stop adds two more.
    Returns:
        Samples in route order. The first is at the track's first point, with
        the speed of the first bucket after it.
    Raises:
        RuntimeError: If the track has no points or any point has no timestamp.
    """
    points = track.points
    if not points:
        raise RuntimeError("Track must contain at least one point to build an activity profile.")
    if any(p.timestamp is None for p in points):
        raise RuntimeError("Every track point needs a timestamp to build an activity profile.")

    start_time = points[0].timestamp
    assert start_time is not None

    def elapsed(p: TrackPoint) -> float:
        assert p.timestamp is not None and start_time is not None
        return (p.timestamp - start_time).total_seconds()

    def sample(p: TrackPoint, speed: float, is_stop: bool = False) -> ProfileSample:
        # 0.0 means "no elevation data" (gpx_reader's default for a point
        # missing <ele>), the same convention fit_writer follows.
        elevation = p.elevation if p.elevation != 0.0 else None
        return ProfileSample(
            distance_from_start=p.distance_from_start,
            elapsed_seconds=elapsed(p),
            speed_mps=speed,
            elevation=elevation,
            lat=p.lat,
            lon=p.lon,
            is_stop=is_stop,
        )

    samples = [sample(points[0], 0.0)]
    bucket_distance = track.total_distance / max(max_samples, 1)
    bucket_start = 0

    def flush(end: int) -> None:
        """Close the bucket from points[bucket_start] to points[end] as one sample."""
        first, last = points[bucket_start], points[end]
        seconds = elapsed(last) - elapsed(first)
        meters = last.distance_from_start - first.distance_from_start
        samples.append(sample(last, meters / seconds if seconds > 0 else 0.0))

    for index in range(1, len(points)):
        prev, curr = points[index - 1], points[index]
        if curr.distance_from_start == prev.distance_from_start and elapsed(curr) > elapsed(prev):
            if bucket_start < index - 1:
                flush(index - 1)
            samples.append(sample(prev, 0.0, is_stop=True))
            samples.append(sample(curr, 0.0, is_stop=True))
            bucket_start = index
            continue
        if curr.distance_from_start - points[bucket_start].distance_from_start >= bucket_distance > 0:
            flush(index)
            bucket_start = index

    if bucket_start < len(points) - 1:
        flush(len(points) - 1)

    first_moving = next((s for s in samples[1:] if not s.is_stop), None)
    if first_moving is not None:
        samples[0].speed_mps = first_moving.speed_mps

    return samples
