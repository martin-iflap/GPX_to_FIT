"""The handwritten FIT encoder against fit_tool, message by message.

Each test encodes one message with both and demands identical bytes: the
definition, the data and the surrounding file. fit_tool is the oracle because
its layout is the one Strava has already accepted (see fit_tool_reference.py).
"""

from typing import cast

import pytest
from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.sport_message import SportMessage
from fit_tool.profile.profile_type import Activity, Event, EventType, FileType, Manufacturer, Sport

from gpx2fit.core import fit_encoder
from gpx2fit.core.fit_encoder import ACTIVITY, EVENT, FILE_ID, LAP, RECORD, SESSION, SPORT, FitEncoder

_T = 1704096000000  # 2024-01-01T08:00:00Z in Unix epoch ms


def _fit_tool_bytes(*messages) -> bytes:
    builder = FitFileBuilder(auto_define=True, min_string_size=50)
    for message in messages:
        builder.add(message)
    return builder.build_bytes()


def _fit_tool_message(cls, values: dict):
    message = cls()
    for name, value in values.items():
        if value is not None:
            setattr(message, name, value)
    return message


def _ours(spec, values: dict) -> bytes:
    encoder = FitEncoder()
    encoder.add(spec, values)
    return encoder.to_bytes()


def _assert_same(ours: bytes, theirs: bytes) -> None:
    """Byte equality, failing with the first differing offset rather than two hex walls."""
    if ours != theirs:
        first = next((i for i, (a, b) in enumerate(zip(ours, theirs)) if a != b), min(len(ours), len(theirs)))
        pytest.fail(
            f"bytes differ from offset {first} (ours {len(ours)} B, fit_tool {len(theirs)} B):\n"
            f"  ours     {ours[max(0, first - 8):first + 16].hex(' ')}\n"
            f"  fit_tool {theirs[max(0, first - 8):first + 16].hex(' ')}"
        )


class TestEnumValuesMatchTheProfile:
    def test_enum_constants(self):
        assert fit_encoder.FILE_TYPE_ACTIVITY == FileType.ACTIVITY.value
        assert fit_encoder.MANUFACTURER_DEVELOPMENT == Manufacturer.DEVELOPMENT.value
        assert fit_encoder.SPORT_RUNNING == Sport.RUNNING.value
        assert fit_encoder.SPORT_WALKING == Sport.WALKING.value
        assert fit_encoder.EVENT_TIMER == Event.TIMER.value
        assert fit_encoder.EVENT_TYPE_START == EventType.START.value
        assert fit_encoder.EVENT_TYPE_STOP_ALL == EventType.STOP_ALL.value
        assert fit_encoder.ACTIVITY_TYPE_MANUAL == Activity.MANUAL.value


class TestFieldsMatchFitToolsProfile:
    @pytest.mark.parametrize("spec, cls", [
        (FILE_ID, FileIdMessage), (SPORT, SportMessage), (EVENT, EventMessage), (RECORD, RecordMessage),
        (LAP, LapMessage), (SESSION, SessionMessage), (ACTIVITY, ActivityMessage),
    ])
    def test_ids_types_scales_and_declaration_order(self, spec, cls):
        assert spec.global_id == cls.ID
        theirs = {field.name: field for field in cls().fields}
        order = [field.name for field in cls().fields]
        for field in spec.fields:
            reference = theirs[field.name]
            assert field.field_id == reference.field_id, field.name
            assert field.base_type.wire_id == reference.base_type.value, field.name
            assert field.base_type.size == reference.base_type.size, field.name
            assert field.scale == reference.scale, field.name
            assert field.offset == reference.offset, field.name
        assert [field.name for field in spec.fields] == sorted(
            (field.name for field in spec.fields), key=order.index
        )


_FILE_IDS = [
    {"type": 4, "manufacturer": 255, "product": 0, "time_created": _T},
    {"type": 4, "manufacturer": 1, "product": 3076, "time_created": _T, "product_name": "Forerunner 245"},
    {"type": 4, "manufacturer": 32, "product": 43, "serial_number": 123456789, "time_created": _T,
     "product_name": "Fēnix ✓ – über Wahoo"},
    {"type": 4, "manufacturer": 294, "product": 65534, "serial_number": 4294967295, "time_created": _T + 999,
     "product_name": "x" * 50},
    {"type": 4, "manufacturer": 1, "product": 1, "time_created": _T, "product_name": "ü" * 30},
]

_RECORDS = [
    {"timestamp": _T, "position_lat": 45.0, "position_long": 7.0, "altitude": 100.0, "distance": 0.0,
     "speed": 3.3333333, "enhanced_speed": 3.3333333},
    {"timestamp": _T + 1000, "position_lat": -33.8688123, "position_long": 151.2093456, "altitude": -430.5,
     "distance": 12345.678, "speed": 0.0, "enhanced_speed": 0.0},
    {"timestamp": _T + 2000, "position_lat": 27.9881, "position_long": 86.925, "altitude": 8848.86,
     "distance": 0.125, "speed": 65.535, "enhanced_speed": 65.535},
    {"timestamp": _T + 3000, "position_lat": -90.0, "position_long": -180.0, "altitude": -500.0,
     "distance": 42_000_000.0, "speed": 1.0005, "enhanced_speed": 1.0005},
    {"timestamp": _T + 1500, "position_lat": 89.999999, "position_long": 179.99999, "altitude": 12607.0,
     "distance": 0.375, "speed": 0.0015, "enhanced_speed": 99.9},
    {"timestamp": _T + 2499, "position_lat": 1e-9, "position_long": -1e-9, "altitude": None,
     "distance": 5.005, "speed": 2.2225, "enhanced_speed": 2.2225},
]

_SUMMARIES = [
    {"start_time": _T, "timestamp": _T + 3_600_000, "total_elapsed_time": 3600.0, "total_timer_time": 3000.0,
     "total_distance": 10000.0, "total_ascent": 250, "sport": 1, "avg_speed": 3.3333333,
     "enhanced_avg_speed": 3.3333333, "max_speed": 5.0, "enhanced_max_speed": 5.0},
    {"start_time": _T, "timestamp": _T + 86_400_000, "total_elapsed_time": 86400.0, "total_timer_time": 0.0,
     "total_distance": 0.0, "total_ascent": 0, "sport": 11, "avg_speed": 0.0,
     "enhanced_avg_speed": 0.0, "max_speed": 0.0, "enhanced_max_speed": 0.0},
    {"start_time": _T + 500, "timestamp": _T + 11_500, "total_elapsed_time": 11.0, "total_timer_time": 10.9995,
     "total_distance": 100.125, "total_ascent": 8848, "sport": 1, "avg_speed": 9.0909,
     "enhanced_avg_speed": 9.0909, "max_speed": 65.535, "enhanced_max_speed": 65.535},
]


class TestSingleMessageParity:
    @pytest.mark.parametrize("values", _FILE_IDS)
    def test_file_id(self, values):
        _assert_same(_ours(FILE_ID, values), _fit_tool_bytes(_fit_tool_message(FileIdMessage, values)))

    @pytest.mark.parametrize("sport", [1, 11])
    def test_sport(self, sport):
        values = {"sport": sport}
        _assert_same(_ours(SPORT, values), _fit_tool_bytes(_fit_tool_message(SportMessage, values)))

    @pytest.mark.parametrize("event_type, timestamp", [(0, _T), (4, _T + 61_000), (4, _T + 500)])
    def test_timer_event(self, event_type, timestamp):
        values = {"event": 0, "event_type": event_type, "timestamp": timestamp}
        _assert_same(_ours(EVENT, values), _fit_tool_bytes(_fit_tool_message(EventMessage, values)))

    @pytest.mark.parametrize("values", _RECORDS)
    def test_record(self, values):
        _assert_same(_ours(RECORD, values), _fit_tool_bytes(_fit_tool_message(RecordMessage, values)))

    @pytest.mark.parametrize("values", _SUMMARIES)
    def test_lap(self, values):
        _assert_same(_ours(LAP, values), _fit_tool_bytes(_fit_tool_message(LapMessage, values)))

    @pytest.mark.parametrize("values", _SUMMARIES)
    def test_session(self, values):
        _assert_same(_ours(SESSION, values), _fit_tool_bytes(_fit_tool_message(SessionMessage, values)))

    @pytest.mark.parametrize("timer", [0.0, 3000.0, 10.9995, 86400.0])
    def test_activity(self, timer):
        values = {"timestamp": _T + 3_600_000, "total_timer_time": timer, "num_sessions": 1, "type": 0}
        _assert_same(_ours(ACTIVITY, values), _fit_tool_bytes(_fit_tool_message(ActivityMessage, values)))


class TestEdgeParity:
    def test_unscaled_float_is_truncated_like_fit_tool(self):
        values = {**_SUMMARIES[0], "total_ascent": 250.9}
        _assert_same(_ours(SESSION, values), _fit_tool_bytes(_fit_tool_message(SessionMessage, values)))

    def test_add_many_with_names_out_of_profile_order_matches_fit_tool(self):
        names = ("enhanced_speed", "distance", "position_long", "timestamp", "speed", "position_lat", "altitude")
        ours = FitEncoder()
        ours.add_many(RECORD, names, [tuple(cast(float, record[name]) for name in names) for record in _RECORDS[:5]])
        theirs = _fit_tool_bytes(*(_fit_tool_message(RecordMessage, record) for record in _RECORDS[:5]))
        _assert_same(ours.to_bytes(), theirs)


class TestSequenceParity:
    def test_records_between_events_redefine_like_fit_tool(self):
        event = {"event": 0, "event_type": 0, "timestamp": _T}
        records = [r for r in _RECORDS if r["altitude"] is not None]
        ours = FitEncoder()
        ours.add(EVENT, event)
        for record in records:
            ours.add(RECORD, record)
        ours.add(EVENT, {**event, "event_type": 4})
        ours.add(RECORD, _RECORDS[-1])  # no altitude: a new definition
        ours.add(RECORD, records[0])  # altitude again: another one

        theirs = _fit_tool_bytes(
            _fit_tool_message(EventMessage, event),
            *(_fit_tool_message(RecordMessage, record) for record in records),
            _fit_tool_message(EventMessage, {**event, "event_type": 4}),
            _fit_tool_message(RecordMessage, _RECORDS[-1]),
            _fit_tool_message(RecordMessage, records[0]),
        )
        _assert_same(ours.to_bytes(), theirs)


class TestRangeErrorsMatchFitTool:
    """Values fit_tool refuses, we refuse too; neither writes a wrapped-around number."""

    @pytest.mark.parametrize("name, value", [
        ("position_long", 180.0),
        ("position_lat", -180.00001),
        ("altitude", -501.0),
        ("altitude", 12608.0),
        ("speed", 65.536),
        ("distance", -0.01),
        ("timestamp", 0),
    ])
    def test_out_of_range_record_values(self, name, value):
        values = {**_RECORDS[0], name: value}
        with pytest.raises(Exception):
            _fit_tool_bytes(_fit_tool_message(RecordMessage, values))
        with pytest.raises(ValueError):
            _ours(RECORD, values)
