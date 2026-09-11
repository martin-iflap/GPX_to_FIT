import pytest
from gpx2fit.core.models import Track
from gpx2fit.core.pacing.surface import (
    build_trace_attributes_payload,
    calculate_surface_multipliers,
    resolve_point_edge_indexes,
)
from tests.conftest import point


def _track(coords: list[tuple[float, float]]) -> Track:
    """Build a Track from (lat, lon) pairs, one per point."""
    return Track(points=[point(lat=lat, lon=lon) for lat, lon in coords])


def _matched(edge_index: int | None) -> dict:
    """A "matched" matched_points entry, or an "unmatched" one if edge_index is None."""
    if edge_index is None:
        return {"type": "unmatched"}
    return {"type": "matched", "edge_index": edge_index}


def _edge(surface: str | None = None, length: float | None = None) -> dict:
    edge: dict = {}
    if surface is not None:
        edge["surface"] = surface
    if length is not None:
        edge["length"] = length
    return edge


class TestBuildTraceAttributesPayload:
    def test_shape_has_one_entry_per_point_in_order(self):
        track = _track([(47.1, 8.1), (47.2, 8.2), (47.3, 8.3)])
        payload = build_trace_attributes_payload(track)
        assert payload["shape"] == [
            {"lat": 47.1, "lon": 8.1},
            {"lat": 47.2, "lon": 8.2},
            {"lat": 47.3, "lon": 8.3},
        ]

    def test_defaults_to_pedestrian_costing_and_map_snap(self):
        payload = build_trace_attributes_payload(_track([(0.0, 0.0), (1.0, 1.0)]))
        assert payload["costing"] == "pedestrian"
        assert payload["shape_match"] == "map_snap"

    def test_custom_costing_is_passed_through(self):
        payload = build_trace_attributes_payload(_track([(0.0, 0.0), (1.0, 1.0)]), costing="bicycle")
        assert payload["costing"] == "bicycle"

    def test_empty_track_raises_value_error(self):
        with pytest.raises(ValueError):
            build_trace_attributes_payload(Track(points=[]))


class TestResolvePointEdgeIndexes:
    def test_all_matched_returns_own_edge_indexes(self):
        response = {
            "edges": [_edge(), _edge(), _edge()],
            "matched_points": [_matched(0), _matched(2), _matched(1)],
        }
        assert resolve_point_edge_indexes(response, 3) == [0, 2, 1]

    def test_unmatched_points_resolve_to_the_nearer_matched_neighbor(self):
        response = {
            "edges": [_edge() for _ in range(6)],
            "matched_points": [_matched(0), _matched(None), _matched(None), _matched(5)],
        }
        # index 1 is closer to index 0 (distance 1) than index 3 (distance 2).
        # index 2 is closer to index 3 (distance 1) than index 0 (distance 2).
        assert resolve_point_edge_indexes(response, 4) == [0, 0, 5, 5]

    def test_equidistant_unmatched_point_resolves_to_the_left_neighbor(self):
        response = {
            "edges": [_edge(), _edge()],
            "matched_points": [_matched(0), _matched(None), _matched(1)],
        }
        assert resolve_point_edge_indexes(response, 3) == [0, 0, 1]

    def test_missing_edge_index_field_is_treated_as_unmatched(self):
        response = {"edges": [], "matched_points": [{"type": "matched"}]}
        assert resolve_point_edge_indexes(response, 1) == [None]

    def test_out_of_range_edge_index_is_treated_as_unmatched(self):
        response = {"edges": [_edge(), _edge()], "matched_points": [_matched(5)]}
        assert resolve_point_edge_indexes(response, 1) == [None]

    def test_no_points_matched_returns_all_none(self):
        response = {
            "edges": [_edge(), _edge()],
            "matched_points": [_matched(None), _matched(None)],
        }
        assert resolve_point_edge_indexes(response, 2) == [None, None]

    def test_matched_points_shorter_than_num_points_is_padded_and_fallback_filled(self):
        response = {"edges": [_edge(), _edge()], "matched_points": [_matched(0)]}
        assert resolve_point_edge_indexes(response, 3) == [0, 0, 0]


class TestCalculateSurfaceMultipliers:
    def test_track_with_fewer_than_two_points_has_no_legs(self):
        track = _track([(0.0, 0.0)])
        assert calculate_surface_multipliers(track, {}, {}) == []

    def test_returns_one_multiplier_per_leg(self):
        track = _track([(0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0)])
        response = {
            "edges": [_edge("paved", 1.0)],
            "matched_points": [_matched(0)] * 4,
        }
        assert len(calculate_surface_multipliers(track, response, {"paved": 1.0})) == 3

    def test_single_edge_per_leg_uses_that_edges_weight_exactly(self):
        track = _track([(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)])
        response = {
            "edges": [_edge("paved", 1.0)],
            "matched_points": [_matched(0)] * 3,
        }
        multipliers = calculate_surface_multipliers(track, response, {"paved": 1.3}, default_multiplier=1.0)
        assert multipliers == [pytest.approx(1.3), pytest.approx(1.3)]

    def test_multi_edge_leg_blends_via_length_weighted_average(self):
        track = _track([(0.0, 0.0), (1.0, 1.0)])
        response = {
            "edges": [_edge("a", 2.0), _edge("b", 1.0), _edge("c", 3.0)],
            "matched_points": [_matched(0), _matched(2)],
        }
        weights = {"a": 1.0, "b": 0.5, "c": 2.0}
        expected = (1.0 * 2.0 + 0.5 * 1.0 + 2.0 * 3.0) / (2.0 + 1.0 + 3.0)
        [multiplier] = calculate_surface_multipliers(track, response, weights)
        assert multiplier == pytest.approx(expected)

    def test_multi_edge_leg_with_zero_length_edges_falls_back_to_unweighted_average(self):
        track = _track([(0.0, 0.0), (1.0, 1.0)])
        response = {
            "edges": [_edge("a", 0.0), _edge("b")],
            "matched_points": [_matched(0), _matched(1)],
        }
        weights = {"a": 1.0, "b": 3.0}
        [multiplier] = calculate_surface_multipliers(track, response, weights)
        assert multiplier == pytest.approx(2.0)

    def test_unmatched_point_still_gets_a_sensible_multiplier_via_fallback(self):
        track = _track([(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)])
        response = {
            "edges": [_edge("a", 1.0)],
            "matched_points": [_matched(0), _matched(None), _matched(0)],
        }
        multipliers = calculate_surface_multipliers(track, response, {"a": 0.7}, default_multiplier=1.0)
        assert multipliers == [pytest.approx(0.7), pytest.approx(0.7)]

    def test_unknown_surface_name_falls_back_to_default_multiplier(self):
        track = _track([(0.0, 0.0), (1.0, 1.0)])
        response = {
            "edges": [_edge("mystery", 1.0)],
            "matched_points": [_matched(0), _matched(0)],
        }
        [multiplier] = calculate_surface_multipliers(track, response, {}, default_multiplier=0.8)
        assert multiplier == pytest.approx(0.8)

    def test_edge_missing_surface_key_falls_back_to_default_multiplier(self):
        track = _track([(0.0, 0.0), (1.0, 1.0)])
        response = {
            "edges": [_edge(length=1.0)],
            "matched_points": [_matched(0), _matched(0)],
        }
        [multiplier] = calculate_surface_multipliers(track, response, {"paved": 1.5}, default_multiplier=0.9)
        assert multiplier == pytest.approx(0.9)

    def test_fully_degenerate_response_gives_every_leg_the_default_multiplier(self):
        track = _track([(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)])
        response = {
            "edges": [_edge("a", 1.0)],
            "matched_points": [_matched(None), _matched(None), _matched(None)],
        }
        multipliers = calculate_surface_multipliers(track, response, {"a": 1.0}, default_multiplier=0.5)
        assert multipliers == [pytest.approx(0.5), pytest.approx(0.5)]

    def test_out_of_order_edge_indexes_raise_value_error(self):
        track = _track([(0.0, 0.0), (1.0, 1.0)])
        response = {
            "edges": [_edge("a", 1.0), _edge("b", 1.0)],
            "matched_points": [_matched(1), _matched(0)],
        }
        with pytest.raises(ValueError):
            calculate_surface_multipliers(track, response, {"a": 1.0, "b": 1.0})

    def test_custom_default_multiplier_is_honored_in_both_fallback_paths(self):
        track = _track([(0.0, 0.0), (1.0, 1.0)])
        unknown_surface_response = {
            "edges": [_edge("mystery", 1.0)],
            "matched_points": [_matched(0), _matched(0)],
        }
        unresolved_edge_response = {
            "edges": [_edge("a", 1.0)],
            "matched_points": [_matched(None), _matched(None)],
        }
        assert calculate_surface_multipliers(track, unknown_surface_response, {}, default_multiplier=2.0) == [
            pytest.approx(2.0)
        ]
        assert calculate_surface_multipliers(track, unresolved_edge_response, {"a": 1.0}, default_multiplier=2.0) == [
            pytest.approx(2.0)
        ]
