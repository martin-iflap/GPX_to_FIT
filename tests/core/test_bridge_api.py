"""bridge_api: what the browser gets back from each call, and the parsed route kept between calls."""

import datetime as dt

import pytest

from gpx2fit.core import bridge_api
from gpx2fit.core.models import InputError

START_ISO = "2026-06-01T08:00:00.000Z"
START = dt.datetime.fromisoformat(START_ISO)

# Out along a meridian, about 111 m per 0.001°, then straight back.
OUT_LATS = [45.0 + i * 0.001 for i in range(11)]
OUT_AND_BACK_LATS = OUT_LATS + OUT_LATS[-2::-1]


def _gpx(lats: list[float], elevations: list[float] | None = None) -> bytes:
    elevations = elevations or [100.0] * len(lats)
    points = "".join(
        f'<trkpt lat="{lat:.6f}" lon="7.0"><ele>{ele}</ele></trkpt>' for lat, ele in zip(lats, elevations)
    )
    return (
        '<?xml version="1.0"?><gpx version="1.1" creator="Test Creator"><trk><trkseg>'
        f"{points}</trkseg></trk></gpx>"
    ).encode()


def _hilly_gpx() -> bytes:
    # About 1.1 km climbing 60 m then dropping it again, so smoothness changes the pacing.
    elevations = [100.0 + 12.0 * min(i, 10 - i) for i in range(11)]
    return _gpx(OUT_LATS, elevations)


def _request(**overrides) -> dict:
    return {"startIso": START_ISO, "durationSeconds": 900, "sport": "hiking", "anchors": [], **overrides}


def _on_route(route: dict, metres: float) -> float:
    """The distance of the route point nearest `metres`, as the GUI's map clicks always send."""
    return min((p["distance"] for p in route["points"]), key=lambda d: abs(d - metres))


@pytest.fixture(autouse=True)
def no_route(monkeypatch):
    monkeypatch.setattr(bridge_api, "_track", None)


@pytest.fixture
def straight_route() -> dict:
    return bridge_api.parse(_gpx(OUT_LATS))


class TestParse:
    def test_returns_the_preview_points_and_summary(self, straight_route):
        points = straight_route["points"]
        assert points[0] == {"lat": 45.0, "lon": 7.0, "elevation": 100.0, "distance": 0.0}
        assert points[-1]["lat"] == pytest.approx(45.01)
        assert points[-1]["distance"] == pytest.approx(straight_route["summary"]["total_distance"])
        assert straight_route["summary"]["total_distance"] == pytest.approx(1112, abs=2)
        assert straight_route["summary"]["total_elevation_gain"] == 0.0
        assert straight_route["warnings"] == []

    def test_counts_the_climb(self):
        assert bridge_api.parse(_hilly_gpx())["summary"]["total_elevation_gain"] == pytest.approx(60.0)

    def test_returns_the_parsers_warnings(self):
        # Two track segments 1.1 km apart: the gap is reported.
        two_segments = _gpx(OUT_LATS).replace(
            b"</trkseg></trk>", b'</trkseg><trkseg><trkpt lat="45.020000" lon="7.0"><ele>100.0</ele></trkpt>'
            b'<trkpt lat="45.021000" lon="7.0"><ele>100.0</ele></trkpt></trkseg></trk>'
        )
        assert len(bridge_api.parse(two_segments)["warnings"]) == 1

    def test_accepts_a_buffer_as_well_as_bytes(self):
        # Pyodide hands a JS Uint8Array over as a memoryview.
        assert bridge_api.parse(memoryview(_gpx(OUT_LATS)))["points"]

    def test_a_bad_file_raises_an_input_error_and_keeps_the_route_already_loaded(self, straight_route):
        with pytest.raises(InputError):
            bridge_api.parse(b"not a gpx file")
        assert len(bridge_api.trace_payload()["shape"]) > 0
        assert bridge_api.nearest_candidates(45.005, 7.0)


class TestBeforeAnyRoute:
    @pytest.mark.parametrize(
        "call",
        [
            lambda: bridge_api.nearest_candidates(45.0, 7.0),
            lambda: bridge_api.resolve_photos([]),
            bridge_api.trace_payload,
            lambda: bridge_api.surface_multipliers({}, "hiking"),
            lambda: bridge_api.convert(_request()),
        ],
    )
    def test_every_call_needs_a_parsed_route(self, call):
        with pytest.raises(RuntimeError, match="parse"):
            call()


class TestNearestCandidates:
    def test_finds_both_passes_of_an_out_and_back(self):
        bridge_api.parse(_gpx(OUT_AND_BACK_LATS))
        candidates = bridge_api.nearest_candidates(45.005, 7.0)
        assert len(candidates) == 2
        assert set(candidates[0]) == {"lat", "lon", "distance_from_start"}
        first, second = sorted(c["distance_from_start"] for c in candidates)
        assert first == pytest.approx(556, abs=2)
        assert second == pytest.approx(1668, abs=2)


class TestResolvePhotos:
    def test_reports_each_reading_in_the_browsers_shape(self, straight_route):
        results = bridge_api.resolve_photos(
            [
                {"lat": 45.005, "lon": 7.0, "timestamp": "2026-06-01T08:05:00.000Z"},
                {"lat": 46.0, "lon": 7.0, "timestamp": "2026-06-01T08:06:00.000Z"},
            ]
        )
        ok, far = results
        assert ok["status"] == "ok"
        assert ok["distanceFromStart"] == pytest.approx(556, abs=2)
        assert ok["gapM"] == pytest.approx(0.0, abs=1)
        assert dt.datetime.fromisoformat(ok["timestamp"]) == START + dt.timedelta(minutes=5)
        assert set(ok) == {"status", "lat", "lon", "distanceFromStart", "timestamp", "gapM", "passes"}
        assert far["status"] == "too_far"

    def test_never_judges_the_activity_window(self, straight_route):
        # A year before any start: still a match, since the GUI decides that later.
        [result] = bridge_api.resolve_photos([{"lat": 45.005, "lon": 7.0, "timestamp": "2025-06-01T08:05:00Z"}])
        assert result["status"] == "ok"


class TestSurface:
    def test_trace_payload_is_the_valhalla_request(self, straight_route):
        payload = bridge_api.trace_payload()
        assert payload["shape"][0] == {"lat": 45.0, "lon": 7.0}
        assert "costing" in payload

    def test_multipliers_cover_every_leg_and_bring_their_warnings(self, straight_route):
        shape_count = len(bridge_api.trace_payload()["shape"])
        unmatched = {"edges": [], "matched_points": [{"type": "unmatched"}] * shape_count}

        result = bridge_api.surface_multipliers(unmatched, "hiking")

        assert len(result["multipliers"]) == len(straight_route["points"]) - 1
        assert set(result["multipliers"]) == {1.0}
        assert len(result["warnings"]) == 1


class TestConvert:
    def test_writes_a_fit_file_and_a_profile_spanning_the_duration(self, straight_route):
        result = bridge_api.convert(_request())

        assert result["fitBytes"][8:12] == b".FIT"
        profile = result["profile"]
        assert profile[0]["elapsedSeconds"] == 0.0
        assert profile[-1]["elapsedSeconds"] == pytest.approx(900.0)
        assert profile[-1]["distanceFromStart"] == pytest.approx(straight_route["summary"]["total_distance"])
        keys = {"distanceFromStart", "elapsedSeconds", "speedMps", "elevation", "lat", "lon", "isStop"}
        assert set(profile[0]) == keys
        assert result["warnings"] == []

    def test_leaves_the_parsed_route_untimed_for_the_next_conversion(self, straight_route):
        bridge_api.convert(_request())
        assert bridge_api._track is not None
        assert all(p.timestamp is None for p in bridge_api._track.points)
        assert bridge_api.convert(_request(durationSeconds=600))["profile"][-1]["elapsedSeconds"] == pytest.approx(600)

    def test_a_mid_route_anchor_fixes_the_time_there(self, straight_route):
        at = _on_route(straight_route, 556.0)
        anchor = {"distanceFromStart": at, "timestamp": "2026-06-01T08:10:00.000Z", "source": "user"}
        profile = bridge_api.convert(_request(anchors=[anchor]))["profile"]
        # The profile is downsampled, so read the time at the first sample past the anchor.
        after = next(s for s in profile if s["distanceFromStart"] >= at)
        assert after["elapsedSeconds"] == pytest.approx(600, abs=15)

    def test_stops_of_both_kinds_become_pauses(self, straight_route):
        stops = [
            {"distanceFromStart": _on_route(straight_route, 300.0), "durationSeconds": 120},
            {
                "distanceFromStart": _on_route(straight_route, 800.0),
                "startIso": "2026-06-01T08:10:00Z",
                "endIso": "2026-06-01T08:12:00Z",
            },
        ]
        profile = bridge_api.convert(_request(stops=stops))["profile"]
        assert sum(s["isStop"] for s in profile) == 4
        assert profile[-1]["elapsedSeconds"] == pytest.approx(900.0)

    def test_an_anchor_keeps_its_source(self, straight_route, monkeypatch):
        seen = []
        real_build_user_anchors = bridge_api.build_user_anchors

        def build_user_anchors(track, raw_anchors, existing_anchors=None):
            seen.extend(raw_anchors)
            return real_build_user_anchors(track, raw_anchors, existing_anchors=existing_anchors)

        monkeypatch.setattr(bridge_api, "build_user_anchors", build_user_anchors)
        at = _on_route(straight_route, 556.0)
        anchors = [
            {"distanceFromStart": at, "timestamp": "2026-06-01T08:07:00Z", "source": "photo"},
            {"distanceFromStart": _on_route(straight_route, 800.0), "timestamp": "2026-06-01T08:10:00Z"},
        ]
        bridge_api.convert(_request(anchors=anchors))
        assert [a.source for a in seen] == ["photo", "user"]

    def test_an_anchor_on_the_routes_start_is_an_input_error(self, straight_route):
        anchor = {"distanceFromStart": 0.0, "timestamp": "2026-06-01T08:05:00Z", "source": "user"}
        with pytest.raises(InputError, match="start"):
            bridge_api.convert(_request(anchors=[anchor]))

    def test_returns_the_pacings_warnings(self, straight_route):
        # Half the route in one minute and the other half in fourteen: implausible.
        anchor = {"distanceFromStart": _on_route(straight_route, 556.0), "timestamp": "2026-06-01T08:01:00Z"}
        assert len(bridge_api.convert(_request(anchors=[anchor]))["warnings"]) >= 1

    def test_a_route_without_elevation_has_none_in_its_profile(self):
        no_elevation = _gpx(OUT_LATS).replace(b"<ele>100.0</ele>", b"")
        bridge_api.parse(no_elevation)
        profile = bridge_api.convert(_request())["profile"]
        assert {s["elevation"] for s in profile} == {None}

    def test_contradictory_anchors_are_an_input_error(self, straight_route):
        anchors = [
            {"distanceFromStart": _on_route(straight_route, metres), "timestamp": timestamp, "source": "user"}
            for metres, timestamp in ((300.0, "2026-06-01T08:10:00Z"), (600.0, "2026-06-01T08:05:00Z"))
        ]
        with pytest.raises(InputError):
            bridge_api.convert(_request(anchors=anchors))

    def test_the_sport_is_the_guis_own_name(self, straight_route):
        bridge_api.convert(_request(sport="running"))
        with pytest.raises(ValueError):
            bridge_api.convert(_request(sport="RUNNING"))

    def test_the_device_is_written_but_not_kept_on_the_route(self, straight_route, monkeypatch):
        written = []
        real_write_fit = bridge_api.write_fit
        monkeypatch.setattr(bridge_api, "write_fit", lambda track: written.append(track) or real_write_fit(track))

        device = {"name": "Garmin Forerunner 965", "manufacturer": 1, "product": 4315, "serialNumber": 42}
        bridge_api.convert(_request(device=device))
        bridge_api.convert(_request())

        with_device, without = written
        assert (with_device.device, with_device.device_manufacturer, with_device.device_product) == (
            "Garmin Forerunner 965",
            1,
            4315,
        )
        assert with_device.device_serial == 42
        assert (without.device, without.device_manufacturer, without.device_serial) == ("Test Creator", None, None)

    def test_smoothness_reaches_the_pacing(self):
        bridge_api.parse(_hilly_gpx())
        speeds = {
            level: [s["speedMps"] for s in bridge_api.convert(_request(smoothness=level))["profile"]]
            for level in (1, 5, 10)
        }
        assert speeds[1] != speeds[5] != speeds[10]
        assert bridge_api.convert(_request())["profile"] == bridge_api.convert(_request(smoothness=5))["profile"]

    def test_surface_multipliers_slow_the_legs_they_cover(self, straight_route):
        legs = len(straight_route["points"]) - 1
        half = legs // 2
        slow_second_half = [1.0] * half + [0.5] * (legs - half)

        profile = bridge_api.convert(_request(surfaceMultipliers=slow_second_half, stops=[]))["profile"]

        early = [s["speedMps"] for s in profile if 100 < s["distanceFromStart"] < 450]
        late = [s["speedMps"] for s in profile if 700 < s["distanceFromStart"] < 1050]
        assert sum(late) / len(late) < 0.7 * sum(early) / len(early)

    def test_surface_multipliers_are_expanded_for_stops(self, straight_route):
        legs = len(straight_route["points"]) - 1
        result = bridge_api.convert(
            _request(
                surfaceMultipliers=[1.0] * legs,
                stops=[{"distanceFromStart": _on_route(straight_route, 300.0), "durationSeconds": 60}],
            )
        )
        assert result["profile"][-1]["elapsedSeconds"] == pytest.approx(900.0)
