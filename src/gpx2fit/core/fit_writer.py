"""Build a Garmin FIT activity file from a fully-paced Track."""

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from gpx2fit.core.fit_encoder import (
    ACTIVITY,
    ACTIVITY_TYPE_MANUAL,
    EVENT,
    EVENT_TIMER,
    EVENT_TYPE_START,
    EVENT_TYPE_STOP_ALL,
    FILE_ID,
    FILE_TYPE_ACTIVITY,
    LAP,
    MANUFACTURER_DEVELOPMENT,
    MAX_STRING_BYTES,
    RECORD,
    SESSION,
    SPORT,
    SPORT_RUNNING,
    SPORT_WALKING,
    FitEncoder,
)
from gpx2fit.core.models import SportType, Track, TrackPoint
from gpx2fit.core.pacing.gradient import elevation_gain


# Seconds between two records while moving, per sport. FIT timestamps are
# whole seconds, so these are whole numbers too. Each moving span still gets a
# record at its exact start and end, whether they fall on the interval.
# Runs get shorter intervals for more accurate corners.
RECORD_INTERVAL_S = {
    SportType.RUNNING: 2,
    SportType.HIKING: 3,
}

# The FIT profile's sport for each of ours.
FIT_SPORT = {
    SportType.RUNNING: SPORT_RUNNING,
    SportType.HIKING: SPORT_WALKING,
}

# What a track with no sport is written as.
_DEFAULT_SPORT = SportType.RUNNING


def _fit_timestamp(value: datetime) -> int:
    """Convert a datetime to Unix epoch milliseconds, the unit fit_encoder takes timestamps in.

    Args:
        value: A naive or timezone-aware datetime. A naive value is treated
            as already being in UTC rather than the local timezone.
    Returns:
        Milliseconds since the Unix epoch (1970-01-01T00:00:00 UTC), rounded
        to the nearest millisecond.
    """
    if value.tzinfo is None:
        utc_value = value.replace(tzinfo=timezone.utc)
    else:
        utc_value = value.astimezone(timezone.utc)
    return round(utc_value.timestamp() * 1000)


def _nearest_second(epoch_seconds: float) -> int:
    """Round half up to a whole epoch second (round() would round half to even)."""
    return math.floor(epoch_seconds + 0.5)


@dataclass(frozen=True)
class _Sample:
    """One record's worth of the paced track."""
    timestamp_ms: int  # Unix epoch milliseconds, as _fit_timestamp gives
    lat: float
    lon: float
    elevation: float
    distance: float
    speed: float


def _leg_speeds(distances: list[float], times: list[float]) -> list[float]:
    """Each leg's paced speed. A zero-duration leg borrows its nearest timed neighbor's, preferring the one before."""
    speeds: list[float | None] = [
        (d1 - d0) / (t1 - t0) if t1 > t0 else None
        for d0, d1, t0, t1 in zip(distances, distances[1:], times, times[1:])
    ]
    known = next((speed for speed in speeds if speed is not None), 0.0)
    filled = []
    for speed in speeds:
        if speed is not None:
            known = speed
        filled.append(known)
    return filled


def _resample(points: list[TrackPoint], interval_s: int) -> list[list[_Sample]]:
    """Resample a paced track onto a grid of whole epoch seconds, one list per moving span.

    FIT timestamps are whole seconds, so a record per track point would give a
    dense GPX several records on one second (a distance jump in no time) and a
    sparse one records 30+ s apart. Instead, a record is written every
    `interval_s` seconds, interpolated in time inside the leg it falls in, with
    that leg's speed (the leg ending on the tick where one ends there; the
    first tick takes the first leg).

    A stop (a zero-distance leg that takes time) splits the track into spans,
    and no record is written while stopped. Each span's first and last ticks
    are its first and last points' times rounded to the nearest second, and
    times between those and the exact ones are clamped to the end points. The
    grid starts again at each span's first tick, and the last tick is always
    written, even when it's closer than `interval_s` to the one before. A
    stop that rounds to no time at all isn't a pause.

    Args:
        points: At least one point, every one with a timestamp, in route order
            with non-decreasing times.
        interval_s: Whole seconds between two records inside a span.
    Returns:
        The spans in order, each a non-empty list of samples at most
        `interval_s` apart. A span's last second is always before the next
        span's first.
    Raises:
        ValueError: If interval_s isn't a positive whole number.
    """
    if not isinstance(interval_s, int) or interval_s < 1:
        raise ValueError(f"interval_s must be a positive whole number of seconds, got {interval_s!r}.")


    times = [_fit_timestamp(p.timestamp) / 1000 for p in points if p.timestamp is not None]
    distances = [p.distance_from_start for p in points]
    speeds = _leg_speeds(distances, times)

    span_starts = [0] + [
        index for index in range(1, len(points))
        if distances[index] == distances[index - 1]
        and _nearest_second(times[index]) > _nearest_second(times[index - 1])
    ]
    span_ends = [start - 1 for start in span_starts[1:]] + [len(points) - 1]

    spans = []
    for lo, hi in zip(span_starts, span_ends):
        samples = []
        leg = lo  # the leg [leg, leg + 1] the current tick falls in
        first_second, last_second = _nearest_second(times[lo]), _nearest_second(times[hi])
        ticks = list(range(first_second, last_second + 1, interval_s))
        if ticks[-1] != last_second:
            ticks.append(last_second)
        for second in ticks:
            t = min(max(float(second), times[lo]), times[hi])
            # Move on past legs that end before this tick, and past zero-duration ones.
            while leg < hi - 1 and (times[leg + 1] < t or times[leg + 1] <= times[leg]):
                leg += 1
            if hi == lo:
                a = b = points[lo]
                fraction, speed = 0.0, 0.0
            else:
                a, b = points[leg], points[leg + 1]
                duration = times[leg + 1] - times[leg]
                fraction = (t - times[leg]) / duration if duration > 0 else 1.0
                speed = speeds[leg]
            samples.append(_Sample(
                timestamp_ms=second * 1000,
                lat=a.lat + fraction * (b.lat - a.lat),
                lon=a.lon + fraction * (b.lon - a.lon),
                elevation=a.elevation + fraction * (b.elevation - a.elevation),
                distance=a.distance_from_start + fraction * (b.distance_from_start - a.distance_from_start),
                speed=speed,
            ))
        spans.append(samples)
    return spans


def _per_point(points: list[TrackPoint]) -> list[list[_Sample]]:
    """One sample per track point at its own time, split into moving spans at stops.

    Each sample takes the speed of the leg ending at it (the first point, the
    first leg's). A stop is a zero-distance leg that takes any time at all.

    Args:
        points: At least one point, every one with a timestamp, in route order.
    Returns:
        The spans in order, each a non-empty list of samples.
    """
    times_ms = [_fit_timestamp(p.timestamp) for p in points if p.timestamp is not None]
    distances = [p.distance_from_start for p in points]
    leg_speeds = _leg_speeds(distances, [t / 1000 for t in times_ms])
    point_speeds = leg_speeds[:1] + leg_speeds if leg_speeds else [0.0]

    spans: list[list[_Sample]] = [[]]
    for index, (point, speed) in enumerate(zip(points, point_speeds)):
        if index > 0 and distances[index] == distances[index - 1] and times_ms[index] > times_ms[index - 1]:
            spans.append([])
        spans[-1].append(_Sample(
            timestamp_ms=times_ms[index],
            lat=point.lat,
            lon=point.lon,
            elevation=point.elevation,
            distance=point.distance_from_start,
            speed=speed,
        ))
    return spans


def write_fit(track: Track, *, resample: bool = True, record_interval_s: int | None = None) -> bytes:
    """Write a .fit file from a fully-paced track.

    Builds a FileIdMessage and SportMessage, one RecordMessage per
    `record_interval_s` seconds of moving time (position/elevation/distance/
    timestamp/speed, interpolated along the track — see _resample), and a
    LapMessage, SessionMessage, and
    ActivityMessage summarizing the whole activity.
    Records are wrapped in timer events the way a real device writes them:
    START before the first, STOP_ALL after the last, and a STOP_ALL/START
    pause around every stop (a zero-distance leg that still takes time), with
    no records in between. Without timer events, Strava derives moving time
    from speed alone and drops every slow stretch (e.g. a steep climb on a
    long hike) as "resting"; with them, it uses the recorded timer time, which
    here is elapsed time minus stops. Average speed is likewise over timer
    time. Every time in the file is on the same whole-second grid, so the
    records, timer events and summaries agree.
    The bytes are packed by fit_encoder, which writes exactly what fit_tool
    used to (tests/core/fit_tool_reference.py) at a fraction of the cost.
    Every point must already have a timestamp — this is the last step in the
    pipeline, run after pacing.combine has stamped them all. Per-record speed
    is the paced speed of the leg the record falls in, so that FIT readers
    which expect an explicit speed field, rather than deriving it themselves,
    can still show pace. Altitude is left out when the track has no elevation
    (Track.has_elevation), and total ascent is counted with hysteresis
    (pacing.gradient.elevation_gain).

    Args:
        track: A track whose points all have a timestamp, in route order.
        resample: False writes one record per track point at its own time
            instead of the whole-second grid, the way a watches "smart
            recording" does. The app always resamples. The tuning harness's
            tests write their synthetic recordings this way: they read them
            back with fit_tool, which decodes slowly, and 1 Hz would mean
            hours of records each.
        record_interval_s: Whole seconds between records when resampling.
            None (what the app passes) takes RECORD_INTERVAL_S for the
            track's sport, a run's when it has none. Ignored when resample
            is False.
    Returns:
        The complete .fit file contents as bytes.
    Raises:
        RuntimeError: If the track has no points, if the first or last point
            has no timestamp, if the last timestamp isn't strictly later
            than the first, or if any point in between is missing a
            timestamp.
    """
    return _encode(_build_content(track, resample=resample, record_interval_s=record_interval_s))


@dataclass(frozen=True)
class _FitContent:
    """Everything that goes in the file, decided; encoding it is all that's left.

    Enum-valued fields hold the FIT profile's integer values. Times are Unix
    epoch milliseconds, durations seconds, distances metres, speeds m/s.
    """
    sport: int
    manufacturer: int
    product: int
    serial_number: int | None
    product_name: str | None
    has_elevation: bool
    spans: list[list[_Sample]]
    first_ms: int
    last_ms: int
    total_elapsed_seconds: float
    total_timer_seconds: float
    total_distance: float
    total_ascent: int
    avg_speed: float
    max_speed: float


def _build_content(track: Track, *, resample: bool, record_interval_s: int | None = None) -> _FitContent:
    """Validate the track and decide every value write_fit puts in the file (see write_fit)."""
    if not track.points:
        raise RuntimeError("Track must contain at least one point for fit file creation.")

    start_time = track.start_time
    if start_time is None:
        raise RuntimeError("Missing start time for fit file creation.")

    end_time = track.points[-1].timestamp
    if end_time is None:
        raise RuntimeError("Missing final timestamp for fit file creation.")
    if end_time <= start_time:
        raise RuntimeError("Final timestamp must be later than start timestamp for fit file creation.")
    if any(point.timestamp is None for point in track.points):
        raise RuntimeError("Missing timestamp for fit file creation.")

    sport = track.sport or _DEFAULT_SPORT
    if record_interval_s is None:
        record_interval_s = RECORD_INTERVAL_S[sport]
    spans = _resample(track.points, record_interval_s) if resample else _per_point(track.points)
    first_ms = spans[0][0].timestamp_ms
    last_ms = spans[-1][-1].timestamp_ms
    paused_ms = sum(
        span[0].timestamp_ms - previous[-1].timestamp_ms for previous, span in zip(spans, spans[1:])
    )
    total_elapsed_seconds = (last_ms - first_ms) / 1000
    total_timer_seconds = total_elapsed_seconds - paused_ms / 1000

    # Strava names the device only from manufacturer + product, looked up in
    # its own table. Without a chosen device, "development" makes no claim
    # to be any real hardware.
    if track.device_manufacturer is None:
        manufacturer, product = MANUFACTURER_DEVELOPMENT, 0
    else:
        manufacturer, product = track.device_manufacturer, track.device_product or 0

    return _FitContent(
        sport=FIT_SPORT[sport],
        manufacturer=manufacturer,
        product=product,
        serial_number=track.device_serial,
        product_name=_fit_string(track.device) or None,
        # Without elevation every point holds a 0.0 placeholder, not sea level.
        has_elevation=track.has_elevation,
        spans=spans,
        first_ms=first_ms,
        last_ms=last_ms,
        total_elapsed_seconds=total_elapsed_seconds,
        total_timer_seconds=total_timer_seconds,
        total_distance=track.total_distance,
        total_ascent=round(elevation_gain(track)),
        avg_speed=track.total_distance / total_timer_seconds if total_timer_seconds > 0 else 0.0,
        max_speed=max((sample.speed for span in spans for sample in span), default=0.0),
    )


def _fit_string(value: str | None) -> str | None:
    """`value` cut, between characters, to what a FIT string field holds.

    The device name can be a GPX's creator attribute, which has no length
    limit, and the name is cosmetic (Strava ignores it), so it's cut rather
    than refused.
    """
    if value is None:
        return None
    return value.encode("utf-8")[:MAX_STRING_BYTES].decode("utf-8", errors="ignore")


def _encode(content: _FitContent) -> bytes:
    """Encode the decided content as a FIT file (see fit_encoder for the byte layout)."""
    encoder = FitEncoder(min_string_size=50)

    encoder.add(FILE_ID, {
        "type": FILE_TYPE_ACTIVITY,
        "manufacturer": content.manufacturer,
        "product": content.product,
        "serial_number": content.serial_number,
        "time_created": content.first_ms,
        "product_name": content.product_name,
    })
    encoder.add(SPORT, {"sport": content.sport})

    record_fields = (
        ("timestamp", "position_lat", "position_long", "altitude", "distance", "speed", "enhanced_speed")
        if content.has_elevation else
        ("timestamp", "position_lat", "position_long", "distance", "speed", "enhanced_speed")
    )
    for index, span in enumerate(content.spans):
        if index > 0:
            encoder.add(EVENT, _timer_event(EVENT_TYPE_STOP_ALL, content.spans[index - 1][-1].timestamp_ms))
        encoder.add(EVENT, _timer_event(EVENT_TYPE_START, span[0].timestamp_ms))
        if content.has_elevation:
            rows = [(s.timestamp_ms, s.lat, s.lon, s.elevation, s.distance, s.speed, s.speed) for s in span]
        else:
            rows = [(s.timestamp_ms, s.lat, s.lon, s.distance, s.speed, s.speed) for s in span]
        encoder.add_many(RECORD, record_fields, rows)
    encoder.add(EVENT, _timer_event(EVENT_TYPE_STOP_ALL, content.last_ms))

    # the lap and the session carry the same values
    summary = {
        "start_time": content.first_ms,
        "timestamp": content.last_ms,
        "total_elapsed_time": content.total_elapsed_seconds,
        "total_timer_time": content.total_timer_seconds,
        "total_distance": content.total_distance,
        "total_ascent": content.total_ascent,
        "sport": content.sport,
        "avg_speed": content.avg_speed,
        "enhanced_avg_speed": content.avg_speed,
        "max_speed": content.max_speed,
        "enhanced_max_speed": content.max_speed,
    }
    encoder.add(LAP, summary)
    encoder.add(SESSION, summary)

    encoder.add(ACTIVITY, {
        "timestamp": content.last_ms,
        "total_timer_time": content.total_timer_seconds,
        "num_sessions": 1,
        "type": ACTIVITY_TYPE_MANUAL,
    })
    return encoder.to_bytes()


def _timer_event(event_type: int, timestamp_ms: int) -> dict[str, int]:
    """A timer START or STOP_ALL event at `timestamp_ms` (Unix epoch milliseconds)."""
    return {"event": EVENT_TIMER, "event_type": event_type, "timestamp": timestamp_ms}
