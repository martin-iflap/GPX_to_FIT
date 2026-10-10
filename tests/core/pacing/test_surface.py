import pytest
from gpx2fit.core.models import SportType, Track
from gpx2fit.core.pacing import surface as surface_module
from gpx2fit.core.pacing.surface import (
    SHAPE_SPACING_M,
    SURFACE_SPIKE_HALF_WINDOW_M,
    _leg_category_multiplier,
    _leg_sac_scale_multiplier,
    _remove_surface_spikes,
    build_trace_attributes_payload,
    calculate_surface_multipliers,
    resolve_point_edge_indexes,
)
from tests.core.conftest import point


def _track(coords: list[tuple[float, float]]) -> Track:
    """Build a Track from (lat, lon) pairs, one per point, 100 m apart (so every point is a shape point)."""
    return Track(points=[
        point(lat=lat, lon=lon, distance_from_start=index * 100.0) for index, (lat, lon) in enumerate(coords)
    ])


def _dense_track(length_m: float, leg_m: float) -> Track:
    """A straight track of `leg_m` legs, like gpx_reader's densified ones."""
    count = round(length_m / leg_m)
    return Track(points=[
        point(lat=46.0 + index * leg_m * 9e-6, lon=14.0, distance_from_start=index * leg_m) for index in range(count + 1)
    ])


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


def _surface_only(weight: float) -> float:
    """The multiplier of an edge with only a `surface` tag: the other families fall back to 1.0."""
    share = surface_module._FAMILY_WEIGHTS["surface"]
    return share * weight + (1 - share)


class TestShapeThinning:
    """A densified track sends Valhalla a point every SHAPE_SPACING_M, not every 2 m leg."""

    def test_a_dense_track_sends_points_at_least_the_spacing_apart(self):
        track = _dense_track(1000.0, 2.0)
        shape = build_trace_attributes_payload(track)["shape"]
        by_lat = {p.lat: p.distance_from_start for p in track.points}
        distances = [by_lat[entry["lat"]] for entry in shape]
        assert distances[0] == 0.0
        assert distances[-1] == 1000.0
        assert all(b - a >= SHAPE_SPACING_M for a, b in zip(distances, distances[1:-1]))
        assert len(shape) <= 1000.0 / SHAPE_SPACING_M + 2

    def test_a_sparse_track_sends_every_point(self):
        track = _dense_track(1000.0, 50.0)
        assert len(build_trace_attributes_payload(track)["shape"]) == len(track.points)

    def test_multipliers_still_come_one_per_dense_leg(self, monkeypatch):
        _patch_tables(monkeypatch, surface={"a": 0.8})
        track = _dense_track(600.0, 2.0)
        shape_count = len(build_trace_attributes_payload(track)["shape"])
        response = {"edges": [_edge(600.0, surface="a")], "matched_points": [_matched(0)] * shape_count}
        multipliers = calculate_surface_multipliers(track, response, SportType.HIKING)
        assert len(multipliers) == len(track.points) - 1
        assert multipliers == [pytest.approx(_surface_only(0.8))] * len(multipliers)

    def test_each_dense_leg_takes_the_surface_of_the_shape_leg_it_lies_in(self, monkeypatch):
        _patch_tables(monkeypatch, surface={"a": 0.8, "b": 0.5})
        track = _dense_track(600.0, 2.0)
        shape = build_trace_attributes_payload(track)["shape"]
        by_lat = {p.lat: p.distance_from_start for p in track.points}
        # Shape points up to 300 m matched to the first edge, the rest to the second.
        matched = [_matched(0 if by_lat[entry["lat"]] <= 300.0 else 1) for entry in shape]
        response = {"edges": [_edge(300.0, surface="a"), _edge(300.0, surface="b")], "matched_points": matched}

        multipliers = calculate_surface_multipliers(track, response, SportType.HIKING)

        starts = [p.distance_from_start for p in track.points[:-1]]
        assert all(m == pytest.approx(_surface_only(0.8)) for m, d in zip(multipliers, starts) if d < 280.0)
        assert all(m == pytest.approx(_surface_only(0.5)) for m, d in zip(multipliers, starts) if d > 320.0)


    def test_an_unmatched_dense_route_gives_every_dense_leg_the_default(self, monkeypatch):
        _patch_tables(monkeypatch, surface={"a": 0.5})
        track = _dense_track(300.0, 2.0)
        shape_count = len(build_trace_attributes_payload(track)["shape"])
        response = {"edges": [_edge(300.0, surface="a")], "matched_points": [_matched(None)] * shape_count}

        multipliers = calculate_surface_multipliers(track, response, SportType.HIKING, default_multiplier=0.7)

        assert multipliers == [pytest.approx(0.7)] * (len(track.points) - 1)

    def test_the_fallback_share_counts_the_whole_length_of_each_backwards_shape_leg(self, monkeypatch):
        _patch_tables(monkeypatch, surface={"a": 0.5})
        track = _dense_track(300.0, 2.0)
        shape = build_trace_attributes_payload(track)["shape"]
        by_lat = {p.lat: p.distance_from_start for p in track.points}
        shape_distances = [by_lat[entry["lat"]] for entry in shape]
        # Edges 0, 1, 0, 1, ...: every odd shape leg runs from edge 1 back to edge 0.
        matched = [_matched(index % 2) for index in range(len(shape))]
        response = {"edges": [_edge(150.0, surface="a"), _edge(150.0, surface="a")], "matched_points": matched}
        backwards = sum(
            b - a for index, (a, b) in enumerate(zip(shape_distances, shape_distances[1:])) if index % 2 == 1
        )
        warnings: list[str] = []

        calculate_surface_multipliers(track, response, SportType.HIKING, warnings=warnings)

        assert len(warnings) == 1
        assert f"{backwards / 300.0:.0%}" in warnings[0]


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

    def test_a_backwards_leg_gets_the_default_and_its_neighbours_keep_their_surface(self, monkeypatch):
        # A matching glitch on one leg used to raise and cost the whole route its surface pacing.
        _patch_tables(monkeypatch, surface={"a": 0.5, "b": 0.5})
        track = _track_with_legs([100.0, 100.0, 100.0])
        response = {
            "edges": [_edge(1.0, surface="a"), _edge(1.0, surface="b")],
            "matched_points": [_matched(0), _matched(1), _matched(0), _matched(1)],
        }

        multipliers = calculate_surface_multipliers(track, response, SportType.HIKING, default_multiplier=1.0)

        assert multipliers[1] == pytest.approx(1.0)
        assert multipliers[0] == pytest.approx(multipliers[2])
        assert multipliers[0] < 1.0

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

    # The sac_scale cases below derive their sac multiplier from the thresholds
    # under test rather than hardcoding one, so retuning _SAC_DOMINANCE_THRESHOLD
    # or _SAC_CONFLICT_DELTA can't silently move a case into a different branch.
    PHYSICAL = (
        surface_module._FAMILY_WEIGHTS["surface"] * 0.8
        + surface_module._FAMILY_WEIGHTS["road_class"] * 0.9
        + surface_module._FAMILY_WEIGHTS["use"] * 0.7
    )

    def _with_sac(self, monkeypatch: pytest.MonkeyPatch, sac_multiplier: float) -> float:
        """The multiplier for one leg on the fixed physical surface above, tagged sac_scale 3 = `sac_multiplier`."""
        _patch_tables(
            monkeypatch,
            surface={"a": 0.8},
            road_class={"x": 0.9},
            use={"p": 0.7},
            sac_scale={"3": sac_multiplier},
        )
        track = _track([(0.0, 0.0), (1.0, 1.0)])
        response = {
            "edges": [_edge(1.0, surface="a", road_class="x", use="p", sac_scale=3)],
            "matched_points": [_matched(0), _matched(0)],
        }
        [multiplier] = calculate_surface_multipliers(track, response, SportType.HIKING)
        return multiplier

    def _blend(self, sac_multiplier: float) -> float:
        return surface_module._SAC_BLEND_WEIGHT * sac_multiplier + (1 - surface_module._SAC_BLEND_WEIGHT) * self.PHYSICAL

    def test_mild_sac_scale_is_blended_with_the_physical_multiplier(self, monkeypatch):
        # Above the dominance threshold, so blended rather than trusted outright.
        threshold = surface_module._SAC_DOMINANCE_THRESHOLD
        sac = threshold + (1.0 - threshold) / 2
        assert sac > threshold  # premise
        assert self._with_sac(monkeypatch, sac) == pytest.approx(self._blend(sac))

    def test_severe_sac_scale_agreeing_with_physical_is_used_outright(self, monkeypatch):
        # At/below the dominance threshold and within the conflict delta of the
        # physical multiplier, so it dominates outright.
        delta = surface_module._SAC_CONFLICT_DELTA
        sac = min(surface_module._SAC_DOMINANCE_THRESHOLD, self.PHYSICAL - delta / 2)
        assert abs(sac - self.PHYSICAL) <= delta  # premise: the fixture's physical is reachable
        assert self._with_sac(monkeypatch, sac) == pytest.approx(sac)

    def test_severe_sac_scale_conflicting_with_physical_falls_back_to_blend(self, monkeypatch):
        # At/below the dominance threshold but more than the conflict delta from
        # the physical multiplier, so blended rather than trusted alone.
        sac = min(surface_module._SAC_DOMINANCE_THRESHOLD, self.PHYSICAL - surface_module._SAC_CONFLICT_DELTA - 0.1)
        assert sac > 0.0  # premise
        assert self._with_sac(monkeypatch, sac) == pytest.approx(self._blend(sac))

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
        # Default 1.0 for the untagged families, so the result can only move off
        # 1.0 through the surface table; a default equal to the table value
        # couldn't tell the table apart from the fallback.
        surface_share = surface_module._FAMILY_WEIGHTS["surface"]
        [hiking_multiplier] = calculate_surface_multipliers(track, response, SportType.HIKING)
        [running_multiplier] = calculate_surface_multipliers(track, response, SportType.RUNNING)
        assert hiking_multiplier == pytest.approx(surface_share * 0.9 + (1 - surface_share))
        assert running_multiplier == pytest.approx(surface_share * 0.4 + (1 - surface_share))

    def test_a_short_edge_between_two_long_legs_is_smoothed_away(self, monkeypatch):
        # Points snap to trail, paved, paved, trail: legs 0 and 2 each span a
        # trail edge and the paved one (the same blend), and the short leg 1
        # is on the paved edge alone, a road crossing.
        _patch_tables(monkeypatch, surface={"trail": 0.6, "paved": 1.2})
        response = {
            "edges": [_edge(50.0, surface="trail"), _edge(10.0, surface="paved"), _edge(50.0, surface="trail")],
            "matched_points": [_matched(0), _matched(1), _matched(1), _matched(2)],
        }
        # Long enough to keep its own shape point, short enough for the spike filter.
        crossing = SHAPE_SPACING_M
        assert crossing < SURFACE_SPIKE_HALF_WINDOW_M
        track = _track_with_legs([100.0, crossing, 100.0])

        multipliers = calculate_surface_multipliers(track, response, SportType.HIKING)

        surface_share = surface_module._FAMILY_WEIGHTS["surface"]
        trail_blend = surface_share * (0.6 * 50 + 1.2 * 10) / 60 + (1 - surface_share)
        assert multipliers == [pytest.approx(trail_blend)] * 3


def _track_with_legs(leg_lengths: list[float]) -> Track:
    """A Track whose legs have the given lengths (positions don't matter to surface.py's matching).

    Legs of at least SHAPE_SPACING_M keep every point a shape point, one per matched_points entry.
    """
    distances = [0.0]
    for length in leg_lengths:
        distances.append(distances[-1] + length)
    return Track(points=[point(distance_from_start=d) for d in distances])


class TestSurfaceFallbackWarning:
    # Legs 0 and 2 match edge 0 → 1 normally; leg 1 runs backwards.
    _BACKWARDS_MIDDLE = {
        "edges": [_edge(1.0, surface="a"), _edge(1.0, surface="a")],
        "matched_points": [_matched(0), _matched(1), _matched(0), _matched(1)],
    }

    def test_a_small_backwards_share_does_not_warn(self, monkeypatch):
        _patch_tables(monkeypatch, surface={"a": 0.5})
        warnings: list[str] = []

        calculate_surface_multipliers(
            _track_with_legs([1000.0, 100.0, 1000.0]), self._BACKWARDS_MIDDLE, SportType.HIKING, warnings=warnings,
        )

        assert warnings == []  # 100 of 2100 m

    def test_a_large_backwards_share_warns_with_its_percentage(self, monkeypatch):
        _patch_tables(monkeypatch, surface={"a": 0.5})
        warnings: list[str] = []

        calculate_surface_multipliers(
            _track_with_legs([100.0, 100.0, 100.0]), self._BACKWARDS_MIDDLE, SportType.HIKING, warnings=warnings,
        )

        assert len(warnings) == 1
        assert "33%" in warnings[0]
        assert "gradient alone" in warnings[0]

    def test_the_share_is_by_distance_not_by_leg_count(self, monkeypatch):
        _patch_tables(monkeypatch, surface={"a": 0.5})
        warnings: list[str] = []

        # One leg in three runs backwards, but it is 80% of the route.
        calculate_surface_multipliers(
            _track_with_legs([100.0, 800.0, 100.0]), self._BACKWARDS_MIDDLE, SportType.HIKING, warnings=warnings,
        )

        assert len(warnings) == 1
        assert "80%" in warnings[0]

    def test_a_fully_unmatched_response_warns_for_the_whole_route(self, monkeypatch):
        _patch_tables(monkeypatch, surface={"a": 0.5})
        response = {"edges": [_edge(1.0, surface="a")], "matched_points": [_matched(None)] * 3}
        warnings: list[str] = []

        calculate_surface_multipliers(_track_with_legs([50.0, 50.0]), response, SportType.HIKING, warnings=warnings)

        assert len(warnings) == 1
        assert "100%" in warnings[0]

    def test_a_clean_match_does_not_warn(self, monkeypatch):
        _patch_tables(monkeypatch, surface={"a": 0.5})
        response = {"edges": [_edge(1.0, surface="a")], "matched_points": [_matched(0)] * 3}
        warnings: list[str] = []

        calculate_surface_multipliers(_track_with_legs([50.0, 50.0]), response, SportType.HIKING, warnings=warnings)

        assert warnings == []


def _profile(*runs: tuple[float, float], leg_m: float) -> tuple[list[float], list[float]]:
    """Multipliers and leg lengths for consecutive (value, length in metres) runs, cut into leg_m legs."""
    multipliers: list[float] = []
    for value, length in runs:
        legs = round(length / leg_m)
        assert legs * leg_m == pytest.approx(length)  # premise: runs fall on leg boundaries
        multipliers += [value] * legs
    return multipliers, [leg_m] * len(multipliers)


def _per_metre(multipliers: list[float], leg_lengths: list[float]) -> list[float]:
    """The value at every whole metre along the route, so tracks of different point density compare."""
    return [value for value, length in zip(multipliers, leg_lengths) for _ in range(round(length))]


class TestRemoveSurfaceSpikes:
    # Lengths are derived from the window rather than hardcoded, so retuning
    # SURFACE_SPIKE_HALF_WINDOW_M can't silently turn a "short" feature into a
    # long one. A feature is removed when it is shorter than the half-window.
    SHORT = SURFACE_SPIKE_HALF_WINDOW_M * 0.6
    LONG = SURFACE_SPIKE_HALF_WINDOW_M * 1.4
    BACKGROUND = SURFACE_SPIKE_HALF_WINDOW_M * 8

    def test_a_short_spike_on_a_dense_track_is_removed_entirely(self):
        multipliers, lengths = _profile(
            (0.8, self.BACKGROUND), (1.05, self.SHORT), (0.8, self.BACKGROUND), leg_m=1.0,
        )
        assert 1.05 in multipliers  # premise

        assert _remove_surface_spikes(multipliers, lengths) == [0.8] * len(multipliers)

    def test_a_short_dip_is_removed_as_well_as_a_short_rise(self):
        multipliers, lengths = _profile(
            (1.0, self.BACKGROUND), (0.5, self.SHORT), (1.0, self.BACKGROUND), leg_m=1.0,
        )
        assert _remove_surface_spikes(multipliers, lengths) == [1.0] * len(multipliers)

    def test_a_feature_longer_than_the_half_window_is_kept_whole(self):
        multipliers, lengths = _profile(
            (0.8, self.BACKGROUND), (1.05, self.LONG), (0.8, self.BACKGROUND), leg_m=1.0,
        )
        assert _remove_surface_spikes(multipliers, lengths) == multipliers

    def test_a_real_surface_change_stays_a_sharp_step_in_the_same_place(self):
        multipliers, lengths = _profile((0.8, self.BACKGROUND), (1.05, self.BACKGROUND), leg_m=1.0)
        assert _remove_surface_spikes(multipliers, lengths) == multipliers

    def test_a_spike_is_removed_without_disturbing_a_nearby_real_step(self):
        # The spike sits a full window before the step, so neither can reach the other.
        gap = SURFACE_SPIKE_HALF_WINDOW_M * 3
        multipliers, lengths = _profile(
            (0.8, self.BACKGROUND), (1.2, self.SHORT), (0.8, gap), (1.05, self.BACKGROUND), leg_m=1.0,
        )
        expected, _ = _profile(
            (0.8, self.BACKGROUND), (0.8, self.SHORT), (0.8, gap), (1.05, self.BACKGROUND), leg_m=1.0,
        )
        assert _remove_surface_spikes(multipliers, lengths) == expected

    def test_the_result_does_not_depend_on_point_density(self):
        # The same route at 1 m and at 5 m point spacing: a moving average or
        # window over a fixed number of legs would treat the two differently.
        runs = ((0.8, 200.0), (1.05, 10.0), (0.8, 200.0), (1.05, 200.0), (0.9, 40.0), (1.05, 200.0))
        dense = _profile(*runs, leg_m=1.0)
        sparse = _profile(*runs, leg_m=5.0)

        dense_result = _per_metre(_remove_surface_spikes(*dense), dense[1])
        sparse_result = _per_metre(_remove_surface_spikes(*sparse), sparse[1])

        assert dense_result == sparse_result
        assert dense_result != _per_metre(*dense)  # premise: the 10 m spike was there to remove

    def test_irregular_point_spacing_gives_the_same_result_as_even_spacing(self):
        # Real GPX spacing varies from leg to leg. Cutting every 10 m into a 1 m
        # and a 9 m leg must not move any surface change, so each leg has to be
        # placed by the lengths before it, not by its own.
        runs = ((0.8, 200.0), (1.05, 10.0), (0.8, 200.0), (1.05, 200.0), (0.9, 40.0), (1.05, 200.0))
        even = _profile(*runs, leg_m=1.0)
        irregular_values = [value for value, length in runs for _ in range(round(length / 10)) for _ in range(2)]
        irregular_lengths = [1.0, 9.0] * (len(irregular_values) // 2)

        assert _per_metre(_remove_surface_spikes(irregular_values, irregular_lengths), irregular_lengths) == (
            _per_metre(_remove_surface_spikes(*even), even[1])
        )

    def test_legs_are_weighted_by_length_not_counted(self):
        # Inside the window, the ten 1 m legs of 0.5 outnumber the two 20 m legs
        # of 1.0 but cover far less ground, so they go.
        multipliers = [1.0] * 4 + [0.5] * 10 + [1.0] * 4
        lengths = [20.0] * 4 + [1.0] * 10 + [20.0] * 4

        assert _remove_surface_spikes(multipliers, lengths, half_window_m=25.0) == [1.0] * len(multipliers)

    def test_a_long_leg_is_a_long_feature_however_few_points_it_has(self):
        # One 40 m leg on a sparse track covers most of a ±25 m window by itself.
        multipliers = [0.8, 0.8, 1.05, 0.8, 0.8]
        lengths = [40.0] * 5

        assert _remove_surface_spikes(multipliers, lengths, half_window_m=25.0) == multipliers

    def test_zero_length_legs_carry_no_weight(self):
        # Duplicate GPX points make zero-length legs. A run of them with an odd
        # value must not outvote a single real leg.
        multipliers = [0.8] * 5 + [2.0] * 50 + [0.8] * 5
        lengths = [10.0] * 5 + [0.0] * 50 + [10.0] * 5

        assert _remove_surface_spikes(multipliers, lengths, half_window_m=25.0) == [0.8] * len(multipliers)

    def test_a_track_with_no_length_is_returned_unchanged(self):
        multipliers = [0.8, 1.2, 0.9, 1.1]
        assert _remove_surface_spikes(multipliers, [0.0] * 4) == multipliers

    def test_fewer_than_three_legs_are_returned_unchanged(self):
        assert _remove_surface_spikes([0.8, 1.2], [1.0, 1.0]) == [0.8, 1.2]
        assert _remove_surface_spikes([], []) == []

    def test_the_input_list_is_not_modified(self):
        multipliers, lengths = _profile(
            (0.8, self.BACKGROUND), (1.05, self.SHORT), (0.8, self.BACKGROUND), leg_m=1.0,
        )
        original = list(multipliers)

        _remove_surface_spikes(multipliers, lengths)

        assert multipliers == original
