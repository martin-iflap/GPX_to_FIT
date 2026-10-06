"""Per-leg surface-based speed multipliers derived from a Valhalla
trace_attributes map-match response.

This module never performs the HTTP call itself. It only
(1) shapes the request payload as plain JSON-serializable data, and
(2) turns an already-fetched Valhalla response dict into per-leg
multipliers, using sport-specific weight tables loaded from
surface_weights.json.
combine.py is expected to multiply these into the per-leg speeds returned
by gradient.py's minetti_speeds_from_gradients/tobler_speeds_from_gradients.

Combining signals: `surface`, `road_class`, and `use` are correlated tags
that often restate the same underlying fact about an edge (e.g. a minor
unpaved trail is typically `surface=gravel` *and* `use=path` *and*
`road_class=unclassified`), so they are averaged together (weighted by
_FAMILY_WEIGHTS) into one "physical surface" factor rather than multiplied,
which would compound the same fact multiple times. `sac_scale` is a
separate, independent difficulty signal that's frequently absent (only
tagged on OSM ways that carry it); when present it's blended in at a high
weight, and when it indicates serious difficulty and roughly agrees with
the physical-surface factor, it's trusted outright.
"""

import json
import pathlib
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from gpx2fit.core.models import SportType, Track

_DEFAULT_COSTING = "pedestrian"
_SHAPE_MATCH = "walk_or_snap" # tries to 'edge_walk', falls back to 'map_snap'

_WEIGHTS_PATH = pathlib.Path(__file__).parent / "surface_weights.json"
with open(_WEIGHTS_PATH, encoding="utf-8") as _f:
    _WEIGHTS_BY_SPORT: dict[str, dict[str, dict[str, float]]] = json.load(_f)

# Weights for combining the surface/road_class/use family into one
# "physical surface" multiplier. road_class is weighted lowest: it's the
# noisiest of the three and frequently "unclassified" on exactly the minor
# trails this matters most for.
_FAMILY_WEIGHTS = {"surface": 0.5, "use": 0.3, "road_class": 0.2}

# sac_scale combination constants.
_SAC_BLEND_WEIGHT = 0.7 # sac_scale's share when blended with the physical-surface factor
_SAC_DOMINANCE_THRESHOLD = 0.85 # sac multiplier at/below this = meaningful difficulty, take it seriously
_SAC_CONFLICT_DELTA = 0.25 # |sac_m - physical| beyond this = don't fully trust sac_scale alone

# When at least this share of the route's distance had no usable match (and
# so got the default multiplier), the user is told.
SURFACE_FALLBACK_WARNING_SHARE = 0.10

# Surface features shorter than this (a road crossing, a short bridge, a
# matching glitch) are smoothed away by _remove_surface_spikes.
SURFACE_SPIKE_HALF_WINDOW_M = 25.0


def build_trace_attributes_payload(track: Track, costing: str = _DEFAULT_COSTING) -> dict:
    """Build the request body for Valhalla's trace_attributes endpoint.

    Args:
        track: Track whose points become the request's shape, one shape
            point per track point, in order.
        costing: Valhalla costing model to request.

    Returns:
        A plain, JSON-serializable dict: {"shape": [...], "costing": ...,
        "shape_match": "walk_or_snap"}. The caller is responsible for actually
        sending this (e.g. via pyfetch).

    Raises:
        ValueError: If track.points is empty.
    """
    if not track.points:
        raise ValueError("Cannot build a trace_attributes payload for a track with no points.")

    return {
        "shape": [{"lat": p.lat, "lon": p.lon} for p in track.points],
        "costing": costing,
        "shape_match": _SHAPE_MATCH,
    }


def resolve_point_edge_indexes(response: dict, num_points: int) -> list[int | None]:
    """Map each of `num_points` input points to a matched Valhalla edge index.

    Valhalla's trace_attributes response includes one `matched_points` entry
    per shape point sent in the request, in the same order, each carrying
    the index (into `response["edges"]`) of the edge it snapped to. Points
    that failed to match are filled in from their nearest matched neighbor.

    Args:
        response: Parsed trace_attributes JSON response.
        num_points: Number of points originally sent as the request shape
            (normally len(track.points)) — the result is always this long,
            regardless of how many entries `response["matched_points"]` has.

    Returns:
        One edge index per point, or None for a point that stays
        unresolved. An entry can only be None if no point anywhere in the
        response matched (a fully degenerate response) — otherwise every
        unmatched point is filled from its nearest matched neighbor.
    """
    edges = response.get("edges") or []
    num_edges = len(edges)
    matched = response.get("matched_points") or []

    raw: list[int | None] = []
    for i in range(num_points):
        entry = matched[i] if i < len(matched) else None
        if not isinstance(entry, dict) or entry.get("type") == "unmatched":
            raw.append(None)
            continue
        edge_index = entry.get("edge_index")
        if not isinstance(edge_index, int) or not (0 <= edge_index < num_edges):
            raw.append(None)
            continue
        raw.append(edge_index)

    # (value, index distance) of the nearest resolved neighbor in each direction,
    # kept in separate lists so `resolved` below stays a plain list[int | None].
    forward: list[tuple[int, int] | None] = [None] * num_points
    last_value, last_index = None, -1
    for i in range(num_points):
        if raw[i] is not None:
            last_value, last_index = raw[i], i
        elif last_value is not None:
            forward[i] = (last_value, i - last_index)

    backward: list[tuple[int, int] | None] = [None] * num_points
    last_value, last_index = None, -1
    for i in range(num_points - 1, -1, -1):
        if raw[i] is not None:
            last_value, last_index = raw[i], i
        elif last_value is not None:
            backward[i] = (last_value, last_index - i)

    resolved: list[int | None] = []
    for i in range(num_points):
        if raw[i] is not None:
            resolved.append(raw[i])
            continue
        fwd, bwd = forward[i], backward[i]
        if fwd is not None and bwd is not None:
            resolved.append(fwd[0] if fwd[1] <= bwd[1] else bwd[0])
        elif fwd is not None:
            resolved.append(fwd[0])
        elif bwd is not None:
            resolved.append(bwd[0])
        else:
            resolved.append(None)

    return resolved


def _length_weighted_average(
    edges: list[dict[str, Any]],
    value_of: Callable[[dict[str, Any]], float],
) -> float:
    """Average `value_of(edge)` over `edges`, weighted by each edge's length.

    Falls back to an unweighted average if every edge spans zero length
    (`edge["length"]` missing or 0), so a leg made entirely of zero-length
    edges still yields a meaningful result instead of a division by zero.

    Args:
        edges: Edges to average over. Must be non-empty.
        value_of: Function returning the value to average for one edge.

    Returns:
        The length-weighted (or, as a fallback, unweighted) average of
        `value_of` over `edges`.
    """
    lengths = [e.get("length", 0.0) for e in edges]
    total_length = sum(lengths)
    if total_length > 0:
        return sum(value_of(e) * l for e, l in zip(edges, lengths)) / total_length
    return sum(value_of(e) for e in edges) / len(edges)


def _leg_category_multiplier(
    edges: list[dict[str, Any]],
    tag: str,
    weights: dict[str, float],
    default_multiplier: float,
) -> float:
    """Compute the length-weighted average multiplier for one edge tag.

    An edge missing `tag`, or whose value isn't in `weights`, contributes
    `default_multiplier` — appropriate for `surface`/`road_class`/`use`,
    where an untagged edge usually just means an ordinary road.

    Args:
        edges: Edges spanned by one leg, in order.
        tag: Edge field to look up (e.g. "surface").
        weights: Tag value -> multiplier table for this category/sport.
        default_multiplier: Used for an edge whose `tag` is missing or
            whose value isn't in `weights`.

    Returns:
        The length-weighted average multiplier over `edges` for `tag`.
    """

    def edge_multiplier(edge: dict[str, Any]) -> float:
        value = edge.get(tag)
        if value is None:
            return default_multiplier
        return weights.get(value, default_multiplier)

    return _length_weighted_average(edges, edge_multiplier)


def _leg_sac_scale_multiplier(
    edges: list[dict[str, Any]],
    weights: dict[str, float],
    default_multiplier: float,
) -> float | None:
    """Compute the length-weighted average sac_scale multiplier for a leg.

    Valhalla encodes `sac_scale` as an integer 0-6: 0 means the edge carries
    no sac_scale tag at all , and 1-6 map to OSM's `hiking`
    through `difficult_alpine_hiking` tiers in increasing difficulty.
    `weights` is keyed by that integer's string form (e.g. "1", "6")
    to match how it round-trips through surface_weights.json.

    Unlike `_leg_category_multiplier`, an edge with no sac_scale data (0 or
    missing) is excluded from the average entirely rather than defaulted.
    The tag is only present on a minority of OSM ways, so "no data" must
    not be treated as "known-easy terrain".

    Args:
        edges: Edges spanned by one leg, in order.
        weights: sac_scale value (as a string, e.g. "3") -> multiplier
            table for this sport.
        default_multiplier: Used for a tagged edge whose sac_scale value
            isn't in `weights`.

    Returns:
        The length-weighted average multiplier over edges that carry
        sac_scale data, or None if no edge in `edges` carries any.
    """
    tagged_edges = [e for e in edges if e.get("sac_scale")]
    if not tagged_edges:
        return None
    return _length_weighted_average(tagged_edges, lambda e: weights.get(str(e["sac_scale"]), default_multiplier))


def _remove_surface_spikes(
    multipliers: list[float],
    leg_lengths: list[float],
    half_window_m: float = SURFACE_SPIKE_HALF_WINDOW_M,
) -> list[float]:
    """Remove surface features too short to be real, keeping real surface changes sharp.

    Each leg takes the distance-weighted median of the multipliers within
    ±half_window_m of its midpoint, i.e. the value covering the larger part of
    that window. A feature shorter than half_window_m (a paved road crossing on
    a trail, a short bridge) never covers the larger part, so it is replaced by
    its surroundings. A longer one keeps its value, and a change between two
    long stretches stays a sharp step in the same place. Unlike a moving
    average, nothing else is touched, and the window is in metres, so the
    result doesn't depend on the GPX's point density.

    Args:
        multipliers: One speed multiplier per leg.
        leg_lengths: Each leg's length in metres, the same length as
            multipliers. A zero-length leg carries no weight.
        half_window_m: Half the window's length.

    Returns:
        A new list with one multiplier per leg. The input is not modified.
    """
    n = len(multipliers)
    result = list(multipliers)
    if n < 3:
        return result

    starts = [0.0] * n
    for i in range(1, n):
        starts[i] = starts[i - 1] + leg_lengths[i - 1]

    # First and last index of the run of equal values each leg belongs to, so
    # a window holding one value throughout is recognized without looking inside.
    run_first = [0] * n
    for i in range(1, n):
        run_first[i] = run_first[i - 1] if multipliers[i] == multipliers[i - 1] else i
    run_last = [n - 1] * n
    for i in range(n - 2, -1, -1):
        run_last[i] = run_last[i + 1] if multipliers[i] == multipliers[i + 1] else i

    # lo...hi are the legs touching the current window. Both only move forward,
    # so finding them costs O(n) over the whole track.
    lo = hi = 0
    for i in range(n):
        centre = starts[i] + leg_lengths[i] / 2
        window_start, window_end = centre - half_window_m, centre + half_window_m
        while lo < i and starts[lo] + leg_lengths[lo] <= window_start:
            lo += 1
        while hi + 1 < n and starts[hi + 1] < window_end:
            hi += 1
        if run_first[i] <= lo and run_last[i] >= hi:
            continue  # one value across the whole window, which is nearly every leg

        # Metres of the window each value covers, a whole run of equal values at a time.
        metres_by_value: defaultdict[float, float] = defaultdict(float)
        j = lo
        while j <= hi:
            run_end = run_last[j]
            overlap = min(window_end, starts[run_end] + leg_lengths[run_end]) - max(window_start, starts[j])
            if overlap > 0:
                metres_by_value[multipliers[j]] += overlap
            j = run_end + 1
        if not metres_by_value:
            continue  # only zero-length legs nearby

        half = sum(metres_by_value.values()) / 2
        covered = 0.0
        for value, metres in sorted(metres_by_value.items()):
            covered += metres
            if covered >= half:
                result[i] = value
                break

    return result


def calculate_surface_multipliers(
    track: Track,
    response: dict,
    sport: SportType,
    default_multiplier: float = 1.0,
    *,
    warnings: list[str] | None = None,
) -> list[float]:
    """Calculate per-leg speed multipliers from a Valhalla trace_attributes response.

    Args:
        track: Track whose points were sent as the Valhalla request shape,
            in the same order.
        response: Parsed trace_attributes JSON response for that request.
        sport: Sport whose weight tables (surface, road_class, use,
            sac_scale) should be used.
        default_multiplier: Used whenever a leg's surface/road_class/use
            can't be resolved (no matched edge) or a resolved value isn't in
            the relevant weight table.
        warnings: If given, a user-facing message is appended when legs
            without a usable match cover at least
            SURFACE_FALLBACK_WARNING_SHARE of the route's distance.

    Returns:
        One relative speed multiplier per leg (N-1 values for N points).
        `surface`, `road_class`, and `use` are combined into one
        weighted-average "physical surface" factor (see _FAMILY_WEIGHTS).
        `sac_scale`, when present on at least one spanned edge, is blended
        in at a high weight (_SAC_BLEND_WEIGHT); if it also indicates
        serious difficulty (at/below _SAC_DOMINANCE_THRESHOLD) and roughly
        agrees with the physical-surface factor (within
        _SAC_CONFLICT_DELTA), it's used outright instead.

        A leg with an unresolved endpoint gets `default_multiplier` directly,
        and so does a leg whose end-point edge index is smaller than its
        start-point one. `response["edges"]` is ordered along the matched
        path, so that only happens on a matching glitch, and slicing the
        wrong span of edges would be worse than having no surface for that
        one leg. It used to raise, which cost the whole route its surfaces.
    """
    if len(track.points) < 2:
        return []

    tables = _WEIGHTS_BY_SPORT[sport.value]
    edges = response.get("edges") or []
    point_edge_indexes = resolve_point_edge_indexes(response, len(track.points))
    leg_lengths = [
        curr.distance_from_start - prev.distance_from_start for prev, curr in zip(track.points, track.points[1:])
    ]

    multipliers = []
    fallback_length = 0.0
    for leg_length, start_edge, end_edge in zip(leg_lengths, point_edge_indexes, point_edge_indexes[1:]):
        if start_edge is None or end_edge is None or start_edge > end_edge:
            multipliers.append(default_multiplier)
            fallback_length += leg_length
            continue

        edge_slice = edges[start_edge:end_edge + 1]

        surface_m = _leg_category_multiplier(edge_slice, "surface", tables["surface"], default_multiplier)
        road_class_m = _leg_category_multiplier(edge_slice, "road_class", tables["road_class"], default_multiplier)
        use_m = _leg_category_multiplier(edge_slice, "use", tables["use"], default_multiplier)
        physical = (
            _FAMILY_WEIGHTS["surface"] * surface_m
            + _FAMILY_WEIGHTS["road_class"] * road_class_m
            + _FAMILY_WEIGHTS["use"] * use_m
        )

        sac_m = _leg_sac_scale_multiplier(edge_slice, tables["sac_scale"], default_multiplier)

        if sac_m is None:
            final = physical
        elif sac_m <= _SAC_DOMINANCE_THRESHOLD and abs(sac_m - physical) <= _SAC_CONFLICT_DELTA:
            final = sac_m
        else:
            final = _SAC_BLEND_WEIGHT * sac_m + (1 - _SAC_BLEND_WEIGHT) * physical

        multipliers.append(final)

    total_length = sum(leg_lengths)
    fallback_share = fallback_length / total_length if total_length > 0 else 0.0
    if warnings is not None and fallback_share >= SURFACE_FALLBACK_WARNING_SHARE:
        warnings.append(
            f"Surface data couldn't be matched to {fallback_share:.0%} of the route, "
            "so those parts are paced on gradient alone."
        )

    return _remove_surface_spikes(multipliers, leg_lengths)


# todo: surface_weights.json's tables are tuned mostly for hiking. The weights might need adjusting, especially for running.
# surface fallback warning share is a first guess.
# surface spike half window is a first guess.