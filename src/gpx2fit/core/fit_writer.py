"""Build a Garmin FIT activity file from a fully-paced Track."""

from datetime import datetime, timezone

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.sport_message import SportMessage
from fit_tool.profile.profile_type import Activity, FileType, Manufacturer, Sport

from gpx2fit.core.models import Track


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


def write_fit(track: Track) -> bytes:
    """Write a .fit file from a fully-paced track.

    Builds a FileIdMessage and SportMessage, one RecordMessage per track
    point (position/elevation/distance/timestamp), and a LapMessage,
    SessionMessage, and ActivityMessage summarizing the whole activity.
    Every point must already have a timestamp — this is the last step in the
    pipeline, run after pacing.combine has stamped them all.

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
    file_id.manufacturer = Manufacturer.DEVELOPMENT.value
    file_id.product = 0
    file_id.time_created = _fit_timestamp(start_time)
    if track.device:
        file_id.product_name = track.device
    builder.add(file_id)

    sport_message = SportMessage()
    sport_message.sport = sport
    builder.add(sport_message)

    total_elapsed_seconds = (end_time - start_time).total_seconds()

    for point in track.points:
        record = RecordMessage()
        record.position_lat = point.lat
        record.position_long = point.lon
        if point.elevation is not None and point.elevation != 0.0:
            # 0.0 means "no elevation data" (gpx_reader's default for a point
            # missing <ele>), not literal sea level, so it's left unset here.
            record.altitude = point.elevation
        record.distance = point.distance_from_start
        if point.timestamp is None:
            raise RuntimeError("Missing timestamp for fit file creation.")
        record.timestamp = _fit_timestamp(point.timestamp)
        builder.add(record)

    lap = LapMessage()
    session = SessionMessage()
    # assign the same values to both lap and session messages
    for message in (lap, session):
        message.start_time = _fit_timestamp(start_time)
        message.timestamp = _fit_timestamp(end_time)
        message.total_elapsed_time = total_elapsed_seconds
        message.total_timer_time = total_elapsed_seconds
        message.total_distance = track.total_distance
        message.total_ascent = round(track.total_elevation_gain)
        message.sport = sport

    builder.add(lap)
    builder.add(session)

    activity = ActivityMessage()
    activity.timestamp = _fit_timestamp(end_time)
    activity.total_timer_time = total_elapsed_seconds
    activity.num_sessions = 1
    activity.type = Activity.MANUAL
    builder.add(activity)

    return builder.build_bytes()
