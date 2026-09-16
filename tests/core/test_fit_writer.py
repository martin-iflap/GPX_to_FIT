from datetime import datetime, timedelta, timezone

import pytest
from fit_tool.fit_file import FitFile
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.profile_type import Event, EventType, Sport

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

    def test_decoded_record_count_matches_point_count(self):
        track = self._build_track(SportType.RUNNING)
        decoded = FitFile.from_bytes(write_fit(track))
        records = [r.message for r in decoded.records if isinstance(r.message, RecordMessage)]
        assert len(records) == len(track.points)

    def test_decoded_positions_and_distances_match_the_source_track(self):
        track = self._build_track(SportType.RUNNING)
        decoded = FitFile.from_bytes(write_fit(track))
        records = [r.message for r in decoded.records if isinstance(r.message, RecordMessage)]

        for pt, record in zip(track.points, records):
            assert record.position_lat == pytest.approx(pt.lat, abs=1e-4)
            assert record.position_long == pytest.approx(pt.lon, abs=1e-4)
            assert record.distance == pytest.approx(pt.distance_from_start)

    def test_decoded_timestamps_match_fit_timestamp_conversion(self):
        track = self._build_track(SportType.RUNNING)
        decoded = FitFile.from_bytes(write_fit(track))
        records = [r.message for r in decoded.records if isinstance(r.message, RecordMessage)]

        for pt, record in zip(track.points, records):
            assert record.timestamp == _fit_timestamp(timestamp_of(pt))

    def test_zero_elevation_is_omitted_but_nonzero_elevation_is_kept(self):
        track = self._build_track(SportType.RUNNING)
        decoded = FitFile.from_bytes(write_fit(track))
        records = [r.message for r in decoded.records if isinstance(r.message, RecordMessage)]

        assert records[0].altitude == pytest.approx(100.0, abs=0.1)
        assert records[1].altitude is None
        assert records[2].altitude == pytest.approx(120.0, abs=0.1)

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

    def test_lap_summary_matches_track_totals(self):
        track = self._build_track(SportType.RUNNING)
        decoded = FitFile.from_bytes(write_fit(track))
        lap = next(r.message for r in decoded.records if isinstance(r.message, LapMessage))

        assert lap.total_distance == pytest.approx(track.total_distance)
        assert lap.total_ascent == round(track.total_elevation_gain)
        assert lap.total_elapsed_time == pytest.approx(
            (timestamp_of(track.points[-1]) - timestamp_of(track.points[0])).total_seconds()
        )

    def test_decoded_record_speed_matches_distance_and_timestamp_deltas(self):
        # Written explicitly so FIT readers that expect a speed field
        # (rather than deriving it themselves from distance/timestamp) can
        # still show pace. Leg 1 is 100m/30s, leg 2 is 150m/30s.
        track = self._build_track(SportType.RUNNING)
        decoded = FitFile.from_bytes(write_fit(track))
        records = [r.message for r in decoded.records if isinstance(r.message, RecordMessage)]

        leg1_speed = 100.0 / 30.0
        leg2_speed = 150.0 / 30.0
        assert records[0].speed == pytest.approx(leg1_speed, abs=0.01)
        assert records[1].speed == pytest.approx(leg1_speed, abs=0.01)
        assert records[2].speed == pytest.approx(leg2_speed, abs=0.01)
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
        # The pause sits between the arrival record and the departure record.
        kinds = [
            "record" if isinstance(m, RecordMessage) else m.event_type
            for m in messages
            if isinstance(m, RecordMessage) or isinstance(m, EventMessage)
        ]
        assert kinds == [
            EventType.START.value, "record", "record",
            EventType.STOP_ALL.value, EventType.START.value,
            "record", "record", EventType.STOP_ALL.value,
        ]

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
