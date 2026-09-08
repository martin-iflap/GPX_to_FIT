from datetime import datetime, timedelta, timezone

import pytest
from fit_tool.fit_file import FitFile
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.profile_type import Sport

from gpx2fit.core.fit_writer import _fit_timestamp, write_fit
from gpx2fit.core.models import SportType, Track, TrackPoint


def _point(lat: float, lon: float, elevation: float, distance: float, timestamp: datetime | None) -> TrackPoint:
    return TrackPoint(lat=lat, lon=lon, elevation=elevation, distance_from_start=distance, timestamp=timestamp)


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
            _point(0.0, 0.0, 0.0, 0.0, timestamp=None),
            _point(0.0, 0.0, 0.0, 10.0, timestamp=datetime(2024, 1, 1, 8, 1, 0)),
        ])
        with pytest.raises(RuntimeError):
            write_fit(track)

    def test_missing_final_timestamp_raises(self):
        track = Track(points=[
            _point(0.0, 0.0, 0.0, 0.0, timestamp=datetime(2024, 1, 1, 8, 0, 0)),
            _point(0.0, 0.0, 0.0, 10.0, timestamp=None),
        ])
        with pytest.raises(RuntimeError):
            write_fit(track)

    def test_final_timestamp_not_after_start_raises(self):
        same_time = datetime(2024, 1, 1, 8, 0, 0)
        track = Track(points=[
            _point(0.0, 0.0, 0.0, 0.0, timestamp=same_time),
            _point(0.0, 0.0, 0.0, 10.0, timestamp=same_time),
        ])
        with pytest.raises(RuntimeError):
            write_fit(track)

    def test_missing_timestamp_on_an_interior_point_raises(self):
        track = Track(points=[
            _point(0.0, 0.0, 0.0, 0.0, timestamp=datetime(2024, 1, 1, 8, 0, 0)),
            _point(0.0, 0.0, 0.0, 5.0, timestamp=None),
            _point(0.0, 0.0, 0.0, 10.0, timestamp=datetime(2024, 1, 1, 8, 1, 0)),
        ])
        with pytest.raises(RuntimeError):
            write_fit(track)


class TestWriteFitOutput:
    def _build_track(self, sport: SportType | None, device: str | None = None) -> Track:
        return Track(
            points=[
                _point(45.0, 7.0, 100.0, 0.0, datetime(2024, 1, 1, 8, 0, 0)),
                _point(45.001, 7.0, 0.0, 100.0, datetime(2024, 1, 1, 8, 0, 30)),
                _point(45.002, 7.0, 120.0, 250.0, datetime(2024, 1, 1, 8, 1, 0)),
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

        for point, record in zip(track.points, records):
            assert record.position_lat == pytest.approx(point.lat, abs=1e-4)
            assert record.position_long == pytest.approx(point.lon, abs=1e-4)
            assert record.distance == pytest.approx(point.distance_from_start)

    def test_decoded_timestamps_match_fit_timestamp_conversion(self):
        track = self._build_track(SportType.RUNNING)
        decoded = FitFile.from_bytes(write_fit(track))
        records = [r.message for r in decoded.records if isinstance(r.message, RecordMessage)]

        for point, record in zip(track.points, records):
            assert record.timestamp == _fit_timestamp(point.timestamp)

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
            (track.points[-1].timestamp - track.points[0].timestamp).total_seconds()
        )
