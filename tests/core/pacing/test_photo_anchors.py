from datetime import datetime, timedelta

import pytest

from gpx2fit.core.models import RawPhotoAnchor, Track
from gpx2fit.core.pacing.anchors import distance_meters
from gpx2fit.core.pacing.photo_anchors import ACTIVITY_TIME_TOLERANCE, MAX_MATCH_DISTANCE_M, resolve_photo_anchors
from tests.conftest import point


class TestResolvePhotoAnchors:
    def test_matches_photo_to_nearest_track_point(self):
        # Points, not the space between them, are the only places a photo can
        # resolve to (same as manual lat/lon anchors) — no interpolation.
        track = Track(points=[
            point(lat=45.0, lon=7.0, distance_from_start=0.0),
            point(lat=45.001, lon=7.0, distance_from_start=500.0),
            point(lat=45.002, lon=7.0, distance_from_start=1000.0),
        ])
        raw = [RawPhotoAnchor(lat=45.0011, lon=7.0, timestamp=datetime(2024, 1, 1, 8, 30, 0))]

        resolved = resolve_photo_anchors(track, raw)

        assert resolved[0].distance_from_start == 500.0
        assert resolved[0].lat == 45.001
        assert resolved[0].lon == 7.0

    def test_gap_m_is_distance_between_raw_gps_and_matched_point(self):
        track = Track(points=[point(lat=45.0, lon=7.0, distance_from_start=0.0), point(lat=45.001, lon=7.0, distance_from_start=500.0)])
        raw = [RawPhotoAnchor(lat=45.0011, lon=7.0, timestamp=datetime(2024, 1, 1, 8, 30, 0))]

        resolved = resolve_photo_anchors(track, raw)

        expected_gap = distance_meters(45.0011, 7.0, 45.001, 7.0)
        assert resolved[0].gap_m == pytest.approx(expected_gap)

    def test_exact_match_has_zero_gap_and_ok_status(self):
        track = Track(points=[point(lat=45.0, lon=7.0, distance_from_start=0.0), point(lat=45.001, lon=7.0, distance_from_start=500.0)])
        raw = [RawPhotoAnchor(lat=45.001, lon=7.0, timestamp=datetime(2024, 1, 1, 8, 30, 0))]

        resolved = resolve_photo_anchors(track, raw)

        assert resolved[0].gap_m == pytest.approx(0.0)
        assert resolved[0].status == "ok"

    def test_status_ok_when_gap_within_default_threshold(self):
        track = Track(points=[point(lat=45.0, lon=7.0, distance_from_start=0.0)])
        # ~11m away, well under MAX_MATCH_DISTANCE_M.
        raw = [RawPhotoAnchor(lat=45.0001, lon=7.0, timestamp=datetime(2024, 1, 1, 8, 30, 0))]

        resolved = resolve_photo_anchors(track, raw)

        assert resolved[0].gap_m < MAX_MATCH_DISTANCE_M
        assert resolved[0].status == "ok"

    def test_status_too_far_when_gap_exceeds_default_threshold(self):
        track = Track(points=[point(lat=45.0, lon=7.0, distance_from_start=0.0)])
        # ~2.2km away, far beyond MAX_MATCH_DISTANCE_M.
        raw = [RawPhotoAnchor(lat=45.02, lon=7.0, timestamp=datetime(2024, 1, 1, 8, 30, 0))]

        resolved = resolve_photo_anchors(track, raw)

        assert resolved[0].gap_m > MAX_MATCH_DISTANCE_M
        assert resolved[0].status == "too_far"

    def test_custom_max_match_distance_m_is_respected(self):
        track = Track(points=[point(lat=45.0, lon=7.0, distance_from_start=0.0)])
        raw = [RawPhotoAnchor(lat=45.02, lon=7.0, timestamp=datetime(2024, 1, 1, 8, 30, 0))]

        assert resolve_photo_anchors(track, raw, max_match_distance_m=10.0)[0].status == "too_far"
        assert resolve_photo_anchors(track, raw, max_match_distance_m=10_000.0)[0].status == "ok"

    def test_gap_exactly_at_threshold_is_ok(self):
        track = Track(points=[point(lat=45.0, lon=7.0, distance_from_start=0.0)])
        gap = distance_meters(45.0, 7.0, 45.001, 7.0)
        raw = [RawPhotoAnchor(lat=45.001, lon=7.0, timestamp=datetime(2024, 1, 1, 8, 30, 0))]

        resolved = resolve_photo_anchors(track, raw, max_match_distance_m=gap)

        assert resolved[0].status == "ok"

    def test_timestamp_is_carried_through_unchanged(self):
        track = Track(points=[point(lat=45.0, lon=7.0, distance_from_start=0.0)])
        timestamp = datetime(2024, 3, 15, 14, 5, 30)
        raw = [RawPhotoAnchor(lat=45.0, lon=7.0, timestamp=timestamp)]

        resolved = resolve_photo_anchors(track, raw)

        assert resolved[0].timestamp == timestamp

    def test_multiple_photos_resolved_independently_and_in_order(self):
        track = Track(points=[
            point(lat=45.0, lon=7.0, distance_from_start=0.0),
            point(lat=45.001, lon=7.0, distance_from_start=500.0),
            point(lat=45.002, lon=7.0, distance_from_start=1000.0),
        ])
        raw = [
            RawPhotoAnchor(lat=45.0, lon=7.0, timestamp=datetime(2024, 1, 1, 8, 0, 0)),
            RawPhotoAnchor(lat=45.002, lon=7.0, timestamp=datetime(2024, 1, 1, 9, 0, 0)),
            RawPhotoAnchor(lat=45.02, lon=7.0, timestamp=datetime(2024, 1, 1, 10, 0, 0)),
        ]

        resolved = resolve_photo_anchors(track, raw)

        assert len(resolved) == 3
        assert [r.distance_from_start for r in resolved] == [0.0, 1000.0, 1000.0]
        assert [r.status for r in resolved] == ["ok", "ok", "too_far"]

    def test_out_and_back_route_matches_nearest_physical_pass(self):
        # Same physical spot visited outbound (distance 100) and on the way
        # back (distance 900) — the photo's own GPS fix should disambiguate
        # which pass it belongs to, same as nearest_point_candidates does for
        # manual map-click anchors.
        track = Track(points=[
            point(lat=45.0, lon=7.0, distance_from_start=0.0),
            point(lat=45.001, lon=7.0, distance_from_start=100.0),
            point(lat=45.002, lon=7.0, distance_from_start=500.0),
            point(lat=45.0011, lon=7.0, distance_from_start=900.0),
            point(lat=45.0, lon=7.0, distance_from_start=1000.0),
        ])
        raw = [RawPhotoAnchor(lat=45.0011, lon=7.0, timestamp=datetime(2024, 1, 1, 8, 30, 0))]

        resolved = resolve_photo_anchors(track, raw)

        assert resolved[0].distance_from_start == 900.0

    def test_empty_track_raises(self):
        raw = [RawPhotoAnchor(lat=45.0, lon=7.0, timestamp=datetime(2024, 1, 1, 8, 30, 0))]
        with pytest.raises(ValueError):
            resolve_photo_anchors(Track(points=[]), raw)

    def test_empty_raw_anchor_list_returns_empty_list(self):
        track = Track(points=[point(lat=45.0, lon=7.0, distance_from_start=0.0)])
        assert resolve_photo_anchors(track, []) == []


class TestResolvePhotoAnchorsActivityTimeWindow:
    def test_time_check_is_skipped_when_activity_window_not_given(self):
        # Default behavior (no activity_start/activity_end) is unchanged:
        # a photo from a wildly different date still resolves purely on GPS.
        track = Track(points=[point(lat=45.0, lon=7.0, distance_from_start=0.0)])
        raw = [RawPhotoAnchor(lat=45.0, lon=7.0, timestamp=datetime(1999, 1, 1))]

        resolved = resolve_photo_anchors(track, raw)

        assert resolved[0].status == "ok"

    def test_status_ok_when_timestamp_within_activity_window(self):
        track = Track(points=[point(lat=45.0, lon=7.0, distance_from_start=0.0)])
        start = datetime(2024, 6, 1, 8, 0, 0)
        end = datetime(2024, 6, 1, 10, 0, 0)
        raw = [RawPhotoAnchor(lat=45.0, lon=7.0, timestamp=datetime(2024, 6, 1, 9, 0, 0))]

        resolved = resolve_photo_anchors(track, raw, activity_start=start, activity_end=end)

        assert resolved[0].status == "ok"

    def test_status_outside_activity_time_when_timestamp_is_before_activity_start(self):
        # This is exactly what a stale "today" start-time field produces:
        # real photos from a past hike, dated long before the chosen start.
        track = Track(points=[point(lat=45.0, lon=7.0, distance_from_start=0.0)])
        start = datetime(2024, 6, 1, 8, 0, 0)
        end = datetime(2024, 6, 1, 10, 0, 0)
        raw = [RawPhotoAnchor(lat=45.0, lon=7.0, timestamp=datetime(2024, 1, 1, 9, 0, 0))]

        resolved = resolve_photo_anchors(track, raw, activity_start=start, activity_end=end)

        assert resolved[0].status == "outside_activity_time"

    def test_status_outside_activity_time_when_timestamp_is_after_activity_end(self):
        track = Track(points=[point(lat=45.0, lon=7.0, distance_from_start=0.0)])
        start = datetime(2024, 6, 1, 8, 0, 0)
        end = datetime(2024, 6, 1, 10, 0, 0)
        raw = [RawPhotoAnchor(lat=45.0, lon=7.0, timestamp=datetime(2024, 6, 2, 9, 0, 0))]

        resolved = resolve_photo_anchors(track, raw, activity_start=start, activity_end=end)

        assert resolved[0].status == "outside_activity_time"

    def test_outside_activity_time_takes_priority_over_a_good_geo_match(self):
        # A wrong date makes even an exact GPS match meaningless — this
        # matters because a coincidentally-close GPS match on a wrongly
        # dated photo is exactly the scenario that slipped through before.
        track = Track(points=[point(lat=45.0, lon=7.0, distance_from_start=0.0)])
        start = datetime(2024, 6, 1, 8, 0, 0)
        end = datetime(2024, 6, 1, 10, 0, 0)
        raw = [RawPhotoAnchor(lat=45.0, lon=7.0, timestamp=datetime(2024, 1, 1, 9, 0, 0))]

        resolved = resolve_photo_anchors(track, raw, activity_start=start, activity_end=end)

        assert resolved[0].gap_m == pytest.approx(0.0)
        assert resolved[0].status == "outside_activity_time"

    def test_tolerance_absorbs_small_clock_drift_just_past_the_boundary(self):
        track = Track(points=[point(lat=45.0, lon=7.0, distance_from_start=0.0)])
        start = datetime(2024, 6, 1, 8, 0, 0)
        end = datetime(2024, 6, 1, 10, 0, 0)
        just_before_start = start - ACTIVITY_TIME_TOLERANCE + timedelta(seconds=1)
        raw = [RawPhotoAnchor(lat=45.0, lon=7.0, timestamp=just_before_start)]

        resolved = resolve_photo_anchors(track, raw, activity_start=start, activity_end=end)

        assert resolved[0].status == "ok"

    def test_timestamp_beyond_tolerance_is_still_rejected(self):
        track = Track(points=[point(lat=45.0, lon=7.0, distance_from_start=0.0)])
        start = datetime(2024, 6, 1, 8, 0, 0)
        end = datetime(2024, 6, 1, 10, 0, 0)
        just_too_early = start - ACTIVITY_TIME_TOLERANCE - timedelta(seconds=1)
        raw = [RawPhotoAnchor(lat=45.0, lon=7.0, timestamp=just_too_early)]

        resolved = resolve_photo_anchors(track, raw, activity_start=start, activity_end=end)

        assert resolved[0].status == "outside_activity_time"
