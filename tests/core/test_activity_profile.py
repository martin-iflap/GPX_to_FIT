from datetime import timedelta

import pytest

from gpx2fit.core.activity_profile import build_activity_profile
from gpx2fit.core.models import ModeAStop, SportType, Track
from gpx2fit.core.pacing.combine import combine
from gpx2fit.core.pacing.stops import expand_track_with_stops
from tests.core.conftest import START, anchor, point, rolling_track


def constant_speed_track(leg_count: int, leg_m: float, leg_seconds: float) -> Track:
    """A flat track covering `leg_m` metres every `leg_seconds` seconds."""
    return Track(points=[
        point(distance_from_start=i * leg_m, elevation=100.0, timestamp=START + timedelta(seconds=i * leg_seconds))
        for i in range(leg_count + 1)
    ])


class TestValidation:
    def test_empty_track_raises(self):
        with pytest.raises(RuntimeError):
            build_activity_profile(Track(points=[]))

    def test_missing_timestamp_raises(self):
        track = constant_speed_track(3, 10.0, 5.0)
        track.points[2].timestamp = None
        with pytest.raises(RuntimeError):
            build_activity_profile(track)


class TestDownsampling:
    def test_sample_count_is_bounded(self):
        track = constant_speed_track(5000, 2.0, 1.0)
        samples = build_activity_profile(track, max_samples=100)
        # One extra for the starting sample.
        assert len(samples) <= 101

    def test_short_track_keeps_every_point(self):
        track = constant_speed_track(4, 10.0, 5.0)
        samples = build_activity_profile(track, max_samples=1000)
        assert [s.distance_from_start for s in samples] == [0.0, 10.0, 20.0, 30.0, 40.0]

    def test_endpoints_match_track(self):
        track = constant_speed_track(500, 3.0, 2.0)
        samples = build_activity_profile(track, max_samples=50)
        assert samples[0].distance_from_start == 0.0
        assert samples[0].elapsed_seconds == 0.0
        assert samples[-1].distance_from_start == track.total_distance
        assert samples[-1].elapsed_seconds == 1000.0

    def test_bucket_speed_is_distance_over_time(self):
        track = constant_speed_track(500, 3.0, 2.0)
        samples = build_activity_profile(track, max_samples=50)
        for s in samples:
            assert s.speed_mps == pytest.approx(1.5)

    def test_speed_is_averaged_within_a_bucket(self):
        # 10 m in 5 s then 10 m in 15 s: one 20 m bucket averages 1.0 m/s, not (2.0 + 0.667) / 2.
        track = Track(points=[
            point(distance_from_start=0.0, timestamp=START),
            point(distance_from_start=10.0, timestamp=START + timedelta(seconds=5)),
            point(distance_from_start=20.0, timestamp=START + timedelta(seconds=20)),
        ])
        samples = build_activity_profile(track, max_samples=1)
        assert len(samples) == 2
        assert samples[1].speed_mps == pytest.approx(1.0)

    def test_missing_elevation_becomes_none(self):
        track = constant_speed_track(2, 10.0, 5.0)
        track.points[1].elevation = 0.0
        samples = build_activity_profile(track)
        assert samples[0].elevation == 100.0
        assert samples[1].elevation is None


class TestStops:
    def test_stop_emits_two_zero_speed_samples(self):
        # 100 m, a 60 s stop at 50 m (duplicated point), then 50 m more.
        track = Track(points=[
            point(distance_from_start=0.0, timestamp=START),
            point(distance_from_start=50.0, timestamp=START + timedelta(seconds=50)),
            point(distance_from_start=50.0, timestamp=START + timedelta(seconds=110)),
            point(distance_from_start=100.0, timestamp=START + timedelta(seconds=160)),
        ])
        samples = build_activity_profile(track)
        stop_samples = [s for s in samples if s.is_stop]
        assert [(s.elapsed_seconds, s.speed_mps) for s in stop_samples] == [(50.0, 0.0), (110.0, 0.0)]
        assert all(s.distance_from_start == 50.0 for s in stop_samples)
        # The moving legs either side keep their real speed.
        moving = [s for s in samples if not s.is_stop]
        assert all(s.speed_mps == pytest.approx(1.0) for s in moving)

    def test_zero_time_duplicate_is_not_a_stop(self):
        track = Track(points=[
            point(distance_from_start=0.0, timestamp=START),
            point(distance_from_start=50.0, timestamp=START + timedelta(seconds=50)),
            point(distance_from_start=50.0, timestamp=START + timedelta(seconds=50)),
            point(distance_from_start=100.0, timestamp=START + timedelta(seconds=100)),
        ])
        assert not any(s.is_stop for s in build_activity_profile(track))

    def test_mode_a_stop_through_combine(self):
        base = rolling_track(2000.0, 40.0)
        stop = ModeAStop(distance_from_start=1000.0, duration=timedelta(minutes=10))
        working = Track(points=expand_track_with_stops(base.points, [stop]))
        end = START + timedelta(hours=1)
        combine(working, [anchor(0.0, START), anchor(2000.0, end)], SportType.HIKING, mode_a_stops=[stop])

        samples = build_activity_profile(working)
        stop_samples = [s for s in samples if s.is_stop]
        assert len(stop_samples) == 2
        assert stop_samples[1].elapsed_seconds - stop_samples[0].elapsed_seconds == pytest.approx(600.0)
        assert samples[-1].elapsed_seconds == pytest.approx(3600.0)
        assert all(s.speed_mps > 0 for s in samples if not s.is_stop)
