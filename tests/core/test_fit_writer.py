from datetime import datetime, timedelta, timezone

import pytest
from fit_tool.fit_file import FitFile
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.profile_type import Event, EventType, Manufacturer, Sport

from gpx2fit.core.fit_writer import _fit_timestamp, write_fit
from gpx2fit.core.models import SportType, Track
from tests.core.conftest import point, timestamp_of


class TestFitTimestamp:
    def test_epoch_is_zero(self):
        assert _fit_timestamp(datetime(1970, 1, 1, tzinfo=timezone.utc)) == 0

    def test_naive_datetime_is_treated_as_utc(self):
        naive = datetime(1970, 1, 1, 0, 0, 1)
        aware = datetime(1970, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
        assert _fit_timestamp(naive) == _fit_timestamp(aware) == 1000

    def test_aware_datetime_in_another_timezone_converts_to_utc(self):
        plus_two = timezone(timedelta(hours=2))
        # 02:00 in UTC+2 is 00:00 UTC, i.e. the epoch.
        local = datetime(1970, 1, 1, 2, 0, 0, tzinfo=plus_two)
        assert _fit_timestamp(local) == 0

    def test_result_is_milliseconds_not_seconds(self):
        one_second_after_epoch = datetime(1970, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
        assert _fit_timestamp(one_second_after_epoch) == 1000


class TestWriteFitValidation:
    def test_empty_track_raises(self):
        with pytest.raises(RuntimeError):
            write_fit(Track(points=[]))

    def test_missing_start_timestamp_raises(self):
        track = Track(points=[
            point(distance_from_start=0.0, timestamp=None),
            point(distance_from_start=10.0, timestamp=datetime(2024, 1, 1, 8, 1, 0)),
        ])
        with pytest.raises(RuntimeError):
            write_fit(track)

    def test_missing_final_timestamp_raises(self):
        track = Track(points=[
            point(distance_from_start=0.0, timestamp=datetime(2024, 1, 1, 8, 0, 0)),
            point(distance_from_start=10.0, timestamp=None),
        ])
        with pytest.raises(RuntimeError):
            write_fit(track)

    def test_final_timestamp_not_after_start_raises(self):
        same_time = datetime(2024, 1, 1, 8, 0, 0)
        track = Track(points=[
            point(distance_from_start=0.0, timestamp=same_time),
            point(distance_from_start=10.0, timestamp=same_time),
        ])
        with pytest.raises(RuntimeError):
            write_fit(track)

    def test_missing_timestamp_on_an_interior_point_raises(self):
        track = Track(points=[
            point(distance_from_start=0.0, timestamp=datetime(2024, 1, 1, 8, 0, 0)),
            point(distance_from_start=5.0, timestamp=None),
            point(distance_from_start=10.0, timestamp=datetime(2024, 1, 1, 8, 1, 0)),
        ])
        with pytest.raises(RuntimeError):
            write_fit(track)


class TestWriteFitOutput:
    def _build_track(self, sport: SportType | None, device: str | None = None) -> Track:
        return Track(
            points=[
                point(lat=45.0, lon=7.0, elevation=100.0, distance_from_start=0.0, timestamp=datetime(2024, 1, 1, 8, 0, 0)),
                point(lat=45.001, lon=7.0, elevation=0.0, distance_from_start=100.0, timestamp=datetime(2024, 1, 1, 8, 0, 30)),
                point(lat=45.002, lon=7.0, elevation=120.0, distance_from_start=250.0, timestamp=datetime(2024, 1, 1, 8, 1, 0)),
            ],
            sport=sport,
            device=device,
        )

    def test_produces_nonempty_bytes(self):
        assert len(write_fit(self._build_track(SportType.RUNNING))) > 0

    def test_records_are_written_once_per_second(self):
        # Points at 0, 30 and 60 s: one record per whole second, not one per point.
        track = self._build_track(SportType.RUNNING)
        records = _records(track)
        assert [record.timestamp for record in records] == [
            _fit_timestamp(_T0 + timedelta(seconds=second)) for second in range(61)
        ]

    def test_records_at_a_track_points_time_match_that_point(self):
        track = self._build_track(SportType.RUNNING)
        records = _records(track)

        for pt, record in zip(track.points, (records[0], records[30], records[60])):
            assert record.position_lat == pytest.approx(pt.lat, abs=1e-6)
            assert record.position_long == pytest.approx(pt.lon, abs=1e-6)
            assert record.distance == pytest.approx(pt.distance_from_start)
            assert record.altitude == pytest.approx(pt.elevation, abs=0.2)

    def test_a_long_leg_is_interpolated_along_its_length(self):
        # Halfway through the first leg (0 -> 100 m over 30 s, 100 m -> 0 m elevation).
        record = _records(self._build_track(SportType.RUNNING))[15]
        assert record.distance == pytest.approx(50.0)
        assert record.position_lat == pytest.approx(45.0005, abs=1e-6)
        assert record.altitude == pytest.approx(50.0, abs=0.2)

    def test_sea_level_elevation_is_written_as_altitude(self):
        # The middle point sits at 0.0 m: a real height on a track that has elevation.
        track = self._build_track(SportType.RUNNING)
        decoded = FitFile.from_bytes(write_fit(track))
        records = [r.message for r in decoded.records if isinstance(r.message, RecordMessage)]

        assert records[0].altitude == pytest.approx(100.0, abs=0.2)
        assert records[30].altitude == pytest.approx(0.0, abs=0.2)
        assert records[60].altitude == pytest.approx(120.0, abs=0.2)

    def test_track_without_elevation_omits_altitude(self):
        track = self._build_track(SportType.RUNNING)
        track.has_elevation = False
        decoded = FitFile.from_bytes(write_fit(track))
        records = [r.message for r in decoded.records if isinstance(r.message, RecordMessage)]

        assert all(record.altitude is None for record in records)

    def test_running_sport_maps_to_running(self):
        decoded = FitFile.from_bytes(write_fit(self._build_track(SportType.RUNNING)))
        lap = next(r.message for r in decoded.records if isinstance(r.message, LapMessage))
        assert lap.sport == Sport.RUNNING.value

    def test_hiking_sport_maps_to_walking(self):
        decoded = FitFile.from_bytes(write_fit(self._build_track(SportType.HIKING)))
        lap = next(r.message for r in decoded.records if isinstance(r.message, LapMessage))
        assert lap.sport == Sport.WALKING.value

    def test_no_sport_defaults_to_running(self):
        decoded = FitFile.from_bytes(write_fit(self._build_track(None)))
        lap = next(r.message for r in decoded.records if isinstance(r.message, LapMessage))
        assert lap.sport == Sport.RUNNING.value

    def test_device_name_is_set_as_product_name(self):
        decoded = FitFile.from_bytes(write_fit(self._build_track(SportType.RUNNING, device="My Watch")))
        file_id = next(r.message for r in decoded.records if isinstance(r.message, FileIdMessage))
        assert file_id.product_name == "My Watch"

    def test_no_device_name_omits_product_name(self):
        decoded = FitFile.from_bytes(write_fit(self._build_track(SportType.RUNNING)))
        file_id = next(r.message for r in decoded.records if isinstance(r.message, FileIdMessage))
        assert not file_id.product_name

    def test_a_name_too_long_for_a_fit_string_is_cut_to_fit(self):
        # The name can come from a GPX's creator attribute, which has no length
        # limit; a FIT field holds at most 255 bytes, its NUL included.
        decoded = FitFile.from_bytes(write_fit(self._build_track(SportType.RUNNING, device="x" * 300)))
        file_id = next(r.message for r in decoded.records if isinstance(r.message, FileIdMessage))
        assert file_id.product_name == "x" * 254

    def test_a_long_multibyte_name_is_cut_between_characters(self):
        # "✓" is 3 UTF-8 bytes: 84 of them (252 bytes) fit, an 85th wouldn't.
        decoded = FitFile.from_bytes(write_fit(self._build_track(SportType.RUNNING, device="✓" * 100)))
        file_id = next(r.message for r in decoded.records if isinstance(r.message, FileIdMessage))
        assert file_id.product_name == "✓" * 84

    def _file_id(self, track: Track) -> FileIdMessage:
        decoded = FitFile.from_bytes(write_fit(track))
        return next(r.message for r in decoded.records if isinstance(r.message, FileIdMessage))

    def test_no_device_writes_development_ids_and_no_serial(self):
        file_id = self._file_id(self._build_track(SportType.RUNNING))
        assert file_id.manufacturer == Manufacturer.DEVELOPMENT.value
        assert file_id.product == 0
        assert file_id.serial_number is None

    def test_chosen_device_ids_are_written_to_file_id(self):
        track = self._build_track(SportType.RUNNING)
        track.device_manufacturer = 1
        track.device_product = 4315
        file_id = self._file_id(track)
        assert (file_id.manufacturer, file_id.product) == (1, 4315)
        assert file_id.serial_number is None

    def test_serial_number_is_written_only_when_given(self):
        track = self._build_track(SportType.RUNNING)
        track.device_manufacturer = 32
        track.device_product = 43
        track.device_serial = 123456789
        assert self._file_id(track).serial_number == 123456789

    def test_lap_summary_matches_track_totals(self):
        track = self._build_track(SportType.RUNNING)
        decoded = FitFile.from_bytes(write_fit(track))
        lap = next(r.message for r in decoded.records if isinstance(r.message, LapMessage))

        assert lap.total_distance == pytest.approx(track.total_distance)
        assert lap.total_ascent == 120  # 100 m down to 0 m, then up to 120 m
        assert lap.total_elapsed_time == pytest.approx(
            (timestamp_of(track.points[-1]) - timestamp_of(track.points[0])).total_seconds()
        )

    def test_total_ascent_ignores_dem_rounding_wiggles(self):
        # A flat road whose whole-metre DEM elevation flickers 100/102 m every point.
        track = Track(points=[
            point(elevation=100.0 + 2.0 * (i % 2), distance_from_start=i * 10.0, timestamp=_T0 + timedelta(seconds=i * 3))
            for i in range(201)
        ])
        decoded = FitFile.from_bytes(write_fit(track))
        session = next(r.message for r in decoded.records if isinstance(r.message, SessionMessage))
        assert session.total_ascent == 0

    def test_record_speed_is_the_paced_speed_of_its_leg(self):
        # Written explicitly so FIT readers that expect a speed field
        # (rather than deriving it themselves from distance/timestamp) can
        # still show pace. Leg 1 is 100m/30s, leg 2 is 150m/30s; a record on
        # a point takes the leg ending there, the first record the first leg.
        records = _records(self._build_track(SportType.RUNNING))

        leg1_speed = 100.0 / 30.0
        leg2_speed = 150.0 / 30.0
        assert all(r.speed == pytest.approx(leg1_speed, abs=0.01) for r in records[:31])
        assert all(r.speed == pytest.approx(leg2_speed, abs=0.01) for r in records[31:])
        assert records[0].enhanced_speed == pytest.approx(leg1_speed, abs=0.01)

    def test_lap_avg_and_max_speed_match_track_totals(self):
        track = self._build_track(SportType.RUNNING)
        decoded = FitFile.from_bytes(write_fit(track))
        lap = next(r.message for r in decoded.records if isinstance(r.message, LapMessage))

        total_seconds = (timestamp_of(track.points[-1]) - timestamp_of(track.points[0])).total_seconds()
        assert lap.avg_speed == pytest.approx(track.total_distance / total_seconds, abs=0.01)
        assert lap.max_speed == pytest.approx(150.0 / 30.0, abs=0.01)


def _decoded_messages(track: Track) -> list:
    return [r.message for r in FitFile.from_bytes(write_fit(track)).records]


def _records(track: Track) -> list[RecordMessage]:
    return [m for m in _decoded_messages(track) if isinstance(m, RecordMessage)]


def _record_seconds(messages: list) -> list[int]:
    """Each record's whole seconds after _T0."""
    return [
        (m.timestamp - _fit_timestamp(_T0)) // 1000
        for m in messages
        if isinstance(m, RecordMessage) and m.timestamp is not None
    ]


def _timer_events(messages: list) -> list[tuple[int, int]]:
    """(event_type, timestamp) for every timer event, in file order."""
    return [
        (m.event_type, m.timestamp)
        for m in messages
        if isinstance(m, EventMessage) and m.event == Event.TIMER.value
    ]


_T0 = datetime(2024, 1, 1, 8, 0, 0)


def _track_with_stop() -> Track:
    """0 m -> 100 m in 60 s, a 10-minute stop at 100 m, then 100 m more in 60 s."""
    return Track(
        points=[
            point(distance_from_start=0.0, timestamp=_T0),
            point(distance_from_start=100.0, timestamp=_T0 + timedelta(seconds=60)),
            point(distance_from_start=100.0, timestamp=_T0 + timedelta(seconds=660)),
            point(distance_from_start=200.0, timestamp=_T0 + timedelta(seconds=720)),
        ],
        sport=SportType.RUNNING,
    )


class TestWriteFitTimerEvents:
    # Without timer events, Strava derives moving time purely from speed
    # and discards anything slower than its resting threshold; with them,
    # it uses the recorded timer time instead.

    def test_timer_starts_before_the_first_record_and_stops_after_the_last(self):
        track = TestWriteFitOutput()._build_track(SportType.RUNNING)
        messages = _decoded_messages(track)
        record_indexes = [i for i, m in enumerate(messages) if isinstance(m, RecordMessage)]
        event_indexes = [i for i, m in enumerate(messages) if isinstance(m, EventMessage)]

        assert _timer_events(messages) == [
            (EventType.START.value, _fit_timestamp(timestamp_of(track.points[0]))),
            (EventType.STOP_ALL.value, _fit_timestamp(timestamp_of(track.points[-1]))),
        ]
        assert event_indexes[0] < record_indexes[0]
        assert event_indexes[-1] > record_indexes[-1]

    def test_a_stop_is_written_as_a_timer_pause_between_arrival_and_departure(self):
        messages = _decoded_messages(_track_with_stop())

        assert _timer_events(messages) == [
            (EventType.START.value, _fit_timestamp(_T0)),
            (EventType.STOP_ALL.value, _fit_timestamp(_T0 + timedelta(seconds=60))),
            (EventType.START.value, _fit_timestamp(_T0 + timedelta(seconds=660))),
            (EventType.STOP_ALL.value, _fit_timestamp(_T0 + timedelta(seconds=720))),
        ]
        # The pause sits between the arrival record and the departure record,
        # with nothing recorded while stopped.
        kinds = [
            "record" if isinstance(m, RecordMessage) else m.event_type
            for m in messages
            if isinstance(m, RecordMessage) or isinstance(m, EventMessage)
        ]
        assert kinds == [
            EventType.START.value, *["record"] * 61,
            EventType.STOP_ALL.value, EventType.START.value,
            *["record"] * 61, EventType.STOP_ALL.value,
        ]
        assert _record_seconds(messages) == [*range(0, 61), *range(660, 721)]

    def test_timer_time_excludes_stops_but_elapsed_time_includes_them(self):
        messages = _decoded_messages(_track_with_stop())

        for summary_type in (LapMessage, SessionMessage):
            summary = next(m for m in messages if isinstance(m, summary_type))
            assert summary.total_elapsed_time == pytest.approx(720.0)
            assert summary.total_timer_time == pytest.approx(120.0)
        activity = next(m for m in messages if isinstance(m, ActivityMessage))
        assert activity.total_timer_time == pytest.approx(120.0)

    def test_avg_speed_is_based_on_timer_time_not_elapsed_time(self):
        messages = _decoded_messages(_track_with_stop())
        session = next(m for m in messages if isinstance(m, SessionMessage))
        assert session.avg_speed == pytest.approx(200.0 / 120.0, abs=0.01)

    def test_zero_distance_leg_without_elapsed_time_is_not_a_pause(self):
        # A duplicated GPX point costs no time, so it isn't a stop.
        track = Track(points=[
            point(distance_from_start=0.0, timestamp=_T0),
            point(distance_from_start=50.0, timestamp=_T0 + timedelta(seconds=30)),
            point(distance_from_start=50.0, timestamp=_T0 + timedelta(seconds=30)),
            point(distance_from_start=100.0, timestamp=_T0 + timedelta(seconds=60)),
        ])
        messages = _decoded_messages(track)

        assert [event_type for event_type, _ in _timer_events(messages)] == [
            EventType.START.value, EventType.STOP_ALL.value,
        ]
        session = next(m for m in messages if isinstance(m, SessionMessage))
        assert session.total_timer_time == pytest.approx(60.0)


class TestWriteFitOneHertzGrid:
    # FIT timestamps are whole seconds, so a record per GPX point would put
    # several points of a dense GPX on one second (a distance jump in zero
    # time) and leave a sparse GPX 30+ s between records.

    def test_dense_track_gives_one_record_per_second_without_duplicates(self):
        # A point every metre, 0.2 s apart: 100 m in 20 s.
        track = Track(points=[
            point(distance_from_start=float(i), timestamp=_T0 + timedelta(seconds=i * 0.2)) for i in range(101)
        ])
        records = _records(track)

        assert [r.timestamp for r in records] == [_fit_timestamp(_T0 + timedelta(seconds=s)) for s in range(21)]
        assert [r.distance for r in records] == pytest.approx([5.0 * s for s in range(21)])

    def test_fractional_start_and_end_snap_to_the_nearest_second(self):
        track = Track(points=[
            point(distance_from_start=0.0, timestamp=_T0 + timedelta(seconds=0.4)),
            point(distance_from_start=100.0, timestamp=_T0 + timedelta(seconds=10.6)),
        ])
        messages = _decoded_messages(track)
        records = [m for m in messages if isinstance(m, RecordMessage)]

        assert [r.timestamp for r in records] == [_fit_timestamp(_T0 + timedelta(seconds=s)) for s in range(12)]
        assert records[0].distance == pytest.approx(0.0)
        assert records[-1].distance == pytest.approx(100.0)
        session = next(m for m in messages if isinstance(m, SessionMessage))
        assert session.start_time == _fit_timestamp(_T0)
        assert session.total_elapsed_time == pytest.approx(11.0)

    def test_fractional_stop_times_snap_and_the_pause_matches_the_records(self):
        track = Track(points=[
            point(distance_from_start=0.0, timestamp=_T0),
            point(distance_from_start=100.0, timestamp=_T0 + timedelta(seconds=30.4)),
            point(distance_from_start=100.0, timestamp=_T0 + timedelta(seconds=90.6)),
            point(distance_from_start=200.0, timestamp=_T0 + timedelta(seconds=120)),
        ])
        messages = _decoded_messages(track)

        assert _timer_events(messages) == [
            (EventType.START.value, _fit_timestamp(_T0)),
            (EventType.STOP_ALL.value, _fit_timestamp(_T0 + timedelta(seconds=30))),
            (EventType.START.value, _fit_timestamp(_T0 + timedelta(seconds=91))),
            (EventType.STOP_ALL.value, _fit_timestamp(_T0 + timedelta(seconds=120))),
        ]
        assert _record_seconds(messages) == [*range(0, 31), *range(91, 121)]
        session = next(m for m in messages if isinstance(m, SessionMessage))
        assert session.total_timer_time == pytest.approx(120.0 - 61.0)

    def test_a_stop_shorter_than_half_a_second_is_not_a_pause(self):
        track = Track(points=[
            point(distance_from_start=0.0, timestamp=_T0),
            point(distance_from_start=50.0, timestamp=_T0 + timedelta(seconds=30.1)),
            point(distance_from_start=50.0, timestamp=_T0 + timedelta(seconds=30.3)),
            point(distance_from_start=100.0, timestamp=_T0 + timedelta(seconds=60)),
        ])
        messages = _decoded_messages(track)

        assert [event_type for event_type, _ in _timer_events(messages)] == [
            EventType.START.value, EventType.STOP_ALL.value,
        ]
        assert _record_seconds(messages) == list(range(61))

    def test_max_speed_is_the_fastest_record(self):
        track = TestWriteFitOutput()._build_track(SportType.RUNNING)
        session = next(m for m in _decoded_messages(track) if isinstance(m, SessionMessage))
        assert session.max_speed == pytest.approx(150.0 / 30.0, abs=0.01)


class TestWriteFitPerPoint:
    # resample=False: one record per point at its own time, as a watches smart
    # recording writes. The tuning harness's synthetic recordings use it.

    def test_one_record_per_point_at_its_own_time(self):
        track = TestWriteFitOutput()._build_track(SportType.RUNNING)
        records = [m for m in _decoded_messages_of(track) if isinstance(m, RecordMessage)]

        assert [r.timestamp for r in records] == [_fit_timestamp(timestamp_of(p)) for p in track.points]
        assert [r.distance for r in records] == pytest.approx([p.distance_from_start for p in track.points])
        # The speed of the leg ending at each point; the first point takes the first leg's.
        assert [r.speed for r in records] == pytest.approx([100.0 / 30.0, 100.0 / 30.0, 150.0 / 30.0], abs=0.01)

    def test_a_stop_is_still_a_timer_pause(self):
        messages = _decoded_messages_of(_track_with_stop())

        assert _timer_events(messages) == [
            (EventType.START.value, _fit_timestamp(_T0)),
            (EventType.STOP_ALL.value, _fit_timestamp(_T0 + timedelta(seconds=60))),
            (EventType.START.value, _fit_timestamp(_T0 + timedelta(seconds=660))),
            (EventType.STOP_ALL.value, _fit_timestamp(_T0 + timedelta(seconds=720))),
        ]
        assert len([m for m in messages if isinstance(m, RecordMessage)]) == 4
        session = next(m for m in messages if isinstance(m, SessionMessage))
        assert session.total_timer_time == pytest.approx(120.0)


def _decoded_messages_of(track: Track) -> list:
    """Like _decoded_messages, but one record per point."""
    return [r.message for r in FitFile.from_bytes(write_fit(track, resample=False)).records]
