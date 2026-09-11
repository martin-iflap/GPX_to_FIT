"""Per-leg surface-based speed multipliers derived from a Valhalla
trace_attributes map-match response.

This module never performs the HTTP call itself. It only
(1) shapes the request payload as plain JSON-serializable data, and
(2) turns an already-fetched Valhalla response dict into per-leg
multipliers, using a caller-supplied surface -> multiplier weight table.
combine.py is expected to multiply these into the per-leg speeds returned
by gradient.py's calculate_minetti_speeds/calculate_tobler_speeds.
"""

from gpx2fit.core.models import Track

_DEFAULT_COSTING = "pedestrian"
_SHAPE_MATCH = "map_snap"

# Valhalla's edge "surface" field is one of exactly these eight values,
# smoothest to roughest. Multipliers are a first-pass placeholder (1.0 =
# no effect on the gradient-modeled speed) meant to be tuned once this is
# actually wired into combine.py and tried against real routes/data.
DEFAULT_SURFACE_WEIGHTS: dict[str, float] = {
    "paved_smooth": 1.0,
    "paved": 1.0,
    "paved_rough": 0.95,
    "compacted": 0.9,
    "dirt": 0.85,
    "gravel": 0.8,
    "path": 0.75,
    "impassable": 0.5,
}


def build_trace_attributes_payload(track: Track, costing: str = _DEFAULT_COSTING) -> dict:
    """Build the request body for Valhalla's trace_attributes endpoint.

    Args:
        track: Track whose points become the request's shape, one shape
            point per track point, in order.
        costing: Valhalla costing model to request.

    Returns:
        A plain, JSON-serializable dict: {"shape": [...], "costing": ...,
        "shape_match": "map_snap"}. The caller is responsible for actually
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


def calculate_surface_multipliers(
    track: Track,
    response: dict,
    weights: dict[str, float],
    default_multiplier: float = 1.0,
) -> list[float]:
    """Calculate per-leg speed multipliers from a Valhalla trace_attributes response.

    Args:
        track: Track whose points were sent as the Valhalla request shape,
            in the same order.
        response: Parsed trace_attributes JSON response for that request.
        weights: Surface name (e.g. "paved_smooth") -> speed multiplier.
        default_multiplier: Used whenever a leg's surface can't be resolved
            (no matched edge) or its surface name isn't in `weights`.

    Returns:
        One relative speed multiplier per leg (N-1 values for N points). A
        leg whose two endpoints fall on different edges is a length-weighted
        average of every edge spanned between them; a leg with an
        unresolved endpoint gets `default_multiplier`.

    Raises:
        ValueError: If a leg's end-point edge index is smaller than its
            start-point edge index. `response["edges"]` is documented to be
            ordered along the matched path, so this should never happen for
            a leg made of two consecutive, forward-progressing track points;
            if it does, something (out-and-back ambiguity, a matching
            glitch) has broken that assumption and silently slicing the
            wrong span of edges would be worse than failing loudly.
    """
    if len(track.points) < 2:
        return []

    edges = response.get("edges") or []
    point_edge_indexes = resolve_point_edge_indexes(response, len(track.points))

    def edge_multiplier(edge: dict) -> float:
        surface = edge.get("surface")
        if surface is None:
            return default_multiplier
        return weights.get(surface, default_multiplier)

    multipliers = []
    for leg_index, (start_edge, end_edge) in enumerate(zip(point_edge_indexes, point_edge_indexes[1:])):
        if start_edge is None or end_edge is None:
            multipliers.append(default_multiplier)
            continue

        if start_edge > end_edge:
            raise ValueError(
                f"Leg {leg_index}: end edge index ({end_edge}) is smaller than "
                f"start edge index ({start_edge}); edges are expected to be "
                "ordered along the matched path."
            )

        edge_slice = edges[start_edge:end_edge + 1]
        lengths = [e.get("length", 0.0) for e in edge_slice]
        total_length = sum(lengths)

        if total_length > 0:
            multiplier = sum(edge_multiplier(e) * l for e, l in zip(edge_slice, lengths)) / total_length
        else:
            multiplier = sum(edge_multiplier(e) for e in edge_slice) / len(edge_slice)

        multipliers.append(multiplier)

    return multipliers
