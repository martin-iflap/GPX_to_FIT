"""Build a Garmin FIT activity file from a fully-paced Track."""

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


def _timer_event(event_type: EventType, timestamp: datetime) -> EventMessage:
    """A timer START or STOP_ALL event at `timestamp`."""
    event = EventMessage()
    event.event = Event.TIMER
    event.event_type = event_type
    event.timestamp = _fit_timestamp(timestamp)
    return event


def write_fit(track: Track) -> bytes:
    """Write a .fit file from a fully-paced track.

    Builds a FileIdMessage and SportMessage, one RecordMessage per track
    point (position/elevation/distance/timestamp/speed), and a LapMessage,
    SessionMessage, and ActivityMessage summarizing the whole activity.
    Records are wrapped in timer events the way a real device writes them:
    START before the first, STOP_ALL after the last, and a STOP_ALL/START
    pause around every stop (a zero-distance leg that still takes time).
    Without timer events, Strava derives moving time from speed alone and
    drops every slow stretch (e.g. a steep climb on a long hike) as
    "resting"; with them, it uses the recorded timer time, which here is
    elapsed time minus stops. Average speed is likewise over timer time.
    Every point must already have a timestamp — this is the last step in the
    pipeline, run after pacing.combine has stamped them all. Per-point speed
    is derived here from consecutive distance/timestamp deltas (not carried
    over from pacing) so that FIT readers which expect an explicit speed
    field, rather than deriving it themselves, can still show pace.

    Args:
        track: A track whose points all have a timestamp, in route order.
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

    sport = Sport.RUNNING
    if track.sport is not None and track.sport.value == "hiking":
        sport = Sport.WALKING

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
    file_id.time_created = _fit_timestamp(start_time)
    if track.device:
        file_id.product_name = track.device
    builder.add(file_id)

    sport_message = SportMessage()
    sport_message.sport = sport
    builder.add(sport_message)

    total_elapsed_seconds = (end_time - start_time).total_seconds()

    builder.add(_timer_event(EventType.START, start_time))

    point_speeds: list[float] = [0.0] * len(track.points)
    for index, (prev, curr) in enumerate(zip(track.points, track.points[1:]), start=1):
        if prev.timestamp is None or curr.timestamp is None:
            raise RuntimeError("Missing timestamp for fit file creation.")
        delta_distance = curr.distance_from_start - prev.distance_from_start
        delta_seconds = (curr.timestamp - prev.timestamp).total_seconds()
        point_speeds[index] = delta_distance / delta_seconds if delta_seconds > 0 else 0.0
    if len(point_speeds) > 1:
        point_speeds[0] = point_speeds[1]

    paused_seconds = 0.0
    previous_point: TrackPoint | None = None
    for point, speed in zip(track.points, point_speeds):
        if point.timestamp is None:
            raise RuntimeError("Missing timestamp for fit file creation.")
        if (
            previous_point is not None
            and previous_point.timestamp is not None
            and point.distance_from_start == previous_point.distance_from_start
            and point.timestamp > previous_point.timestamp
        ):
            # Time passing with no distance covered can only be a stop
            # (pacing gives an ordinary duplicated point zero time), so it's
            # written as a timer pause rather than left as zero-speed time.
            builder.add(_timer_event(EventType.STOP_ALL, previous_point.timestamp))
            builder.add(_timer_event(EventType.START, point.timestamp))
            paused_seconds += (point.timestamp - previous_point.timestamp).total_seconds()
        previous_point = point

        record = RecordMessage()
        record.position_lat = point.lat
        record.position_long = point.lon
        if point.elevation is not None and point.elevation != 0.0:
            # 0.0 means "no elevation data" (gpx_reader's default for a point
            # missing <ele>), not literal sea level, so it's left unset here.
            record.altitude = point.elevation
        record.distance = point.distance_from_start
        record.speed = speed
        record.enhanced_speed = speed
        record.timestamp = _fit_timestamp(point.timestamp)
        builder.add(record)

    builder.add(_timer_event(EventType.STOP_ALL, end_time))
    total_timer_seconds = total_elapsed_seconds - paused_seconds

    lap = LapMessage()
    session = SessionMessage()
    avg_speed = track.total_distance / total_timer_seconds if total_timer_seconds > 0 else 0.0
    max_speed = max(point_speeds, default=0.0)
    # assign the same values to both lap and session messages
    for message in (lap, session):
        message.start_time = _fit_timestamp(start_time)
        message.timestamp = _fit_timestamp(end_time)
        message.total_elapsed_time = total_elapsed_seconds
        message.total_timer_time = total_timer_seconds
        message.total_distance = track.total_distance
        message.total_ascent = round(track.total_elevation_gain)
        message.sport = sport
        message.avg_speed = avg_speed
        message.enhanced_avg_speed = avg_speed
        message.max_speed = max_speed
        message.enhanced_max_speed = max_speed

    builder.add(lap)
    builder.add(session)

    activity = ActivityMessage()
    activity.timestamp = _fit_timestamp(end_time)
    activity.total_timer_time = total_timer_seconds
    activity.num_sessions = 1
    activity.type = Activity.MANUAL
    builder.add(activity)

    return builder.build_bytes()
