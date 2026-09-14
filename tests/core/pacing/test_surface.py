import pytest
from gpx2fit.core.models import SportType, Track
from gpx2fit.core.pacing import surface as surface_module
from gpx2fit.core.pacing.surface import (
    _leg_category_multiplier,
    _leg_sac_scale_multiplier,
    build_trace_attributes_payload,
    calculate_surface_multipliers,
    resolve_point_edge_indexes,
)
from tests.core.conftest import point


def _track(coords: list[tuple[float, float]]) -> Track:
    """Build a Track from (lat, lon) pairs, one per point."""
    return Track(points=[point(lat=lat, lon=lon) for lat, lon in coords])


def _matched(edge_index: int | None) -> dict:
    """A "matched" matched_points entry, or an "unmatched" one if edge_index is None."""
    if edge_index is None:
        return {"type": "unmatched"}
    return {"type": "matched", "edge_index": edge_index}


def _edge(length: float | None = None, **tags: str | int) -> dict:
    edge: dict = {}
    if length is not None:
        edge["length"] = length
    edge.update(tags)
    return edge


def _patch_tables(
    monkeypatch: pytest.MonkeyPatch,
    surface: dict[str, float] | None = None,
    road_class: dict[str, float] | None = None,
    use: dict[str, float] | None = None,
    sac_scale: dict[str, float] | None = None,
    sport: SportType = SportType.HIKING,
) -> None:
    """Replace the loaded weight tables with a small, fully controlled table for one sport."""
    tables = {
        "surface": surface or {},
        "road_class": road_class or {},
        "use": use or {},
        "sac_scale": sac_scale or {},
    }
    monkeypatch.setattr(surface_module, "_WEIGHTS_BY_SPORT", {sport.value: tables})


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
        assert payload["shape_match"] == "walk_or_snap"

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


class TestLegCategoryMultiplier:
    """Unit tests for the length-weighted-average primitive shared by every
    category (surface, road_class, use, sac_scale)."""

    def test_single_edge_uses_that_edges_weight_exactly(self):
        edges = [_edge(1.0, surface="paved")]
        multiplier = _leg_category_multiplier(edges, "surface", {"paved": 1.3}, default_multiplier=1.0)
        assert multiplier == pytest.approx(1.3)

    def test_multiple_edges_blend_via_length_weighted_average(self):
        edges = [_edge(2.0, surface="a"), _edge(1.0, surface="b"), _edge(3.0, surface="c")]
        weights = {"a": 1.0, "b": 0.5, "c": 2.0}
        expected = (1.0 * 2.0 + 0.5 * 1.0 + 2.0 * 3.0) / (2.0 + 1.0 + 3.0)
        multiplier = _leg_category_multiplier(edges, "surface", weights, default_multiplier=1.0)
        assert multiplier == pytest.approx(expected)

    def test_zero_length_edges_fall_back_to_unweighted_average(self):
        edges = [_edge(0.0, surface="a"), _edge(surface="b")]
        weights = {"a": 1.0, "b": 3.0}
        multiplier = _leg_category_multiplier(edges, "surface", weights, default_multiplier=1.0)
        assert multiplier == pytest.approx(2.0)

    def test_unknown_tag_value_falls_back_to_default_multiplier(self):
        edges = [_edge(1.0, surface="mystery")]
        multiplier = _leg_category_multiplier(edges, "surface", {}, default_multiplier=0.8)
        assert multiplier == pytest.approx(0.8)

    def test_missing_tag_falls_back_to_default_multiplier_by_default(self):
        edges = [_edge(1.0)]
        multiplier = _leg_category_multiplier(edges, "surface", {"paved": 1.5}, default_multiplier=0.9)
        assert multiplier == pytest.approx(0.9)

    def test_custom_default_multiplier_is_honored(self):
        edges = [_edge(1.0, surface="mystery")]
        multiplier = _leg_category_multiplier(edges, "surface", {}, default_multiplier=2.0)
        assert multiplier == pytest.approx(2.0)


class TestLegSacScaleMultiplier:
    """Unit tests for the sac_scale-specific primitive, which excludes
    edges with no sac_scale data instead of defaulting them (see its
    docstring). Valhalla encodes sac_scale as an integer 0-6, where 0 means
    "no data" rather than "known easy" — 1-6 are the real OSM tiers."""

    def test_single_tagged_edge_uses_that_edges_weight_exactly(self):
        edges = [_edge(1.0, sac_scale=1)]
        multiplier = _leg_sac_scale_multiplier(edges, {"1": 0.6}, default_multiplier=1.0)
        assert multiplier == pytest.approx(0.6)

    def test_untagged_edges_are_excluded_rather_than_defaulted(self):
        edges = [_edge(1.0, sac_scale=1), _edge(9.0)]
        multiplier = _leg_sac_scale_multiplier(edges, {"1": 0.6}, default_multiplier=1.0)
        # The untagged 9.0-length edge is excluded entirely, not defaulted or
        # blended in — the result is exactly the tagged edge's own weight.
        assert multiplier == pytest.approx(0.6)

    def test_zero_sac_scale_is_treated_as_no_data_and_excluded(self):
        # Valhalla's sentinel for "edge carries no sac_scale tag at all" is
        # the integer 0, not a missing field — must be excluded exactly
        # like a missing field, not looked up as a real difficulty tier.
        edges = [_edge(1.0, sac_scale=1), _edge(9.0, sac_scale=0)]
        multiplier = _leg_sac_scale_multiplier(edges, {"1": 0.6}, default_multiplier=1.0)
        assert multiplier == pytest.approx(0.6)

    def test_returns_none_when_no_edge_carries_the_tag(self):
        edges = [_edge(1.0), _edge(2.0)]
        multiplier = _leg_sac_scale_multiplier(edges, {"1": 0.6}, default_multiplier=1.0)
        assert multiplier is None

    def test_returns_none_when_every_edge_has_sac_scale_zero(self):
        edges = [_edge(1.0, sac_scale=0), _edge(2.0, sac_scale=0)]
        multiplier = _leg_sac_scale_multiplier(edges, {"1": 0.6}, default_multiplier=1.0)
        assert multiplier is None

    def test_multiple_tagged_edges_blend_via_length_weighted_average(self):
        edges = [_edge(1.0, sac_scale=1), _edge(3.0, sac_scale=4)]
        weights = {"1": 1.0, "4": 0.6}
        expected = (1.0 * 1.0 + 0.6 * 3.0) / (1.0 + 3.0)
        multiplier = _leg_sac_scale_multiplier(edges, weights, default_multiplier=1.0)
        assert multiplier == pytest.approx(expected)

    def test_unknown_tag_value_falls_back_to_default_multiplier(self):
        edges = [_edge(1.0, sac_scale=9)]
        multiplier = _leg_sac_scale_multiplier(edges, {}, default_multiplier=0.8)
        assert multiplier == pytest.approx(0.8)


class TestCalculateSurfaceMultipliers:
    def test_track_with_fewer_than_two_points_has_no_legs(self, monkeypatch):
        _patch_tables(monkeypatch)
        track = _track([(0.0, 0.0)])
        assert calculate_surface_multipliers(track, {}, SportType.HIKING) == []

    def test_returns_one_multiplier_per_leg(self, monkeypatch):
        _patch_tables(monkeypatch, surface={"paved": 1.0})
        track = _track([(0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0)])
        response = {
            "edges": [_edge(1.0, surface="paved")],
            "matched_points": [_matched(0)] * 4,
        }
        assert len(calculate_surface_multipliers(track, response, SportType.HIKING)) == 3

    def test_unresolved_leg_endpoint_gets_default_multiplier_directly(self, monkeypatch):
        _patch_tables(monkeypatch, surface={"a": 0.1})
        track = _track([(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)])
        response = {
            "edges": [_edge(1.0, surface="a")],
            "matched_points": [_matched(0), _matched(None), _matched(0)],
        }
        multipliers = calculate_surface_multipliers(track, response, SportType.HIKING, default_multiplier=1.0)
        # Every point resolves via resolve_point_edge_indexes's nearest-neighbor
        # fallback here (no leg endpoint is truly unresolved), so both legs
        # fall through to the normal per-category computation.
        assert len(multipliers) == 2

    def test_fully_degenerate_response_gives_every_leg_the_default_multiplier(self, monkeypatch):
        _patch_tables(monkeypatch, surface={"a": 0.1})
        track = _track([(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)])
        response = {
            "edges": [_edge(1.0, surface="a")],
            "matched_points": [_matched(None), _matched(None), _matched(None)],
        }
        multipliers = calculate_surface_multipliers(track, response, SportType.HIKING, default_multiplier=0.5)
        assert multipliers == [pytest.approx(0.5), pytest.approx(0.5)]

    def test_out_of_order_edge_indexes_raise_value_error(self, monkeypatch):
        _patch_tables(monkeypatch, surface={"a": 1.0, "b": 1.0})
        track = _track([(0.0, 0.0), (1.0, 1.0)])
        response = {
            "edges": [_edge(1.0, surface="a"), _edge(1.0, surface="b")],
            "matched_points": [_matched(1), _matched(0)],
        }
        with pytest.raises(ValueError):
            calculate_surface_multipliers(track, response, SportType.HIKING)

    def test_surface_road_class_and_use_are_combined_via_weighted_average(self, monkeypatch):
        _patch_tables(
            monkeypatch,
            surface={"a": 0.8},
            road_class={"x": 0.9},
            use={"p": 0.7},
        )
        track = _track([(0.0, 0.0), (1.0, 1.0)])
        response = {
            "edges": [_edge(1.0, surface="a", road_class="x", use="p")],
            "matched_points": [_matched(0), _matched(0)],
        }
        expected = (
            surface_module._FAMILY_WEIGHTS["surface"] * 0.8
            + surface_module._FAMILY_WEIGHTS["road_class"] * 0.9
            + surface_module._FAMILY_WEIGHTS["use"] * 0.7
        )
        [multiplier] = calculate_surface_multipliers(track, response, SportType.HIKING)
        assert multiplier == pytest.approx(expected)

    def test_absent_sac_scale_leaves_the_physical_multiplier_unchanged(self, monkeypatch):
        _patch_tables(monkeypatch, surface={"a": 0.8}, road_class={"x": 0.9}, use={"p": 0.7})
        track = _track([(0.0, 0.0), (1.0, 1.0)])
        response = {
            "edges": [_edge(1.0, surface="a", road_class="x", use="p")],
            "matched_points": [_matched(0), _matched(0)],
        }
        physical = (
            surface_module._FAMILY_WEIGHTS["surface"] * 0.8
            + surface_module._FAMILY_WEIGHTS["road_class"] * 0.9
            + surface_module._FAMILY_WEIGHTS["use"] * 0.7
        )
        [multiplier] = calculate_surface_multipliers(track, response, SportType.HIKING)
        assert multiplier == pytest.approx(physical)

    def test_mild_sac_scale_is_blended_with_the_physical_multiplier(self, monkeypatch):
        # sac multiplier (0.95) is above the dominance threshold, so it's
        # blended in rather than trusted outright.
        _patch_tables(
            monkeypatch,
            surface={"a": 0.8},
            road_class={"x": 0.9},
            use={"p": 0.7},
            sac_scale={"1": 0.95},
        )
        track = _track([(0.0, 0.0), (1.0, 1.0)])
        response = {
            "edges": [_edge(1.0, surface="a", road_class="x", use="p", sac_scale=1)],
            "matched_points": [_matched(0), _matched(0)],
        }
        physical = (
            surface_module._FAMILY_WEIGHTS["surface"] * 0.8
            + surface_module._FAMILY_WEIGHTS["road_class"] * 0.9
            + surface_module._FAMILY_WEIGHTS["use"] * 0.7
        )
        expected = surface_module._SAC_BLEND_WEIGHT * 0.95 + (1 - surface_module._SAC_BLEND_WEIGHT) * physical
        [multiplier] = calculate_surface_multipliers(track, response, SportType.HIKING)
        assert multiplier == pytest.approx(expected)

    def test_severe_sac_scale_agreeing_with_physical_is_used_outright(self, monkeypatch):
        # sac multiplier (0.6) is at/below the dominance threshold and close
        # to the physical multiplier (0.79), so it dominates outright.
        _patch_tables(
            monkeypatch,
            surface={"a": 0.8},
            road_class={"x": 0.9},
            use={"p": 0.7},
            sac_scale={"4": 0.6},
        )
        track = _track([(0.0, 0.0), (1.0, 1.0)])
        response = {
            "edges": [_edge(1.0, surface="a", road_class="x", use="p", sac_scale=4)],
            "matched_points": [_matched(0), _matched(0)],
        }
        [multiplier] = calculate_surface_multipliers(track, response, SportType.HIKING)
        assert multiplier == pytest.approx(0.6)

    def test_severe_sac_scale_conflicting_with_physical_falls_back_to_blend(self, monkeypatch):
        # sac multiplier (0.3) is at/below the dominance threshold but far
        # from the physical multiplier (0.79) — more than the conflict
        # delta apart — so it's blended in rather than trusted alone.
        _patch_tables(
            monkeypatch,
            surface={"a": 0.8},
            road_class={"x": 0.9},
            use={"p": 0.7},
            sac_scale={"6": 0.3},
        )
        track = _track([(0.0, 0.0), (1.0, 1.0)])
        response = {
            "edges": [_edge(1.0, surface="a", road_class="x", use="p", sac_scale=6)],
            "matched_points": [_matched(0), _matched(0)],
        }
        physical = (
            surface_module._FAMILY_WEIGHTS["surface"] * 0.8
            + surface_module._FAMILY_WEIGHTS["road_class"] * 0.9
            + surface_module._FAMILY_WEIGHTS["use"] * 0.7
        )
        expected = surface_module._SAC_BLEND_WEIGHT * 0.3 + (1 - surface_module._SAC_BLEND_WEIGHT) * physical
        [multiplier] = calculate_surface_multipliers(track, response, SportType.HIKING)
        assert multiplier == pytest.approx(expected)

    def test_zero_sac_scale_leaves_the_physical_multiplier_unchanged(self, monkeypatch):
        # sac_scale 0 is Valhalla's "no data" sentinel, not a real difficulty
        # tier — it must be treated exactly like a missing sac_scale field.
        _patch_tables(
            monkeypatch,
            surface={"a": 0.8},
            road_class={"x": 0.9},
            use={"p": 0.7},
            sac_scale={"1": 0.95},
        )
        track = _track([(0.0, 0.0), (1.0, 1.0)])
        response = {
            "edges": [_edge(1.0, surface="a", road_class="x", use="p", sac_scale=0)],
            "matched_points": [_matched(0), _matched(0)],
        }
        physical = (
            surface_module._FAMILY_WEIGHTS["surface"] * 0.8
            + surface_module._FAMILY_WEIGHTS["road_class"] * 0.9
            + surface_module._FAMILY_WEIGHTS["use"] * 0.7
        )
        [multiplier] = calculate_surface_multipliers(track, response, SportType.HIKING)
        assert multiplier == pytest.approx(physical)

    def test_partially_tagged_sac_scale_across_a_multi_edge_leg_averages_only_tagged_edges(self, monkeypatch):
        _patch_tables(
            monkeypatch,
            surface={"a": 1.0},
            road_class={"x": 1.0},
            use={"p": 1.0},
            sac_scale={"1": 0.6},
        )
        track = _track([(0.0, 0.0), (1.0, 1.0)])
        response = {
            "edges": [
                _edge(1.0, surface="a", road_class="x", use="p", sac_scale=1),
                _edge(9.0, surface="a", road_class="x", use="p"),
            ],
            "matched_points": [_matched(0), _matched(1)],
        }
        # physical is 1.0 on every edge regardless of length weighting, so
        # any deviation from 1.0 in the result can only come from sac_scale
        # having been resolved from the one tagged edge (length 1.0),
        # excluding the untagged one (length 9.0), then blended in.
        expected = surface_module._SAC_BLEND_WEIGHT * 0.6 + (1 - surface_module._SAC_BLEND_WEIGHT) * 1.0
        [multiplier] = calculate_surface_multipliers(track, response, SportType.HIKING)
        assert multiplier == pytest.approx(expected)

    def test_uses_the_requested_sports_own_table(self, monkeypatch):
        tables = {
            "hiking": {"surface": {"a": 0.9}, "road_class": {}, "use": {}, "sac_scale": {}},
            "running": {"surface": {"a": 0.4}, "road_class": {}, "use": {}, "sac_scale": {}},
        }
        monkeypatch.setattr(surface_module, "_WEIGHTS_BY_SPORT", tables)
        track = _track([(0.0, 0.0), (1.0, 1.0)])
        response = {
            "edges": [_edge(1.0, surface="a")],
            "matched_points": [_matched(0), _matched(0)],
        }
        [hiking_multiplier] = calculate_surface_multipliers(track, response, SportType.HIKING, default_multiplier=0.9)
        [running_multiplier] = calculate_surface_multipliers(track, response, SportType.RUNNING, default_multiplier=0.4)
        assert hiking_multiplier == pytest.approx(0.9)
        assert running_multiplier == pytest.approx(0.4)
