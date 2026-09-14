from datetime import datetime, timedelta

import pytest

from gpx2fit.core.models import Anchor, InputError, RawAnchor, Track
from gpx2fit.core.pacing.anchors import (
    distance_meters,
    add_start_end_anchors,
    build_user_anchors,
    nearest_point_candidates,
    nearest_point_distance_from_start,
)
from tests.core.conftest import haversine_m, point


class TestDistanceMeters:
    def test_same_point_is_zero(self):
        assert distance_meters(45.0, 7.0, 45.0, 7.0) == pytest.approx(0.0)

    def test_matches_reference_haversine_formula(self):
        # Turin to Milan, roughly.
        lat_a, lon_a = 45.0703, 7.6869
        lat_b, lon_b = 45.4642, 9.1900
        assert distance_meters(lat_a, lon_a, lat_b, lon_b) == pytest.approx(
            haversine_m(lat_a, lon_a, lat_b, lon_b)
        )

    def test_one_degree_of_latitude_is_about_111km(self):
        assert distance_meters(0.0, 0.0, 1.0, 0.0) == pytest.approx(111_195, rel=0.01)


class TestNearestPointDistanceFromStart:
    def test_returns_distance_of_closest_point(self):
        track = Track(points=[
            point(lat=45.0, lon=7.0, distance_from_start=0.0),
            point(lat=45.001, lon=7.0, distance_from_start=100.0),
            point(lat=45.01, lon=7.0, distance_from_start=1000.0),
        ])
        assert nearest_point_distance_from_start(track, 45.0011, 7.0) == 100.0

    def test_empty_track_raises(self):
        with pytest.raises(ValueError):
            nearest_point_distance_from_start(Track(points=[]), 45.0, 7.0)

    def test_tie_breaks_to_first_point_in_track_order(self):
        track = Track(points=[
            point(lat=45.0, lon=7.0, distance_from_start=0.0),
            point(lat=45.0, lon=7.0, distance_from_start=500.0),
        ])
        assert nearest_point_distance_from_start(track, 45.0, 7.0) == 0.0


class TestNearestPointCandidates:
    def test_single_pass_route_yields_one_candidate(self):
        track = Track(points=[
            point(lat=45.0, lon=7.0, distance_from_start=0.0),
            point(lat=45.001, lon=7.0, distance_from_start=100.0),
            point(lat=45.002, lon=7.0, distance_from_start=200.0),
        ])
        candidates = nearest_point_candidates(track, 45.001, 7.0)
        assert len(candidates) == 1
        assert candidates[0]["distance_from_start"] == 100.0

    def test_out_and_back_route_yields_two_clusters(self):
        # Same physical spot visited at distance 100 (outbound) and 900 (return).
        track = Track(points=[
            point(lat=45.0, lon=7.0, distance_from_start=0.0),
            point(lat=45.001, lon=7.0, distance_from_start=100.0),
            point(lat=45.002, lon=7.0, distance_from_start=500.0),
            point(lat=45.001, lon=7.0, distance_from_start=900.0),
            point(lat=45.0, lon=7.0, distance_from_start=1000.0),
        ])
        candidates = nearest_point_candidates(track, 45.001, 7.0)
        assert [c["distance_from_start"] for c in candidates] == [100.0, 900.0]

    def test_points_within_cluster_gap_merge_into_one_cluster(self):
        track = Track(points=[
            point(lat=45.001, lon=7.0, distance_from_start=100.0),
            point(lat=45.001, lon=7.0, distance_from_start=130.0),  # 30m gap, well under default 50m cluster_gap_m
        ])
        candidates = nearest_point_candidates(track, 45.001, 7.0)
        assert len(candidates) == 1

    def test_points_beyond_cluster_gap_split_into_two_clusters(self):
        track = Track(points=[
            point(lat=45.001, lon=7.0, distance_from_start=100.0),
            point(lat=45.001, lon=7.0, distance_from_start=200.0),  # 100m gap, over the 50m cluster_gap_m
        ])
        candidates = nearest_point_candidates(track, 45.001, 7.0)
        assert len(candidates) == 2

    def test_max_candidates_truncates_results(self):
        # Four separate passes through the same spot, far enough apart to cluster separately.
        track = Track(points=[point(lat=45.001, lon=7.0, distance_from_start=d) for d in (0.0, 200.0, 400.0, 600.0)])
        candidates = nearest_point_candidates(track, 45.001, 7.0, max_candidates=2)
        assert len(candidates) == 2
        assert [c["distance_from_start"] for c in candidates] == [0.0, 200.0]

    def test_empty_track_raises(self):
        with pytest.raises(ValueError):
            nearest_point_candidates(Track(points=[]), 45.0, 7.0)


class TestAddStartEndAnchors:
    def test_uses_explicit_end_time(self):
        start = datetime(2024, 1, 1, 8, 0, 0)
        end = datetime(2024, 1, 1, 9, 0, 0)
        track = Track(points=[point(distance_from_start=0.0), point(distance_from_start=1000.0)])

        anchors = add_start_end_anchors(track, start, end_time=end)

        assert anchors[0].distance_from_start == 0.0
        assert anchors[0].timestamp == start
        assert anchors[1].distance_from_start == 1000.0
        assert anchors[1].timestamp == end

    def test_derives_end_time_from_duration(self):
        start = datetime(2024, 1, 1, 8, 0, 0)
        track = Track(points=[point(distance_from_start=0.0), point(distance_from_start=1000.0)])

        anchors = add_start_end_anchors(track, start, duration=timedelta(minutes=30))

        assert anchors[1].timestamp == start + timedelta(minutes=30)

    def test_end_time_takes_priority_over_duration(self):
        start = datetime(2024, 1, 1, 8, 0, 0)
        end = datetime(2024, 1, 1, 9, 0, 0)
        track = Track(points=[point(distance_from_start=0.0), point(distance_from_start=1000.0)])

        anchors = add_start_end_anchors(track, start, end_time=end, duration=timedelta(minutes=30))

        assert anchors[1].timestamp == end

    def test_neither_end_time_nor_duration_raises(self):
        track = Track(points=[point(distance_from_start=0.0)])
        with pytest.raises(ValueError):
            add_start_end_anchors(track, datetime(2024, 1, 1, 8, 0, 0))

    def test_empty_track_raises(self):
        with pytest.raises(ValueError):
            add_start_end_anchors(Track(points=[]), datetime(2024, 1, 1, 8, 0, 0), duration=timedelta(minutes=1))


class TestBuildUserAnchors:
    def test_uses_distance_from_start_directly_when_given(self):
        track = Track(points=[point(distance_from_start=0.0), point(distance_from_start=1000.0)])
        raw = [RawAnchor(timestamp=datetime(2024, 1, 1, 8, 30, 0), distance_from_start=500.0)]

        anchors = build_user_anchors(track, raw)

        assert anchors[0].distance_from_start == 500.0

    def test_resolves_lat_lon_to_nearest_point_distance(self):
        track = Track(points=[
            point(lat=45.0, lon=7.0, distance_from_start=0.0),
            point(lat=45.001, lon=7.0, distance_from_start=500.0),
        ])
        raw = [RawAnchor(timestamp=datetime(2024, 1, 1, 8, 30, 0), lat=45.001, lon=7.0)]

        anchors = build_user_anchors(track, raw)

        assert anchors[0].distance_from_start == 500.0

    def test_neither_distance_nor_lat_lon_raises(self):
        track = Track(points=[point(distance_from_start=0.0), point(distance_from_start=1000.0)])
        raw = [RawAnchor(timestamp=datetime(2024, 1, 1, 8, 30, 0))]
        with pytest.raises(ValueError):
            build_user_anchors(track, raw)

    def test_distance_beyond_track_end_raises(self):
        track = Track(points=[point(distance_from_start=0.0), point(distance_from_start=1000.0)])
        raw = [RawAnchor(timestamp=datetime(2024, 1, 1, 8, 30, 0), distance_from_start=1500.0)]
        with pytest.raises(ValueError):
            build_user_anchors(track, raw)

    def test_negative_distance_raises(self):
        track = Track(points=[point(distance_from_start=0.0), point(distance_from_start=1000.0)])
        raw = [RawAnchor(timestamp=datetime(2024, 1, 1, 8, 30, 0), distance_from_start=-1.0)]
        with pytest.raises(ValueError):
            build_user_anchors(track, raw)

    def test_sorts_by_distance_from_start(self):
        track = Track(points=[point(distance_from_start=0.0), point(distance_from_start=1000.0)])
        raw = [
            RawAnchor(timestamp=datetime(2024, 1, 1, 9, 0, 0), distance_from_start=800.0),
            RawAnchor(timestamp=datetime(2024, 1, 1, 8, 0, 0), distance_from_start=200.0),
        ]

        anchors = build_user_anchors(track, raw)

        assert [a.distance_from_start for a in anchors] == [200.0, 800.0]

    def test_preserves_source_from_raw_anchor(self):
        track = Track(points=[point(distance_from_start=0.0), point(distance_from_start=1000.0)])
        raw = [RawAnchor(timestamp=datetime(2024, 1, 1, 8, 30, 0), distance_from_start=500.0, source="photo")]

        anchors = build_user_anchors(track, raw)

        assert anchors[0].source == "photo"

    def test_two_raw_anchors_at_the_same_distance_raises(self):
        # Two clicks close enough together to snap to the same point would
        # otherwise leave pacing.combine unable to tell which anchor's
        # timestamp belongs to that point.
        track = Track(points=[point(distance_from_start=0.0), point(distance_from_start=1000.0)])
        raw = [
            RawAnchor(timestamp=datetime(2024, 1, 1, 8, 30, 0), distance_from_start=500.0),
            RawAnchor(timestamp=datetime(2024, 1, 1, 9, 0, 0), distance_from_start=500.0),
        ]
        with pytest.raises(InputError):
            build_user_anchors(track, raw)

    def test_raw_anchor_coinciding_with_an_existing_anchor_raises(self):
        track = Track(points=[point(distance_from_start=0.0), point(distance_from_start=1000.0)])
        raw = [RawAnchor(timestamp=datetime(2024, 1, 1, 8, 30, 0), distance_from_start=0.0)]
        existing = [Anchor(distance_from_start=0.0, timestamp=datetime(2024, 1, 1, 8, 0, 0), source="user")]

        with pytest.raises(InputError):
            build_user_anchors(track, raw, existing_anchors=existing)
