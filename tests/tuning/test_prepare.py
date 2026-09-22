"""Reshaping a recording into the kind of input the converter actually gets."""

from bisect import bisect_left
from datetime import timedelta

import pytest

from gpx2fit.core.fit_writer import write_fit
from gpx2fit.core.gpx_reader import parse_gpx_bytes
from gpx2fit.core.models import SportType
from tuning.fit_reader import UnreadableActivity, read_reference_activity
from tuning.prepare import prepare_reference
from tests.tuning.conftest import synthetic_activity, with_standing_pause


def _prepared(track=None, **kwargs):
    """Read and prepare a synthetic activity in one step."""
    track = track if track is not None else synthetic_activity()
    return prepare_reference(read_reference_activity(write_fit(track), "probe"), **kwargs)


class TestResampling:
    def test_point_spacing_is_near_the_target(self):
        prepared = _prepared(spacing_m=30.0)
        gaps = [
            later.distance_from_start - earlier.distance_from_start
            for earlier, later in zip(prepared.track.points, prepared.track.points[1:])
        ]
        # The recording is at 16 m, so a 30 m target lands on every other point.
        assert 28.0 <= min(gaps[:-1]) and max(gaps) <= 40.0

    def test_a_wider_spacing_keeps_fewer_points(self):
        assert len(_prepared(spacing_m=50.0).track.points) < len(_prepared(spacing_m=15.0).track.points)

    def test_resampling_keeps_the_full_route_length(self):
        dense = _prepared(spacing_m=15.0)
        sparse = _prepared(spacing_m=60.0)
        assert sparse.distance_m == pytest.approx(dense.distance_m, rel=0.02)

    def test_resampling_selects_real_points_rather_than_interpolating(self):
        """Every kept point must be one the athlete actually recorded, not a midpoint between two.

        Matched with a tolerance rather than exactly: FIT stores position in
        semicircles and the emulated GPX writes seven decimals, so a point
        survives the round trip to within centimetres, not bit-for-bit.
        """
        activity = read_reference_activity(write_fit(synthetic_activity()), "probe")
        prepared = prepare_reference(activity, spacing_m=40.0)
        recorded = sorted(point.lat for point in activity.track.points)
        # The recording steps 16 m per point, so anything interpolated would
        # land tens of metres from every real point rather than centimetres.
        for point in prepared.track.points:
            index = bisect_left(recorded, point.lat)
            neighbours = recorded[max(0, index - 1): index + 1]
            assert min(abs(point.lat - lat) for lat in neighbours) < 1e-6


class TestElevation:
    def test_elevation_is_quantized_to_whole_metres(self):
        for point in _prepared().track.points:
            assert point.elevation == pytest.approx(round(point.elevation))

    def test_a_coarser_step_quantizes_further(self):
        prepared = _prepared(elevation_step_m=5.0)
        for point in prepared.track.points:
            assert point.elevation % 5.0 == pytest.approx(0.0)

    def test_an_activity_with_flat_elevation_is_rejected(self):
        with pytest.raises(UnreadableActivity, match="elevation never changes"):
            _prepared(synthetic_activity(amplitude=0.0))


class TestReferenceTiming:
    def test_reference_starts_at_zero_and_strictly_increases(self):
        reference = _prepared().reference_elapsed
        assert reference[0] == 0.0
        assert all(later > earlier for earlier, later in zip(reference, reference[1:]))

    def test_one_reference_time_per_track_point(self):
        prepared = _prepared()
        assert len(prepared.reference_elapsed) == len(prepared.track.points)

    def test_moving_time_matches_the_recording_when_nothing_was_stopped(self):
        track = synthetic_activity()
        prepared = _prepared(track)
        assert track.points[0].timestamp is not None and track.points[-1].timestamp is not None
        wall = (track.points[-1].timestamp - track.points[0].timestamp).total_seconds()
        assert prepared.moving_seconds == pytest.approx(wall, rel=0.01)


class TestStopRemoval:
    def test_a_timer_pause_is_removed_from_moving_time(self):
        track = synthetic_activity(point_count=400)
        stop_at = track.points[200]
        assert stop_at.timestamp is not None
        paused = type(stop_at)(
            lat=stop_at.lat, lon=stop_at.lon, elevation=stop_at.elevation,
            distance_from_start=stop_at.distance_from_start,
            timestamp=stop_at.timestamp + timedelta(minutes=15),
        )
        for point in track.points[201:]:
            assert point.timestamp is not None
            point.timestamp += timedelta(minutes=15)
        track.points.insert(201, paused)

        prepared = _prepared(track)
        assert prepared.excised_seconds == pytest.approx(900.0, abs=2.0)
        assert any("timer pause" in note for note in prepared.notes)

    def test_unpaused_standing_is_detected_and_removed(self):
        """Not every watch auto-pauses, so a lunch break can look like very slow movement."""
        plain = _prepared(synthetic_activity(point_count=400))
        standing = _prepared(with_standing_pause(synthetic_activity(point_count=400), 200, seconds=300))

        assert standing.excised_seconds == pytest.approx(300.0, abs=15.0)
        assert any("standing" in note for note in standing.notes)
        # The break must not be charged to the surrounding legs' pace.
        assert standing.moving_seconds == pytest.approx(plain.moving_seconds, rel=0.02)

    def test_a_brief_slow_patch_is_left_alone(self):
        """A gate or a road crossing is part of how the route paces, not a stop."""
        prepared = _prepared(with_standing_pause(synthetic_activity(point_count=400), 200, seconds=8))
        assert prepared.excised_seconds == pytest.approx(0.0, abs=1.0)


class TestTrimming:
    def test_trim_narrows_the_route_to_the_requested_range(self):
        full = _prepared()
        trimmed = _prepared(trim_km=(2.0, 6.0))

        assert trimmed.distance_m < full.distance_m
        assert trimmed.distance_m == pytest.approx(4000.0, rel=0.05)
        assert any("trimmed" in note for note in trimmed.notes)

    def test_trim_resets_the_reference_clock_to_the_new_start(self):
        assert _prepared(trim_km=(2.0, 6.0)).reference_elapsed[0] == 0.0

    def test_a_trim_that_keeps_almost_nothing_is_rejected(self):
        with pytest.raises(UnreadableActivity, match="keeps only"):
            _prepared(trim_km=(2.0, 2.05))


class TestQualityGates:
    def test_a_very_short_activity_is_rejected(self):
        with pytest.raises(UnreadableActivity, match="need at least"):
            _prepared(synthetic_activity(point_count=20))


class TestSynthesizedGpx:
    def test_the_gpx_parses_back_to_the_same_track(self):
        """The emulated input goes through the real production entry point, so it must be real GPX."""
        prepared = _prepared()
        reparsed = parse_gpx_bytes(prepared.gpx_bytes)

        assert len(reparsed.points) == len(prepared.track.points)
        assert reparsed.total_distance == pytest.approx(prepared.distance_m)

    def test_the_track_carries_the_activity_sport(self):
        prepared = _prepared(synthetic_activity(sport=SportType.RUNNING))
        assert prepared.track.sport == SportType.RUNNING
