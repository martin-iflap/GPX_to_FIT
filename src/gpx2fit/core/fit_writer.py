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
    # fit-tool expects "unix epoch milliseconds" for date_time fields.
    # It applies the FIT epoch conversion internally during encoding.
    if value.tzinfo is None:
        utc_value = value.replace(tzinfo=timezone.utc)
    else:
        utc_value = value.astimezone(timezone.utc)
    return round(utc_value.timestamp() * 1000)


def _to_semicircles(degrees: float) -> int:
    return round(degrees * ((1 << 31) / 180.0))


def write_fit(track: Track) -> bytes:
    """Write a fit file from provided track data."""
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
            record.altitude = point.elevation
        record.distance = point.distance_from_start
        if point.timestamp is None:
            raise RuntimeError("Missing timestamp for fit file creation.")
        record.timestamp = _fit_timestamp(point.timestamp)
        builder.add(record)

    lap = LapMessage()
    lap.start_time = _fit_timestamp(start_time)
    lap.timestamp = _fit_timestamp(end_time)
    lap.total_elapsed_time = total_elapsed_seconds
    lap.total_timer_time = total_elapsed_seconds
    lap.total_distance = track.total_distance
    lap.total_ascent = round(track.total_elevation_gain) # todo: do something about the duplication later!!
    lap.sport = sport
    builder.add(lap)

    session = SessionMessage()
    session.start_time = _fit_timestamp(start_time)
    session.timestamp = _fit_timestamp(end_time)
    session.total_elapsed_time = total_elapsed_seconds
    session.total_timer_time = total_elapsed_seconds
    session.total_distance = track.total_distance
    session.total_ascent = round(track.total_elevation_gain)
    session.sport = sport
    builder.add(session)

    activity = ActivityMessage()
    activity.timestamp = _fit_timestamp(end_time)
    activity.total_timer_time = total_elapsed_seconds
    activity.num_sessions = 1
    activity.type = Activity.MANUAL
    builder.add(activity)

    return builder.build_bytes()
