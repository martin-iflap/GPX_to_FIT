"""The browser's entry points into core/: every call gui/js/pyodideBridge.js makes.

The GUI talks to Python only through these functions. Each takes and returns
plain dicts, lists, numbers, strings and bytes, which Pyodide converts to and
from JS, so nothing on the JS side builds a dataclass or reads one. Dict keys
are the JS side's (camelCase), except a nearest-point candidate's, which is
`anchors.nearest_point_candidates`' own dict passed through as it is.

The parsed route is kept here between calls (`parse` sets it), so one upload
is parsed once and reused by the map clicks, the photo matching and every
conversion. So are the surface multipliers read from Valhalla's answer for
it, once the JS side has fetched one (`store_surface_response`): they depend
only on the route and the sport, so every later conversion reuses them, and a
new route clears them. The bridge runs
one call at a time, so nothing else can replace either between a surface
lookup and the conversion that uses it.
"""

import datetime as dt
from dataclasses import replace

from gpx2fit.core.activity_profile import build_activity_profile
from gpx2fit.core.fit_writer import write_fit
from gpx2fit.core.gpx_reader import MAX_LEG_M, densify_points, parse_gpx_bytes
from gpx2fit.core.models import Anchor, RawAnchor, RawPhotoAnchor, RawStop, SportType, Track
from gpx2fit.core.pacing.anchors import add_start_end_anchors, build_user_anchors, nearest_point_candidates
from gpx2fit.core.pacing.combine import combine
from gpx2fit.core.pacing.curve_selection import DEFAULT_SMOOTHNESS
from gpx2fit.core.pacing.gradient import elevation_gain
from gpx2fit.core.pacing.photo_anchors import resolve_photo_anchors
from gpx2fit.core.pacing.stops import expand_multipliers_with_stops, expand_track_with_stops, resolve_stops
from gpx2fit.core.pacing.surface import build_trace_attributes_payload, calculate_surface_multipliers

_track: Track | None = None
# Each sport's per-leg surface multipliers for _track, read from Valhalla's
# trace_attributes answer, with the warnings reading them gave. None until an
# answer is stored.
_surface: dict[SportType, tuple[list[float], list[str]]] | None = None


def _require_track() -> Track:
    if _track is None:
        raise RuntimeError("No route is loaded: call parse() first.")
    return _track


def _optional_int(value: float | None) -> int | None:
    return None if value is None else int(value)


def parse(gpx_bytes: bytes | memoryview) -> dict:
    """Parse an uploaded GPX and keep it as the route every later call uses.

    Args:
        gpx_bytes: The .gpx file's contents. A JS Uint8Array arrives as a memoryview.

    Returns:
        {"points": [{"lat", "lon"}], "summary": {"total_distance",
        "total_elevation_gain"}, "warnings": [str]}, for the map preview and
        the sidebar's stats. The points are the GPX's own, without the ones
        densifying adds: those lie on the straight lines between them, so the
        drawn line is the same, with far fewer points to send and draw.

    Raises:
        InputError: If the file isn't valid GPX or has no track points. The
            previously loaded route stays loaded, with its surface answer.
    """
    global _track, _surface
    warnings: list[str] = []
    sparse = parse_gpx_bytes(bytes(gpx_bytes), max_leg_m=None, warnings=warnings)
    # We densify only afterward because we also want the sparse points for the map since it
    # doesn't need so many points. Densifying is last step so the resulting track is the same.
    track = replace(sparse, points=densify_points(sparse.points, MAX_LEG_M))
    _track = track
    _surface = None
    return {
        "points": [{"lat": p.lat, "lon": p.lon} for p in sparse.points],
        "summary": {"total_distance": track.total_distance, "total_elevation_gain": elevation_gain(track)},
        "warnings": warnings,
    }


def nearest_candidates(lat: float, lon: float) -> list[dict]:
    """Every pass of the route near a map click, for the GUI to choose from.

    Returns:
        `anchors.nearest_point_candidates`' dicts: {"lat", "lon",
        "distance_from_start"}, one per distinct pass.
    """
    return nearest_point_candidates(_require_track(), lat, lon)


def resolve_photos(readings: list[dict]) -> list[dict]:
    """Match photos' GPS readings to the route, without an activity window.

    The GUI adds every "ok" match as an anchor and lets the activity window
    decide later whether it's used, so the "outside_activity_time" status is
    never produced here.

    Args:
        readings: [{"lat", "lon", "timestamp"}], the timestamp in ISO 8601.

    Returns:
        One dict per reading, in order: {"status", "lat", "lon",
        "distanceFromStart", "timestamp" (ISO 8601), "gapM", "passes"}.
    """
    raw = [
        RawPhotoAnchor(lat=r["lat"], lon=r["lon"], timestamp=dt.datetime.fromisoformat(r["timestamp"]))
        for r in readings
    ]
    return [
        {
            "status": r.status,
            "lat": r.lat,
            "lon": r.lon,
            "distanceFromStart": r.distance_from_start,
            "timestamp": r.timestamp.isoformat(),
            "gapM": r.gap_m,
            "passes": r.passes,
        }
        for r in resolve_photo_anchors(_require_track(), raw)
    ]


def trace_payload() -> dict | None:
    """The request body for Valhalla's trace_attributes, which the JS side sends.
    Returns:
        The body, or None when an answer for this route is already stored,
        so there is nothing to ask.
    """
    track = _require_track()
    return None if _surface is not None else build_trace_attributes_payload(track)


def store_surface_response(response: dict) -> None:
    """Read Valhalla's answer to `trace_payload()`'s request into every conversion of this route.

    Each sport's multipliers are worked out once, here, and kept, so a
    conversion only looks its sport's up. That also means an answer the
    conversion couldn't use fails now, while the JS side still treats it as
    a failed lookup, and nothing is kept. The next conversion then asks
    again.

    Args:
        response: The decoded JSON answer.
    Raises:
        Whatever reading it raises (an AttributeError or TypeError for an
        answer of the wrong shape). Nothing is kept then.
    """
    global _surface
    track = _require_track()
    surface = {}
    for sport in SportType:
        sport_warnings: list[str] = []
        multipliers = calculate_surface_multipliers(track, response, sport, warnings=sport_warnings)
        surface[sport] = (multipliers, sport_warnings)
    _surface = surface


def _raw_anchor(entry: dict) -> RawAnchor:
    return RawAnchor(
        timestamp=dt.datetime.fromisoformat(entry["timestamp"]),
        distance_from_start=entry["distanceFromStart"],
        source=entry.get("source") or "user",
    )


def _raw_stop(entry: dict) -> RawStop:
    duration_seconds = entry.get("durationSeconds")
    start_iso = entry.get("startIso")
    end_iso = entry.get("endIso")
    return RawStop(
        distance_from_start=entry["distanceFromStart"],
        duration=dt.timedelta(seconds=float(duration_seconds)) if duration_seconds is not None else None,
        start_timestamp=dt.datetime.fromisoformat(start_iso) if start_iso else None,
        end_timestamp=dt.datetime.fromisoformat(end_iso) if end_iso else None,
    )


def _by_position(anchor: Anchor) -> tuple[float, dt.datetime]:
    return anchor.distance_from_start, anchor.timestamp


def convert(request: dict) -> dict:
    """Pace the parsed route and write it as a FIT file.

    Pacing runs on a copy of the route's points, expanded with a duplicate
    point per stop, so the parsed route stays untimed for the next
    conversion.

    Args:
        request: {
            "startIso": the route's start time, ISO 8601,
            "durationSeconds": the planned total duration,
            "sport": "running" or "hiking",
            "anchors": [{"distanceFromStart", "timestamp" (ISO 8601), "source"}],
                mid-route anchors only (the start and end are added here),
            "stops": [{"distanceFromStart", and either "durationSeconds" or
                "startIso" + "endIso"}], optional,
            "device": {"name", "manufacturer", "product", "serialNumber"},
                every key optional; set on the written track only, so a
                cleared device really clears,
            "surfaceLookup": whether to pace on the stored surface answer,
                optional (absent is off); with no answer stored, the route
                paces on gradient alone either way,
            "smoothness": the pace-smoothness level 1-10, optional,
        }

    Returns:
        {"fitBytes": bytes, "profile": [{"distanceFromStart",
        "elapsedSeconds", "speedMps", "elevation", "lat", "lon", "isStop"}],
        "warnings": [str]} with the surface's warnings, then the pacing's.

    Raises:
        InputError: If the anchors and stops contradict each other.
    """
    track = _require_track()
    sport = SportType(request["sport"])
    warnings: list[str] = []

    boundary = add_start_end_anchors(
        track=track,
        start_time=dt.datetime.fromisoformat(request["startIso"]),
        duration=dt.timedelta(seconds=float(request["durationSeconds"])),
    )
    raw_anchors = [_raw_anchor(a) for a in request.get("anchors") or []]
    mid_route = build_user_anchors(track, raw_anchors, existing_anchors=boundary)
    hard_anchors = sorted(boundary + mid_route, key=_by_position)

    raw_stops = [_raw_stop(s) for s in request.get("stops") or []]
    resolved_mode_b, mode_a_stops = resolve_stops(track, raw_stops, hard_anchors)
    stop_anchors = [
        anchor
        for stop in resolved_mode_b
        for anchor in (
            Anchor(stop.distance_from_start, stop.arrival, source="stop_arrival"),
            Anchor(stop.distance_from_start, stop.departure, source="stop_departure"),
        )
    ]
    all_anchors = sorted(hard_anchors + stop_anchors, key=_by_position)
    all_stops = [*resolved_mode_b, *mode_a_stops]

    device = request.get("device") or {}
    working_track = Track(
        points=expand_track_with_stops(track.points, all_stops),
        sport=sport,
        device=device.get("name") or track.device,
        device_manufacturer=_optional_int(device.get("manufacturer")),
        device_product=_optional_int(device.get("product")),
        device_serial=_optional_int(device.get("serialNumber")),
        activity_name=track.activity_name,
        has_elevation=track.has_elevation,
    )

    working_multipliers = None
    if request.get("surfaceLookup") and _surface is not None:
        multipliers, surface_warnings = _surface[sport]
        warnings.extend(surface_warnings)
        working_multipliers = expand_multipliers_with_stops(track.points, multipliers, all_stops)

    smoothness = request.get("smoothness")
    combine(
        track=working_track,
        anchors=all_anchors,
        sport=sport,
        multipliers=working_multipliers,
        mode_a_stops=mode_a_stops,
        smoothness=DEFAULT_SMOOTHNESS if smoothness is None else int(smoothness),
        warnings=warnings,
    )

    return {
        "fitBytes": write_fit(working_track),
        "profile": [
            {
                "distanceFromStart": s.distance_from_start,
                "elapsedSeconds": s.elapsed_seconds,
                "speedMps": s.speed_mps,
                "elevation": s.elevation,
                "lat": s.lat,
                "lon": s.lon,
                "isStop": s.is_stop,
            }
            for s in build_activity_profile(working_track)
        ],
        "warnings": warnings,
    }
