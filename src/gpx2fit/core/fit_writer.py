"""Build a Garmin FIT activity file from a fully-paced Track."""

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.sport_message import SportMessage
from fit_tool.profile.profile_type import Activity, Event, EventType, FileType, Manufacturer, Sport

from gpx2fit.core.models import Track, TrackPoint
from gpx2fit.core.pacing.gradient import elevation_gain


def _fit_timestamp(value: datetime) -> int:
    """Convert a datetime to a FIT timestamp (Unix epoch milliseconds), which fit-tool expects.

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


def _resample(points: list[TrackPoint]) -> list[list[_Sample]]:
    """Resample a paced track onto a 1 Hz grid of whole epoch seconds, one list per moving span.

    FIT timestamps are whole seconds, so a record per track point would give a
    dense GPX several records on one second (a distance jump in no time) and a
    sparse one records 30+ s apart. Instead, every whole second gets a record,
    interpolated in time inside the leg it falls in, with that leg's speed (the
    leg ending on the tick where one ends there; the first tick takes the
    first leg).

    A stop (a zero-distance leg that takes time) splits the track into spans,
    and no record is written while stopped. Each span's first and last ticks
    are its first and last points' times rounded to the nearest second, and
    times between those and the exact ones are clamped to the end points. A
    stop that rounds to no time at all isn't a pause.

    Args:
        points: At least one point, every one with a timestamp, in route order
            with non-decreasing times.
    Returns:
        The spans in order, each a non-empty list of samples one second apart.
        A span's last second is always before the next span's first.
    """
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
        for second in range(_nearest_second(times[lo]), _nearest_second(times[hi]) + 1):
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


def _timer_event(event_type: EventType, timestamp_ms: int) -> EventMessage:
    """A timer START or STOP_ALL event at `timestamp_ms` (Unix epoch milliseconds)."""
    event = EventMessage()
    event.event = Event.TIMER
    event.event_type = event_type
    event.timestamp = timestamp_ms
    return event


def write_fit(track: Track, *, resample: bool = True) -> bytes:
    """Write a .fit file from a fully-paced track.

    Builds a FileIdMessage and SportMessage, one RecordMessage per second of
    moving time (position/elevation/distance/timestamp/speed, interpolated
    along the track — see _resample), and a LapMessage, SessionMessage, and
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
    1 Hz costs one record per second of activity, and fit_tool builds each
    record slowly (~0.3 ms in CPython, more in Pyodide): about 6 s for a 6 h
    hike in CPython, against ~1.3 s for one record per point. If that's too
    slow in the browser, a coarser grid is the first thing to try.
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
            instead of the 1 Hz grid, the way a watches "smart recording" does.
            The app always resamples. The tuning harness's tests write their
            synthetic recordings this way, since 1 Hz costs a record per second
            of activity and fit_tool builds each one slowly.
    Returns:
        The complete .fit file contents as bytes.
    Raises:
        RuntimeError: If the track has no points, if the first or last point
            has no timestamp, if the last timestamp isn't strictly later
            than the first, or if any point in between is missing a
            timestamp.
    """
    builder = FitFileBuilder(auto_define=True, min_string_size=50)

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

    sport = Sport.RUNNING
    if track.sport is not None and track.sport.value == "hiking":
        sport = Sport.WALKING

    spans = _resample(track.points) if resample else _per_point(track.points)
    first_ms = spans[0][0].timestamp_ms
    last_ms = spans[-1][-1].timestamp_ms

    file_id = FileIdMessage()
    file_id.type = FileType.ACTIVITY
    # Strava names the device only from manufacturer + product, looked up in
    # its own table. Without a chosen device, "development" makes no claim
    # to be any real hardware.
    if track.device_manufacturer is None:
        file_id.manufacturer = Manufacturer.DEVELOPMENT.value
        file_id.product = 0
    else:
        file_id.manufacturer = track.device_manufacturer
        file_id.product = track.device_product or 0
    if track.device_serial is not None:
        file_id.serial_number = track.device_serial
    file_id.time_created = first_ms
    if track.device:
        file_id.product_name = track.device
    builder.add(file_id)

    sport_message = SportMessage()
    sport_message.sport = sport
    builder.add(sport_message)

    paused_ms = 0
    for index, span in enumerate(spans):
        if index == 0:
            builder.add(_timer_event(EventType.START, span[0].timestamp_ms))
        else:
            previous_last = spans[index - 1][-1].timestamp_ms
            builder.add(_timer_event(EventType.STOP_ALL, previous_last))
            builder.add(_timer_event(EventType.START, span[0].timestamp_ms))
            paused_ms += span[0].timestamp_ms - previous_last

        for sample in span:
            record = RecordMessage()
            record.position_lat = sample.lat
            record.position_long = sample.lon
            if track.has_elevation:
                # Without elevation every point holds a 0.0 placeholder, not sea level.
                record.altitude = sample.elevation
            record.distance = sample.distance
            record.speed = sample.speed
            record.enhanced_speed = sample.speed
            record.timestamp = sample.timestamp_ms
            builder.add(record)

    builder.add(_timer_event(EventType.STOP_ALL, last_ms))
    total_elapsed_seconds = (last_ms - first_ms) / 1000
    total_timer_seconds = total_elapsed_seconds - paused_ms / 1000

    lap = LapMessage()
    session = SessionMessage()
    avg_speed = track.total_distance / total_timer_seconds if total_timer_seconds > 0 else 0.0
    max_speed = max((sample.speed for span in spans for sample in span), default=0.0)
    ascent = elevation_gain(track)
    # assign the same values to both lap and session messages
    for message in (lap, session):
        message.start_time = first_ms
        message.timestamp = last_ms
        message.total_elapsed_time = total_elapsed_seconds
        message.total_timer_time = total_timer_seconds
        message.total_distance = track.total_distance
        message.total_ascent = round(ascent)
        message.sport = sport
        message.avg_speed = avg_speed
        message.enhanced_avg_speed = avg_speed
        message.max_speed = max_speed
        message.enhanced_max_speed = max_speed

    builder.add(lap)
    builder.add(session)

    activity = ActivityMessage()
    activity.timestamp = last_ms
    activity.total_timer_time = total_timer_seconds
    activity.num_sessions = 1
    activity.type = Activity.MANUAL
    builder.add(activity)

    return builder.build_bytes()
