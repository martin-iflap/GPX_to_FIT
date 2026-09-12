from datetime import timedelta

import pytest

from gpx2fit.core.models import RawStop, SportType, Track
from gpx2fit.core.pacing.combine import combine
from gpx2fit.core.pacing.stops import (
    ModeAStop,
    ResolvedStop,
    expand_multipliers_with_stops,
    expand_track_with_stops,
    resolve_stops,
)
from tests.conftest import START, anchor, point


class TestResolveStopsModeB:
    def test_explicit_start_end_resolves_directly(self):
        track = Track(points=[point(distance_from_start=0.0), point(distance_from_start=500.0), point(distance_from_start=1000.0)])
        arrival = START + timedelta(minutes=5)
        departure = arrival + timedelta(minutes=10)
        hard_anchors = [anchor(0.0, START), anchor(1000.0, START + timedelta(minutes=20))]
        raw = [RawStop(distance_from_start=500.0, start_timestamp=arrival, end_timestamp=departure)]

        mode_b, mode_a = resolve_stops(track, raw, hard_anchors)

        assert mode_b == [ResolvedStop(500.0, arrival, departure)]
        assert mode_a == []

    def test_end_not_later_than_start_raises(self):
        track = Track(points=[point(distance_from_start=0.0), point(distance_from_start=500.0), point(distance_from_start=1000.0)])
        hard_anchors = [anchor(0.0, START), anchor(1000.0, START + timedelta(minutes=20))]
        raw = [RawStop(distance_from_start=500.0, start_timestamp=START, end_timestamp=START)]

        with pytest.raises(ValueError):
            resolve_stops(track, raw, hard_anchors)


class TestResolveStopsModeA:
    def test_duration_only_resolves_to_a_pending_mode_a_stop(self):
        track = Track(points=[point(distance_from_start=0.0), point(distance_from_start=500.0), point(distance_from_start=1000.0)])
        hard_anchors = [anchor(0.0, START), anchor(1000.0, START + timedelta(minutes=20))]
        duration = timedelta(minutes=15)
        raw = [RawStop(distance_from_start=500.0, duration=duration)]

        mode_b, mode_a = resolve_stops(track, raw, hard_anchors)

        assert mode_b == []
        assert mode_a == [ModeAStop(500.0, duration)]

    def test_missing_lat_lon_and_distance_raises(self):
        track = Track(points=[point(distance_from_start=0.0), point(distance_from_start=500.0), point(distance_from_start=1000.0)])
        hard_anchors = [anchor(0.0, START), anchor(1000.0, START + timedelta(minutes=20))]
        raw = [RawStop(duration=timedelta(minutes=5))]

        with pytest.raises(ValueError):
            resolve_stops(track, raw, hard_anchors)


class TestResolveStopsValidation:
    def test_both_duration_and_start_end_given_raises(self):
        track = Track(points=[point(distance_from_start=0.0), point(distance_from_start=500.0), point(distance_from_start=1000.0)])
        hard_anchors = [anchor(0.0, START), anchor(1000.0, START + timedelta(minutes=20))]
        raw = [RawStop(
            distance_from_start=500.0,
            duration=timedelta(minutes=5),
            start_timestamp=START,
            end_timestamp=START + timedelta(minutes=5),
        )]

        with pytest.raises(ValueError):
            resolve_stops(track, raw, hard_anchors)

    def test_neither_duration_nor_start_end_given_raises(self):
        track = Track(points=[point(distance_from_start=0.0), point(distance_from_start=500.0), point(distance_from_start=1000.0)])
        hard_anchors = [anchor(0.0, START), anchor(1000.0, START + timedelta(minutes=20))]
        raw = [RawStop(distance_from_start=500.0)]

        with pytest.raises(ValueError):
            resolve_stops(track, raw, hard_anchors)

    def test_distance_coinciding_with_existing_anchor_raises(self):
        track = Track(points=[point(distance_from_start=0.0), point(distance_from_start=500.0), point(distance_from_start=1000.0)])
        hard_anchors = [anchor(0.0, START), anchor(500.0, START + timedelta(minutes=10)), anchor(1000.0, START + timedelta(minutes=20))]
        raw = [RawStop(distance_from_start=500.0, duration=timedelta(minutes=5))]

        with pytest.raises(ValueError):
            resolve_stops(track, raw, hard_anchors)

    @pytest.mark.parametrize(
        "first_kwargs, second_kwargs",
        [
            pytest.param(
                {"duration": timedelta(minutes=5)},
                {"duration": timedelta(minutes=8)},
                id="mode_a-mode_a",
            ),
            pytest.param(
                {"duration": timedelta(minutes=5)},
                {"start_timestamp": START, "end_timestamp": START + timedelta(minutes=5)},
                id="mode_a-mode_b",
            ),
            pytest.param(
                {"start_timestamp": START, "end_timestamp": START + timedelta(minutes=5)},
                {"start_timestamp": START, "end_timestamp": START + timedelta(minutes=7)},
                id="mode_b-mode_b",
            ),
        ],
    )
    def test_two_stops_at_the_same_distance_raises(self, first_kwargs, second_kwargs):
        track = Track(points=[point(distance_from_start=0.0), point(distance_from_start=500.0), point(distance_from_start=1000.0)])
        hard_anchors = [anchor(0.0, START), anchor(1000.0, START + timedelta(minutes=20))]
        raw = [
            RawStop(distance_from_start=500.0, **first_kwargs),
            RawStop(distance_from_start=500.0, **second_kwargs),
        ]

        with pytest.raises(ValueError):
            resolve_stops(track, raw, hard_anchors)


class TestExpandTrackWithStops:
    def test_point_count_increases_by_one_per_stop(self):
        points = [point(distance_from_start=0.0), point(distance_from_start=500.0), point(distance_from_start=1000.0)]
        stops: list[ResolvedStop | ModeAStop] = [ResolvedStop(500.0, START, START + timedelta(minutes=5))]

        result = expand_track_with_stops(points, stops)

        assert len(result) == len(points) + 1

    def test_duplicate_has_same_position_and_no_timestamp(self):
        points = [point(distance_from_start=0.0), point(distance_from_start=500.0), point(distance_from_start=1000.0)]
        stops: list[ResolvedStop | ModeAStop] = [ResolvedStop(500.0, START, START + timedelta(minutes=5))]

        result = expand_track_with_stops(points, stops)

        original = result[1]
        duplicate = result[2]
        assert duplicate.distance_from_start == original.distance_from_start
        assert duplicate.lat == original.lat
        assert duplicate.lon == original.lon
        assert duplicate.elevation == original.elevation
        assert duplicate.timestamp is None

    def test_multiple_stops_insert_in_ascending_order_without_corrupting_each_other(self):
        points = [
            point(distance_from_start=0.0), point(distance_from_start=300.0),
            point(distance_from_start=600.0), point(distance_from_start=1000.0),
        ]
        stops: list[ResolvedStop | ModeAStop] = [
            ResolvedStop(600.0, START, START + timedelta(minutes=5)),
            ResolvedStop(300.0, START, START + timedelta(minutes=5)),
        ]

        result = expand_track_with_stops(points, stops)

        assert [p.distance_from_start for p in result] == [0.0, 300.0, 300.0, 600.0, 600.0, 1000.0]

    def test_distance_matching_no_point_raises(self):
        points = [point(distance_from_start=0.0), point(distance_from_start=1000.0)]
        stops: list[ResolvedStop | ModeAStop] = [ResolvedStop(500.0, START, START + timedelta(minutes=5))]

        with pytest.raises(ValueError):
            expand_track_with_stops(points, stops)

    def test_mode_a_stop_duplicate_has_same_position_and_no_timestamp(self):
        points = [point(distance_from_start=0.0), point(distance_from_start=500.0), point(distance_from_start=1000.0)]
        stops: list[ResolvedStop | ModeAStop] = [ModeAStop(500.0, timedelta(minutes=5))]

        result = expand_track_with_stops(points, stops)

        original = result[1]
        duplicate = result[2]
        assert len(result) == len(points) + 1
        assert duplicate.distance_from_start == original.distance_from_start
        assert duplicate.timestamp is None

    def test_mixed_resolved_stop_and_mode_a_stop_insert_correctly(self):
        points = [
            point(distance_from_start=0.0), point(distance_from_start=300.0),
            point(distance_from_start=600.0), point(distance_from_start=1000.0),
        ]
        stops = [
            ResolvedStop(300.0, START, START + timedelta(minutes=5)),
            ModeAStop(600.0, timedelta(minutes=8)),
        ]

        result = expand_track_with_stops(points, stops)

        assert [p.distance_from_start for p in result] == [0.0, 300.0, 300.0, 600.0, 600.0, 1000.0]


class TestExpandMultipliersWithStops:
    def test_length_increases_by_one_per_stop_matching_expand_track_with_stops(self):
        points = [point(distance_from_start=0.0), point(distance_from_start=500.0), point(distance_from_start=1000.0)]
        multipliers = [1.0, 0.8]
        stops: list[ResolvedStop | ModeAStop] = [ResolvedStop(500.0, START, START + timedelta(minutes=5))]

        result = expand_multipliers_with_stops(points, multipliers, stops)

        assert len(result) == len(multipliers) + 1
        assert len(result) == len(expand_track_with_stops(points, stops)) - 1

        # Leg 0->1 (multiplier 1.0) is untouched; leg 1->2 (multiplier 0.8,
        # the one leaving the stopped-at point) is duplicated: once for the
        # new zero-distance arrival->departure leg, once for the real leg.
        assert result == [1.0, 0.8, 0.8]

    def test_multiple_stops_each_duplicate_their_own_leaving_leg(self):
        points = [
            point(distance_from_start=0.0), point(distance_from_start=300.0),
            point(distance_from_start=600.0), point(distance_from_start=1000.0),
        ]
        multipliers = [1.0, 0.9, 0.5]
        stops: list[ResolvedStop | ModeAStop] = [
            ResolvedStop(600.0, START, START + timedelta(minutes=5)),
            ResolvedStop(300.0, START, START + timedelta(minutes=5)),
        ]

        result = expand_multipliers_with_stops(points, multipliers, stops)

        assert result == [1.0, 0.9, 0.9, 0.5, 0.5]

    def test_no_stops_returns_multipliers_unchanged(self):
        points = [point(distance_from_start=0.0), point(distance_from_start=500.0), point(distance_from_start=1000.0)]
        multipliers = [1.0, 0.8]

        result = expand_multipliers_with_stops(points, multipliers, [])

        assert result == multipliers

    def test_mode_a_stop_duplicates_its_leaving_leg_too(self):
        points = [point(distance_from_start=0.0), point(distance_from_start=500.0), point(distance_from_start=1000.0)]
        multipliers = [1.0, 0.8]
        stops: list[ResolvedStop | ModeAStop] = [ModeAStop(500.0, timedelta(minutes=5))]

        result = expand_multipliers_with_stops(points, multipliers, stops)

        assert result == [1.0, 0.8, 0.8]


class TestStopsEndToEnd:
    def test_resolved_stops_expanded_and_combined_leave_every_point_timestamped(self):
        track = Track(points=[point(distance_from_start=d) for d in (0.0, 250.0, 500.0, 750.0, 1000.0)])
        end_time = START + timedelta(minutes=45)
        hard_anchors = [anchor(0.0, START), anchor(1000.0, end_time)]
        raw_stops = [
            RawStop(distance_from_start=250.0, duration=timedelta(minutes=10)),
            RawStop(
                distance_from_start=750.0,
                start_timestamp=START + timedelta(minutes=25),
                end_timestamp=START + timedelta(minutes=28),
            ),
        ]

        resolved_mode_b, mode_a_stops = resolve_stops(track, raw_stops, hard_anchors)
        expanded_points = expand_track_with_stops(track.points, [*resolved_mode_b, *mode_a_stops])

        stop_anchors = [
            a
            for rs in resolved_mode_b
            for a in (
                anchor(rs.distance_from_start, rs.arrival, source="stop_arrival"),
                anchor(rs.distance_from_start, rs.departure, source="stop_departure"),
            )
        ]
        all_anchors = sorted(hard_anchors + stop_anchors, key=lambda a: (a.distance_from_start, a.timestamp))

        working_track = Track(points=expanded_points)
        combine(working_track, all_anchors, SportType.RUNNING, mode_a_stops=mode_a_stops)

        assert all(p.timestamp is not None for p in working_track.points)
        assert working_track.points[0].timestamp == START
        assert working_track.points[-1].timestamp == end_time

    def test_a_stops_natural_departure_coinciding_with_the_next_anchor_still_paces_the_whole_route(self):
        # Under the old two-pass model (dry-run arrival, then a dedicated
        # zero-length segment for the stop's own arrival/departure anchor
        # pair), a stop whose departure happened to land exactly on the next
        # fixed anchor's time left everything after it unstamped. The new
        # single-pass model carves the stop's duration out of the segment's
        # active time up front, so this degenerate case now paces correctly
        # end-to-end instead.
        track = Track(points=[point(distance_from_start=d) for d in (0.0, 100.0, 200.0)])
        end_time = START + timedelta(minutes=10)
        hard_anchors = [anchor(0.0, START), anchor(200.0, end_time)]
        raw_stops = [RawStop(distance_from_start=100.0, duration=timedelta(minutes=5))]

        resolved_mode_b, mode_a_stops = resolve_stops(track, raw_stops, hard_anchors)
        expanded_points = expand_track_with_stops(track.points, [*resolved_mode_b, *mode_a_stops])

        working_track = Track(points=expanded_points)
        combine(working_track, hard_anchors, SportType.RUNNING, mode_a_stops=mode_a_stops)

        assert all(p.timestamp is not None for p in working_track.points)
        assert working_track.points[0].timestamp == START
        assert working_track.points[-1].timestamp == end_time
