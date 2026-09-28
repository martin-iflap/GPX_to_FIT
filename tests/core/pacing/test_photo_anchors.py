from datetime import datetime, timedelta

import pytest

from gpx2fit.core.models import InputError, RawAnchor, RawPhotoAnchor, SportType, Track
from gpx2fit.core.pacing.anchors import add_start_end_anchors, build_user_anchors, distance_meters
from gpx2fit.core.pacing.combine import combine
from gpx2fit.core.pacing.photo_anchors import MAX_MATCH_DISTANCE_M, resolve_photo_anchors
from tests.core.conftest import point


def _track_through(lat: float, lon: float) -> Track:
    """A three-point track whose middle point is (lat, lon).

    A photo matching the route's first or last point is "at_route_end", not
    "ok", since the start/end anchors already sit there. Tests about the gap
    or the time window match this interior point instead; the neighbors are
    about 790 m off to either side (0.01 deg of longitude), far from anything
    the tests place.
    """
    return Track(points=[
        point(lat=lat, lon=lon - 0.01, distance_from_start=0.0),
        point(lat=lat, lon=lon, distance_from_start=790.0),
        point(lat=lat, lon=lon + 0.01, distance_from_start=1580.0),
    ])


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
        track = Track(points=[
            point(lat=45.0, lon=7.0, distance_from_start=0.0),
            point(lat=45.001, lon=7.0, distance_from_start=500.0),
            point(lat=45.002, lon=7.0, distance_from_start=1000.0),
        ])
        raw = [RawPhotoAnchor(lat=45.001, lon=7.0, timestamp=datetime(2024, 1, 1, 8, 30, 0))]

        resolved = resolve_photo_anchors(track, raw)

        assert resolved[0].gap_m == pytest.approx(0.0)
        assert resolved[0].status == "ok"

    def test_status_ok_when_gap_within_default_threshold(self):
        track = _track_through(45.0, 7.0)
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
        track = _track_through(45.0, 7.0)
        raw = [RawPhotoAnchor(lat=45.02, lon=7.0, timestamp=datetime(2024, 1, 1, 8, 30, 0))]

        assert resolve_photo_anchors(track, raw, max_match_distance_m=10.0)[0].status == "too_far"
        assert resolve_photo_anchors(track, raw, max_match_distance_m=10_000.0)[0].status == "ok"

    def test_gap_exactly_at_threshold_is_ok(self):
        track = _track_through(45.0, 7.0)
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
        # The first two sit exactly on the route's start and finish; the third
        # is too far from anywhere, which is reported ahead of where it lands.
        assert [r.status for r in resolved] == ["at_route_end", "at_route_end", "too_far"]

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
        track = _track_through(45.0, 7.0)
        raw = [RawPhotoAnchor(lat=45.0, lon=7.0, timestamp=datetime(1999, 1, 1))]

        resolved = resolve_photo_anchors(track, raw)

        assert resolved[0].status == "ok"

    def test_status_ok_when_timestamp_within_activity_window(self):
        track = _track_through(45.0, 7.0)
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

    @pytest.mark.parametrize("offset", [
        timedelta(minutes=-10),  # coffee at the trailhead before setting off
        timedelta(0),            # exactly on the start
        timedelta(hours=2),      # exactly on the end
        timedelta(hours=2, minutes=10),  # the restaurant after the finish
    ])
    def test_window_is_exclusive_and_unpadded(self, offset):
        track = Track(points=[point(lat=45.0, lon=7.0, distance_from_start=0.0)])
        start = datetime(2024, 6, 1, 8, 0, 0)
        end = start + timedelta(hours=2)
        raw = [RawPhotoAnchor(lat=45.0, lon=7.0, timestamp=start + offset)]

        resolved = resolve_photo_anchors(track, raw, activity_start=start, activity_end=end)

        assert resolved[0].status == "outside_activity_time"

    @pytest.mark.parametrize("offset", [
        timedelta(seconds=1), timedelta(minutes=30), timedelta(minutes=60),
        timedelta(minutes=90), timedelta(hours=2) - timedelta(seconds=1),
    ])
    def test_every_accepted_photo_survives_conversion(self, offset):
        # The contract that matters: "ok" must mean the anchor is usable. A photo
        # accepted here but outside the start/end anchors' times used to block
        # the whole conversion in combine() instead of being ignored on its own.
        # Along the route in step with its time, so only the window is on test.
        track = Track(points=[
            point(lat=45.0 + i * 0.00045, lon=7.0, distance_from_start=i * 50.0) for i in range(101)
        ])
        start = datetime(2024, 6, 1, 8, 0, 0)
        end = start + timedelta(hours=2)
        share = offset / (end - start)
        index = min(99, max(1, round(share * 100)))
        raw = [RawPhotoAnchor(lat=45.0 + index * 0.00045, lon=7.0, timestamp=start + offset)]

        [resolved] = resolve_photo_anchors(track, raw, activity_start=start, activity_end=end)
        assert resolved.status == "ok"

        boundary = add_start_end_anchors(track, start, end_time=end)
        photo = build_user_anchors(
            track,
            [RawAnchor(distance_from_start=resolved.distance_from_start, timestamp=resolved.timestamp, source="photo")],
            existing_anchors=boundary,
        )
        combine(track, sorted(boundary + photo, key=lambda a: a.distance_from_start), SportType.HIKING)


class TestResolvePhotoAnchorsRouteEnds:
    """A photo on the route's first or last point can't become an anchor.

    The start and end anchors already sit on those points, and
    build_user_anchors rejects a second anchor at the same distance. So the
    photo is reported per-photo as "at_route_end" rather than accepted as "ok"
    and then failing the whole conversion.
    """

    START = datetime(2024, 6, 1, 8, 0, 0)
    END = datetime(2024, 6, 1, 10, 0, 0)
    MID = datetime(2024, 6, 1, 9, 0, 0)

    @staticmethod
    def _line() -> Track:
        return Track(points=[
            point(lat=45.0 + i * 0.001, lon=7.0, distance_from_start=i * 111.0) for i in range(5)
        ])

    @staticmethod
    def _loop() -> Track:
        # A square that ends where it started: points 0 and 8 share coordinates.
        corners = [(45.0, 7.0), (45.001, 7.0), (45.002, 7.0), (45.002, 7.001), (45.002, 7.002),
                   (45.001, 7.002), (45.0, 7.002), (45.0, 7.001), (45.0, 7.0)]
        return Track(points=[
            point(lat=lat, lon=lon, distance_from_start=i * 100.0) for i, (lat, lon) in enumerate(corners)
        ])

    def _resolve(self, track: Track, lat: float, lon: float, timestamp: datetime | None = None):
        raw = [RawPhotoAnchor(lat=lat, lon=lon, timestamp=timestamp or self.MID)]
        [resolved] = resolve_photo_anchors(track, raw, activity_start=self.START, activity_end=self.END)
        return resolved

    def test_photo_on_the_first_point_is_at_route_end(self):
        assert self._resolve(self._line(), 45.0, 7.0).status == "at_route_end"

    def test_photo_on_the_last_point_is_at_route_end(self):
        assert self._resolve(self._line(), 45.004, 7.0).status == "at_route_end"

    def test_photo_near_but_not_on_an_end_still_snaps_to_it(self):
        # ~20 m short of the start, off the route: nearest is still point 0.
        resolved = self._resolve(self._line(), 44.99982, 7.0)
        assert resolved.distance_from_start == 0.0
        assert resolved.status == "at_route_end"

    def test_the_points_next_to_the_ends_are_ok(self):
        assert self._resolve(self._line(), 45.001, 7.0).status == "ok"
        assert self._resolve(self._line(), 45.003, 7.0).status == "ok"

    def test_photo_at_a_loops_start_and_finish_is_at_route_end(self):
        # The common case: a loop's trailhead is both the first and last point.
        resolved = self._resolve(self._loop(), 45.0, 7.0)
        assert resolved.distance_from_start in (0.0, 800.0)
        assert resolved.status == "at_route_end"

    def test_outside_activity_time_is_reported_before_at_route_end(self):
        resolved = self._resolve(self._line(), 45.0, 7.0, timestamp=datetime(2024, 1, 1, 9, 0, 0))
        assert resolved.status == "outside_activity_time"

    def test_too_far_is_reported_before_at_route_end(self):
        # ~1.1 km beyond the start: nearest point is the start, but the photo
        # has nothing to do with it.
        resolved = self._resolve(self._line(), 44.99, 7.0)
        assert resolved.distance_from_start == 0.0
        assert resolved.status == "too_far"

    @pytest.mark.parametrize("index", range(9))
    def test_status_predicts_whether_the_photo_can_be_used(self, index):
        # The contract across every point of a loop: "ok" converts, and
        # "at_route_end" is exactly the case build_user_anchors would reject.
        track = self._loop()
        target = track.points[index]
        timestamp = self.START + (self.END - self.START) * (target.distance_from_start / track.total_distance)
        timestamp = min(max(timestamp, self.START + timedelta(seconds=1)), self.END - timedelta(seconds=1))
        resolved = self._resolve(track, target.lat, target.lon, timestamp=timestamp)

        boundary = add_start_end_anchors(track, self.START, end_time=self.END)
        raw_anchor = RawAnchor(distance_from_start=resolved.distance_from_start, timestamp=resolved.timestamp, source="photo")

        if resolved.status == "ok":
            photo = build_user_anchors(track, [raw_anchor], existing_anchors=boundary)
            combine(track, sorted(boundary + photo, key=lambda a: a.distance_from_start), SportType.HIKING)
        else:
            assert resolved.status == "at_route_end"
            with pytest.raises(InputError):
                build_user_anchors(track, [raw_anchor], existing_anchors=boundary)
