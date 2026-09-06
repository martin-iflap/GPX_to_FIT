import math

import pytest

from gpx2fit.core.gpx_reader import parse_gpx_bytes


def _gpx(trkpts: str, creator: str | None = "Test Creator") -> bytes:
    creator_attr = f' creator="{creator}"' if creator else ""
    xml = f"""<?xml version="1.0"?>
<gpx version="1.1"{creator_attr}>
<trk><trkseg>
{trkpts}
</trkseg></trk>
</gpx>"""
    return xml.encode("utf-8")


def _trkpt(lat: float, lon: float, ele: float | None = None) -> str:
    if ele is None:
        return f'<trkpt lat="{lat}" lon="{lon}"></trkpt>'
    return f'<trkpt lat="{lat}" lon="{lon}"><ele>{ele}</ele></trkpt>'


def _haversine_m(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    r = 6371000.0
    phi1, phi2 = math.radians(lat_a), math.radians(lat_b)
    d_phi = math.radians(lat_b - lat_a)
    d_lambda = math.radians(lon_b - lon_a)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class TestBasicParsing:
    def test_returns_one_point_per_trkpt_in_order(self):
        gpx_bytes = _gpx(
            _trkpt(45.0, 7.0, 100.0) + _trkpt(45.001, 7.0, 110.0) + _trkpt(45.002, 7.0, 120.0)
        )
        track = parse_gpx_bytes(gpx_bytes)
        assert [(p.lat, p.lon, p.elevation) for p in track.points] == [
            (45.0, 7.0, 100.0),
            (45.001, 7.0, 110.0),
            (45.002, 7.0, 120.0),
        ]

    def test_first_point_has_zero_distance_from_start(self):
        gpx_bytes = _gpx(_trkpt(45.0, 7.0, 100.0) + _trkpt(45.001, 7.0, 110.0))
        track = parse_gpx_bytes(gpx_bytes)
        assert track.points[0].distance_from_start == 0.0

    def test_distance_from_start_is_cumulative_and_matches_great_circle_distance(self):
        lats = [45.0, 45.001, 45.003]
        gpx_bytes = _gpx("".join(_trkpt(lat, 7.0, 0.0) for lat in lats))
        track = parse_gpx_bytes(gpx_bytes)

        expected_leg_1 = _haversine_m(lats[0], 7.0, lats[1], 7.0)
        expected_leg_2 = _haversine_m(lats[1], 7.0, lats[2], 7.0)

        assert track.points[1].distance_from_start == pytest.approx(expected_leg_1, rel=0.01)
        assert track.points[2].distance_from_start == pytest.approx(expected_leg_1 + expected_leg_2, rel=0.01)

    def test_no_points_yields_empty_track(self):
        gpx_bytes = _gpx("")
        track = parse_gpx_bytes(gpx_bytes)
        assert track.points == []


class TestDevice:
    def test_falls_back_to_gpx_creator_when_no_device_given(self):
        gpx_bytes = _gpx(_trkpt(45.0, 7.0, 100.0), creator="Garmin Connect")
        track = parse_gpx_bytes(gpx_bytes)
        assert track.device == "Garmin Connect"

    def test_explicit_device_overrides_creator(self):
        gpx_bytes = _gpx(_trkpt(45.0, 7.0, 100.0), creator="Garmin Connect")
        track = parse_gpx_bytes(gpx_bytes, device="My Watch")
        assert track.device == "My Watch"

    def test_no_creator_and_no_device_is_none(self):
        gpx_bytes = _gpx(_trkpt(45.0, 7.0, 100.0), creator=None)
        track = parse_gpx_bytes(gpx_bytes)
        assert track.device is None


class TestMissingElevation:
    def test_first_point_missing_elevation_defaults_to_zero(self):
        gpx_bytes = _gpx(_trkpt(45.0, 7.0, ele=None) + _trkpt(45.001, 7.0, 50.0))
        track = parse_gpx_bytes(gpx_bytes)
        assert track.points[0].elevation == 0.0

    def test_missing_elevation_carries_forward_previous_point(self):
        gpx_bytes = _gpx(
            _trkpt(45.0, 7.0, 100.0) + _trkpt(45.001, 7.0, ele=None) + _trkpt(45.002, 7.0, ele=None)
        )
        track = parse_gpx_bytes(gpx_bytes)
        assert [p.elevation for p in track.points] == [100.0, 100.0, 100.0]

    def test_elevation_resumes_from_real_value_after_gap(self):
        gpx_bytes = _gpx(
            _trkpt(45.0, 7.0, 100.0) + _trkpt(45.001, 7.0, ele=None) + _trkpt(45.002, 7.0, 130.0)
        )
        track = parse_gpx_bytes(gpx_bytes)
        assert [p.elevation for p in track.points] == [100.0, 100.0, 130.0]
