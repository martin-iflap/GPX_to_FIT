"""Decoding a real FIT activity back into a Track."""

from datetime import timedelta

import pytest
from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.sport_message import SportMessage
from fit_tool.profile.profile_type import FileType, Manufacturer, Sport

from gpx2fit.core.fit_writer import _fit_timestamp, write_fit
from gpx2fit.core.models import SportType
from tuning.fit_reader import UnreadableActivity, _resolve_sport, read_reference_activity
from tests.tuning.conftest import START, synthetic_activity, synthetic_fit_bytes, with_standing_pause


class TestSportMapping:
    def test_running_maps_to_running(self):
        assert _resolve_sport("x", Sport.RUNNING.value) == SportType.RUNNING

    def test_walking_and_hiking_both_map_to_hiking(self):
        """The model has one gait for both; FIT distinguishes them and we don't need to."""
        assert _resolve_sport("x", Sport.WALKING.value) == SportType.HIKING
        assert _resolve_sport("x", Sport.HIKING.value) == SportType.HIKING

    def test_a_sport_with_no_pacing_model_is_rejected(self):
        with pytest.raises(UnreadableActivity, match="no pacing model"):
            _resolve_sport("ride", Sport.CYCLING.value)

    def test_a_file_declaring_no_sport_is_rejected(self):
        with pytest.raises(UnreadableActivity, match="declares no sport"):
            _resolve_sport("x", None)


class TestReadReferenceActivity:
    def test_round_trips_a_file_this_project_wrote(self):
        track = synthetic_activity(point_count=200)
        activity = read_reference_activity(write_fit(track), "probe")

        assert activity.name == "probe"
        assert activity.sport == SportType.HIKING
        assert len(activity.track.points) == len(track.points)

    def test_positions_and_elevations_survive_the_round_trip(self):
        track = synthetic_activity(point_count=120)
        activity = read_reference_activity(write_fit(track), "probe")

        for original, decoded in zip(track.points, activity.track.points):
            assert decoded.lat == pytest.approx(original.lat, abs=1e-4)
            assert decoded.lon == pytest.approx(original.lon, abs=1e-4)
            assert decoded.elevation == pytest.approx(original.elevation, abs=1.0)

    def test_timestamps_survive_to_the_second(self):
        track = synthetic_activity(point_count=120)
        activity = read_reference_activity(write_fit(track), "probe")

        first, last = activity.track.points[0], activity.track.points[-1]
        assert first.timestamp is not None and last.timestamp is not None
        recorded = (last.timestamp - first.timestamp).total_seconds()
        assert track.points[-1].timestamp is not None and track.points[0].timestamp is not None
        expected = (track.points[-1].timestamp - track.points[0].timestamp).total_seconds()
        assert recorded == pytest.approx(expected, abs=1.0)

    def test_running_activities_are_recognised(self):
        activity = read_reference_activity(
            synthetic_fit_bytes(sport=SportType.RUNNING, point_count=150), "run"
        )
        assert activity.sport == SportType.RUNNING

    def test_timer_pauses_are_extracted(self):
        """write_fit brackets a zero-distance stop in timer events; we read them back."""
        track = synthetic_activity(point_count=150)
        stop_at = track.points[70]
        assert stop_at.timestamp is not None
        paused = type(stop_at)(
            lat=stop_at.lat, lon=stop_at.lon, elevation=stop_at.elevation,
            distance_from_start=stop_at.distance_from_start,
            timestamp=stop_at.timestamp + timedelta(minutes=8),
        )
        for point in track.points[71:]:
            assert point.timestamp is not None
            point.timestamp += timedelta(minutes=8)
        track.points.insert(71, paused)

        activity = read_reference_activity(write_fit(track), "with-stop")
        assert len(activity.pauses) == 1
        assert activity.paused_seconds == pytest.approx(480.0, abs=1.0)

    def test_an_activity_without_pauses_reports_none(self):
        activity = read_reference_activity(synthetic_fit_bytes(point_count=150), "probe")
        assert activity.pauses == []
        assert activity.paused_seconds == 0.0

    def test_unpaused_standing_is_not_a_timer_pause(self):
        """Standing the watch never auto-paused leaves no timer event, by design."""
        track = with_standing_pause(synthetic_activity(point_count=200), at_index=90, seconds=120)
        activity = read_reference_activity(write_fit(track), "standing")
        assert activity.paused_seconds == 0.0

    def test_session_totals_are_carried_through(self):
        activity = read_reference_activity(synthetic_fit_bytes(point_count=200), "probe")
        assert activity.totals.distance_m is not None
        assert activity.totals.distance_m > 0
        assert activity.totals.elapsed_seconds is not None

    def test_undecodable_bytes_are_rejected_with_the_file_name(self):
        with pytest.raises(UnreadableActivity, match="nonsense"):
            read_reference_activity(b"not a fit file at all", "nonsense")

    def test_an_indoor_activity_with_no_positions_is_rejected(self):
        """A treadmill run records time but no GPS, so there's no route to pace."""
        builder = FitFileBuilder(auto_define=True, min_string_size=50)
        file_id = FileIdMessage()
        file_id.type = FileType.ACTIVITY
        file_id.manufacturer = Manufacturer.DEVELOPMENT.value
        file_id.product = 0
        file_id.time_created = _fit_timestamp(START)
        builder.add(file_id)

        sport_message = SportMessage()
        sport_message.sport = Sport.RUNNING
        builder.add(sport_message)

        for second in range(60):
            record = RecordMessage()
            record.timestamp = _fit_timestamp(START + timedelta(seconds=second))
            record.distance = second * 3.0
            builder.add(record)

        with pytest.raises(UnreadableActivity, match="no route to pace"):
            read_reference_activity(builder.build_bytes(), "treadmill")
