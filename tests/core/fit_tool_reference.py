"""The FIT encoding write_fit used before core got its own encoder, kept as a test oracle.

This is fit_writer._encode_fit_tool as it was when fit_tool still built the
file in the browser, frozen here verbatim. Strava has accepted this layout, so
the handwritten encoder (core/fit_encoder.py) must produce exactly the same
bytes for the same content. Don't "improve" it: a change here moves the
target the encoder is held to. A deliberate change of layout means changing
this oracle and the encoder together, and re-checking a Strava upload.
"""

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.sport_message import SportMessage
from fit_tool.profile.profile_type import Activity, Event, EventType, FileType, Sport

from gpx2fit.core.fit_writer import _FitContent


def _timer_event(event_type: EventType, timestamp_ms: int) -> EventMessage:
    """A timer START or STOP_ALL event at `timestamp_ms` (Unix epoch milliseconds)."""
    event = EventMessage()
    event.event = Event.TIMER
    event.event_type = event_type
    event.timestamp = timestamp_ms
    return event


def encode(content: _FitContent) -> bytes:
    """Encode the decided content with fit_tool's FitFileBuilder."""
    builder = FitFileBuilder(auto_define=True, min_string_size=50)

    file_id = FileIdMessage()
    file_id.type = FileType.ACTIVITY
    file_id.manufacturer = content.manufacturer
    file_id.product = content.product
    if content.serial_number is not None:
        file_id.serial_number = content.serial_number
    file_id.time_created = content.first_ms
    if content.product_name:
        file_id.product_name = content.product_name
    builder.add(file_id)

    sport_message = SportMessage()
    sport_message.sport = Sport(content.sport)
    builder.add(sport_message)

    for index, span in enumerate(content.spans):
        if index == 0:
            builder.add(_timer_event(EventType.START, span[0].timestamp_ms))
        else:
            builder.add(_timer_event(EventType.STOP_ALL, content.spans[index - 1][-1].timestamp_ms))
            builder.add(_timer_event(EventType.START, span[0].timestamp_ms))

        for sample in span:
            record = RecordMessage()
            record.position_lat = sample.lat
            record.position_long = sample.lon
            if content.has_elevation:
                record.altitude = sample.elevation
            record.distance = sample.distance
            record.speed = sample.speed
            record.enhanced_speed = sample.speed
            record.timestamp = sample.timestamp_ms
            builder.add(record)

    builder.add(_timer_event(EventType.STOP_ALL, content.last_ms))

    lap = LapMessage()
    session = SessionMessage()
    # assign the same values to both lap and session messages
    for message in (lap, session):
        message.start_time = content.first_ms
        message.timestamp = content.last_ms
        message.total_elapsed_time = content.total_elapsed_seconds
        message.total_timer_time = content.total_timer_seconds
        message.total_distance = content.total_distance
        message.total_ascent = content.total_ascent
        message.sport = Sport(content.sport)
        message.avg_speed = content.avg_speed
        message.enhanced_avg_speed = content.avg_speed
        message.max_speed = content.max_speed
        message.enhanced_max_speed = content.max_speed

    builder.add(lap)
    builder.add(session)

    activity = ActivityMessage()
    activity.timestamp = content.last_ms
    activity.total_timer_time = content.total_timer_seconds
    activity.num_sessions = 1
    activity.type = Activity.MANUAL
    builder.add(activity)

    return builder.build_bytes()
