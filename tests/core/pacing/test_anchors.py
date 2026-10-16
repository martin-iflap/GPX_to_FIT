import math
import random
from datetime import datetime, timedelta

import pytest

from gpx2fit.core.models import Anchor, Candidate, InputError, RawAnchor, Track, TrackPoint
from gpx2fit.core.pacing.anchors import (
    distance_meters,
    add_start_end_anchors,
    build_user_anchors,
    nearest_point_candidates,
)
from tests.core.conftest import haversine_m, point

_M_PER_DEGREE_LAT = 6_371_000.0 * math.pi / 180


def _near_origin(north_m: float = 0.0, east_m: float = 0.0) -> tuple[float, float]:
    """The lat/lon this many metres north and east of (45.0, 7.0)."""
    return (
        45.0 + north_m / _M_PER_DEGREE_LAT,
        7.0 + east_m / (_M_PER_DEGREE_LAT * math.cos(math.radians(45.0))),
    )


def _out_and_back(return_offset_m: float) -> Track:
    """100 m east in 10 m legs, then back west `return_offset_m` north of the way out.

    The way back is at distances 200-300, so its points never share a cluster with the way out's.
    """
    way_out = [(_near_origin(east_m=10.0 * i), 10.0 * i) for i in range(11)]
    way_back = [(_near_origin(north_m=return_offset_m, east_m=100.0 - 10.0 * i), 200.0 + 10.0 * i) for i in range(11)]
    return Track(points=[point(lat=lat, lon=lon, distance_from_start=d) for (lat, lon), d in way_out + way_back])


def _reference_candidates(
    track: Track, lat: float, lon: float, proximity_radius_m: float = 25.0, cluster_gap_m: float = 50.0,
    max_candidates: int = 4,
) -> list[Candidate]:
    """nearest_point_candidates as first written, three plain scans: the oracle for the faster one."""
    nearest = min(track.points, key=lambda p: distance_meters(p.lat, p.lon, lat, lon))
    nearby = sorted(
        (p for p in track.points if distance_meters(p.lat, p.lon, nearest.lat, nearest.lon) <= proximity_radius_m),
        key=lambda p: p.distance_from_start,
    )
    clusters: list[list[TrackPoint]] = []
    for p in nearby:
        if clusters and p.distance_from_start - clusters[-1][-1].distance_from_start <= cluster_gap_m:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    representatives = sorted(
        (min(cluster, key=lambda p: distance_meters(p.lat, p.lon, lat, lon)) for cluster in clusters),
        key=lambda p: p.distance_from_start,
    )
    return [
        Candidate(distance_from_start=p.distance_from_start, lat=p.lat, lon=p.lon)
        for p in representatives[:max_candidates]
    ]


def _wandering_route(rng: random.Random, legs: int) -> Track:
    """A random walk in 2 m legs that comes back the same way, a few metres off, then loops past its middle."""
    heading, north, east = 0.0, 0.0, 0.0
    positions = [(north, east)]
    for _ in range(legs):
        heading += rng.gauss(0.0, 0.3)
        north, east = north + 2.0 * math.cos(heading), east + 2.0 * math.sin(heading)
        positions.append((north, east))
    positions += [(n + rng.uniform(-4, 4), e + rng.uniform(-4, 4)) for n, e in reversed(positions[:-1])]
    positions += positions[legs // 2: legs]
    return Track(points=[
        point(lat=lat, lon=lon, distance_from_start=2.0 * i)
        for i, (lat, lon) in enumerate(_near_origin(n, e) for n, e in positions)
    ])


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

    def test_a_pass_near_the_nearest_point_counts_though_farther_from_the_click(self):
        # The click is 20 m south of the way out, so the way back, 20 m north
        # of it, is 40 m from the click but 20 m from the nearest point.
        candidates = nearest_point_candidates(_out_and_back(return_offset_m=20.0), *_near_origin(-20.0, 50.0))
        assert [c["distance_from_start"] for c in candidates] == [50.0, 250.0]

    def test_a_pass_beyond_the_proximity_radius_of_the_nearest_point_does_not_count(self):
        candidates = nearest_point_candidates(_out_and_back(return_offset_m=30.0), *_near_origin(-20.0, 50.0))
        assert [c["distance_from_start"] for c in candidates] == [50.0]

    def test_each_pass_is_represented_by_its_point_closest_to_the_click(self):
        # Not by its first point: the way out's cluster runs 40-80 m, the way back's 230-250 m.
        candidates = nearest_point_candidates(_out_and_back(return_offset_m=20.0), *_near_origin(-20.0, 58.0))
        way_out_lat, way_out_lon = _near_origin(east_m=60.0)
        way_back_lat, way_back_lon = _near_origin(north_m=20.0, east_m=60.0)
        assert candidates == [
            Candidate(distance_from_start=60.0, lat=way_out_lat, lon=way_out_lon),
            Candidate(distance_from_start=240.0, lat=way_back_lat, lon=way_back_lon),
        ]

    def test_a_tie_within_a_pass_goes_to_the_earlier_point_on_the_route(self):
        track = Track(points=[
            point(lat=45.001, lon=7.0, distance_from_start=100.0),
            point(lat=45.001, lon=7.0, distance_from_start=120.0),
        ])
        assert [c["distance_from_start"] for c in nearest_point_candidates(track, 45.001, 7.0)] == [100.0]

    def test_a_tie_for_the_nearest_point_goes_to_the_earlier_point_on_the_route(self):
        # The click is exactly halfway between two points 38 m apart (exact
        # binary offsets, so the tie is exact). Only the later one has a
        # third point within proximity_radius_m, so the pick shows.
        step = 2.0 ** -12  # degrees of longitude, about 19 m at 45° N
        track = Track(points=[
            point(lat=45.0, lon=8.0 - step, distance_from_start=0.0),
            point(lat=45.0, lon=8.0 + step, distance_from_start=500.0),
            point(lat=45.0, lon=8.0 + 2 * step, distance_from_start=1000.0),
        ])
        assert [c["distance_from_start"] for c in nearest_point_candidates(track, 45.0, 8.0)] == [0.0]

    @pytest.mark.parametrize("seed", range(5))
    def test_matches_a_plain_scan_of_every_point(self, seed):
        rng = random.Random(seed)
        track = _wandering_route(rng, legs=1500)
        for _ in range(40):
            spot = rng.choice(track.points)
            north_m, east_m = rng.uniform(-120, 120), rng.uniform(-120, 120)
            lat = spot.lat + north_m / _M_PER_DEGREE_LAT
            lon = spot.lon + east_m / (_M_PER_DEGREE_LAT * math.cos(math.radians(spot.lat)))
            for radius in (5.0, 25.0, 60.0):
                assert nearest_point_candidates(track, lat, lon, proximity_radius_m=radius) == _reference_candidates(
                    track, lat, lon, proximity_radius_m=radius
                )


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
        track = Track(points=[point(distance_from_start=0.0), point(distance_from_start=1000.0)])
        with pytest.raises(ValueError, match="end_time or duration"):
            add_start_end_anchors(track, datetime(2024, 1, 1, 8, 0, 0))

    def test_empty_track_raises(self):
        with pytest.raises(ValueError):
            add_start_end_anchors(Track(points=[]), datetime(2024, 1, 1, 8, 0, 0), duration=timedelta(minutes=1))

    @pytest.mark.parametrize("distances", [(0.0,), (0.0, 0.0, 0.0)], ids=["single-point", "zero-length"])
    def test_route_without_length_raises_input_error(self, distances):
        # The end anchor would land on the start, so there is nothing to pace.
        track = Track(points=[point(distance_from_start=d) for d in distances])
        with pytest.raises(InputError, match="no length"):
            add_start_end_anchors(track, datetime(2024, 1, 1, 8, 0, 0), duration=timedelta(minutes=30))

    def test_boundary_anchors_are_labelled_start_and_end(self):
        # So an error about them can say "the start" rather than "anchor at 0.00 km".
        track = Track(points=[point(distance_from_start=0.0), point(distance_from_start=1000.0)])
        anchors = add_start_end_anchors(track, datetime(2024, 1, 1, 8, 0, 0), duration=timedelta(hours=1))
        assert [a.source for a in anchors] == ["start", "end"]


class TestBuildUserAnchors:
    def test_uses_distance_from_start_directly_when_given(self):
        track = Track(points=[point(distance_from_start=0.0), point(distance_from_start=1000.0)])
        raw = [RawAnchor(timestamp=datetime(2024, 1, 1, 8, 30, 0), distance_from_start=500.0)]

        anchors = build_user_anchors(track, raw)

        assert anchors[0].distance_from_start == 500.0

    def test_a_raw_anchor_needs_its_distance_along_the_route(self):
        with pytest.raises(TypeError):
            RawAnchor(timestamp=datetime(2024, 1, 1, 8, 30, 0))  # pyrefly: ignore

    def test_a_raw_anchor_cannot_be_placed_by_lat_lon(self):
        # A nearest-point lookup would quietly take the first pass of an
        # out-and-back: the GUI sends the distance of the pass the user picked.
        with pytest.raises(TypeError):
            RawAnchor(timestamp=datetime(2024, 1, 1, 8, 30, 0), lat=45.0, lon=7.0)  # pyrefly: ignore

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

    def test_anchor_on_the_start_says_so(self):
        track = Track(points=[point(distance_from_start=0.0), point(distance_from_start=1000.0)])
        boundary = add_start_end_anchors(track, datetime(2024, 1, 1, 8, 0, 0), duration=timedelta(hours=1))
        raw = [RawAnchor(timestamp=datetime(2024, 1, 1, 8, 30, 0), distance_from_start=0.0, source="photo")]

        with pytest.raises(InputError, match=r"route's start") as error:
            build_user_anchors(track, raw, existing_anchors=boundary)
        assert "Two anchors" not in str(error.value)

    def test_anchor_on_the_finish_says_so(self):
        track = Track(points=[point(distance_from_start=0.0), point(distance_from_start=1000.0)])
        boundary = add_start_end_anchors(track, datetime(2024, 1, 1, 8, 0, 0), duration=timedelta(hours=1))
        raw = [RawAnchor(timestamp=datetime(2024, 1, 1, 8, 30, 0), distance_from_start=1000.0)]

        with pytest.raises(InputError, match=r"route's finish"):
            build_user_anchors(track, raw, existing_anchors=boundary)

    def test_two_mid_route_anchors_on_one_point_keep_the_generic_message(self):
        track = Track(points=[point(distance_from_start=0.0), point(distance_from_start=1000.0)])
        boundary = add_start_end_anchors(track, datetime(2024, 1, 1, 8, 0, 0), duration=timedelta(hours=1))
        raw = [
            RawAnchor(timestamp=datetime(2024, 1, 1, 8, 20, 0), distance_from_start=500.0),
            RawAnchor(timestamp=datetime(2024, 1, 1, 8, 30, 0), distance_from_start=500.0),
        ]

        with pytest.raises(InputError, match=r"Two anchors land on the exact same point"):
            build_user_anchors(track, raw, existing_anchors=boundary)
