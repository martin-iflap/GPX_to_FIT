import pytest

from gpx2fit.core.gpx_reader import parse_gpx_bytes
from gpx2fit.core.models import InputError
from tests.core.conftest import haversine_m


def _gpx(trkpts: str, creator: str | None = "Test Creator") -> bytes:
    creator_attr = f' creator="{creator}"' if creator else ""
    xml = f"""<?xml version="1.0"?>
<gpx version="1.1"{creator_attr}>
<trk><trkseg>
{trkpts}
</trkseg></trk>
</gpx>"""
    return xml.encode("utf-8")


def _trkpt(lat: float, lon: float, ele: float | str | None = None) -> str:
    if ele is None:
        return f'<trkpt lat="{lat}" lon="{lon}"></trkpt>'
    return f'<trkpt lat="{lat}" lon="{lon}"><ele>{ele}</ele></trkpt>'


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

        expected_leg_1 = haversine_m(lats[0], 7.0, lats[1], 7.0)
        expected_leg_2 = haversine_m(lats[1], 7.0, lats[2], 7.0)

        assert track.points[1].distance_from_start == pytest.approx(expected_leg_1, rel=0.01)
        assert track.points[2].distance_from_start == pytest.approx(expected_leg_1 + expected_leg_2, rel=0.01)

    def test_no_points_raises_input_error(self):
        gpx_bytes = _gpx("")
        with pytest.raises(InputError):
            parse_gpx_bytes(gpx_bytes)

    def test_single_point_raises_input_error(self):
        with pytest.raises(InputError, match="only one point"):
            parse_gpx_bytes(_gpx(_trkpt(45.0, 7.0, 100.0)))

    def test_route_without_length_raises_input_error(self):
        # Every point in the same place: nothing to pace along.
        gpx_bytes = _gpx(_trkpt(45.0, 7.0, 100.0) * 3)
        with pytest.raises(InputError, match="no length"):
            parse_gpx_bytes(gpx_bytes)

    def test_invalid_utf8_raises_input_error(self):
        with pytest.raises(InputError):
            parse_gpx_bytes(b"\xff\xfe\x00\x01")

    def test_unparseable_xml_raises_input_error(self):
        with pytest.raises(InputError):
            parse_gpx_bytes(b"this is not gpx or xml at all")


_TWO_POINTS = _trkpt(45.0, 7.0, 100.0) + _trkpt(45.001, 7.0, 100.0)


class TestDevice:
    def test_falls_back_to_gpx_creator_when_no_device_given(self):
        gpx_bytes = _gpx(_TWO_POINTS, creator="Garmin Connect")
        track = parse_gpx_bytes(gpx_bytes)
        assert track.device == "Garmin Connect"

    def test_explicit_device_overrides_creator(self):
        gpx_bytes = _gpx(_TWO_POINTS, creator="Garmin Connect")
        track = parse_gpx_bytes(gpx_bytes, device="My Watch")
        assert track.device == "My Watch"

    def test_no_creator_and_no_device_is_none(self):
        gpx_bytes = _gpx(_TWO_POINTS, creator=None)
        track = parse_gpx_bytes(gpx_bytes)
        assert track.device is None


class TestMissingElevation:
    def test_leading_missing_elevation_is_back_filled_from_the_first_known_value(self):
        # Not 0.0, which would put a fake cliff where the real elevation starts.
        gpx_bytes = _gpx(_trkpt(45.0, 7.0, ele=None) + _trkpt(45.001, 7.0, ele=None) + _trkpt(45.002, 7.0, 50.0))
        track = parse_gpx_bytes(gpx_bytes)
        assert [p.elevation for p in track.points] == [50.0, 50.0, 50.0]
        assert track.has_elevation

    def test_no_elevation_anywhere_is_zero_and_flagged(self):
        gpx_bytes = _gpx(_trkpt(45.0, 7.0, ele=None) + _trkpt(45.001, 7.0, ele=None))
        track = parse_gpx_bytes(gpx_bytes)
        assert [p.elevation for p in track.points] == [0.0, 0.0]
        assert not track.has_elevation

    def test_sea_level_is_real_elevation(self):
        gpx_bytes = _gpx(_trkpt(45.0, 7.0, 0.0) + _trkpt(45.001, 7.0, 0.0))
        assert parse_gpx_bytes(gpx_bytes).has_elevation

    @pytest.mark.parametrize("sentinel", [-32768.0, 99999.0, "nan"])
    def test_implausible_elevation_is_treated_as_missing(self, sentinel):
        # A -32768 / 99999 "no data" sentinel would otherwise read as a cliff
        # steep enough to underflow the speed curves.
        gpx_bytes = _gpx(
            _trkpt(45.0, 7.0, 100.0) + _trkpt(45.001, 7.0, sentinel) + _trkpt(45.002, 7.0, 110.0)
        )
        track = parse_gpx_bytes(gpx_bytes)
        assert [p.elevation for p in track.points] == [100.0, 100.0, 110.0]

    def test_leading_implausible_elevation_is_back_filled(self):
        gpx_bytes = _gpx(_trkpt(45.0, 7.0, -32768.0) + _trkpt(45.001, 7.0, 20.0))
        track = parse_gpx_bytes(gpx_bytes)
        assert [p.elevation for p in track.points] == [20.0, 20.0]

    def test_only_implausible_elevations_count_as_no_elevation(self):
        gpx_bytes = _gpx(_trkpt(45.0, 7.0, -32768.0) + _trkpt(45.001, 7.0, -32768.0))
        assert not parse_gpx_bytes(gpx_bytes).has_elevation

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


def _multi_gpx(*tracks: list[list[tuple[float, float]]]) -> bytes:
    """A GPX with one <trk> per argument, each a list of <trkseg>s, each a list of (lat, lon)."""
    body = "".join(
        "<trk>" + "".join(
            "<trkseg>" + "".join(_trkpt(lat, lon, 100.0) for lat, lon in segment) + "</trkseg>"
            for segment in segments
        ) + "</trk>"
        for segments in tracks
    )
    return f'<?xml version="1.0"?>\n<gpx version="1.1" creator="Test">{body}</gpx>'.encode("utf-8")


# Two short north-going segments. The second starts `gap_deg` of latitude past the end of the first.
def _two_segments(gap_deg: float) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    first = [(45.0, 7.0), (45.001, 7.0), (45.002, 7.0)]
    start = 45.002 + gap_deg
    second = [(start, 7.0), (start + 0.001, 7.0)]
    return first, second


class TestSegmentGapWarning:
    def test_a_wide_gap_between_trksegs_warns_with_its_width(self):
        first, second = _two_segments(0.0126)  # ~1.40 km
        warnings: list[str] = []

        parse_gpx_bytes(_multi_gpx([first, second]), warnings=warnings)

        gap_km = haversine_m(45.002, 7.0, 45.0146, 7.0) / 1000
        assert f"{gap_km:.2f}" == "1.40"
        assert len(warnings) == 1
        assert "1 gap " in warnings[0]
        assert "1.40 km" in warnings[0]
        assert "straight line" in warnings[0]

    def test_a_gap_between_two_trks_warns_too(self):
        first, second = _two_segments(0.0126)
        warnings: list[str] = []

        parse_gpx_bytes(_multi_gpx([first], [second]), warnings=warnings)

        assert len(warnings) == 1
        assert "1.40 km" in warnings[0]

    def test_several_gaps_are_counted_in_one_warning_naming_the_widest(self):
        segments = [
            [(45.0, 7.0), (45.001, 7.0)],
            [(45.005, 7.0), (45.006, 7.0)],  # ~445 m after the first
            [(45.0186, 7.0), (45.0196, 7.0)],  # ~1.40 km after the second
        ]
        warnings: list[str] = []

        parse_gpx_bytes(_multi_gpx(segments), warnings=warnings)

        assert len(warnings) == 1
        assert "2 gaps " in warnings[0]
        assert "1.40 km" in warnings[0]

    def test_contiguous_segments_do_not_warn(self):
        # A paused recording's usual export: the next segment starts where the last one ended.
        first, second = _two_segments(0.0)
        warnings: list[str] = []

        parse_gpx_bytes(_multi_gpx([first, second]), warnings=warnings)

        assert warnings == []

    def test_a_short_gap_does_not_warn(self):
        first, second = _two_segments(0.00054)  # ~60 m
        warnings: list[str] = []

        parse_gpx_bytes(_multi_gpx([first, second]), warnings=warnings)

        assert warnings == []

    def test_a_long_leg_inside_one_segment_is_not_a_gap(self):
        # Planners write long straight legs on purpose; only the joins between segments are suspect.
        warnings: list[str] = []

        parse_gpx_bytes(_multi_gpx([[(45.0, 7.0), (45.02, 7.0)]]), warnings=warnings)

        assert warnings == []

    def test_the_join_still_counts_toward_the_distance(self):
        first, second = _two_segments(0.0126)

        track = parse_gpx_bytes(_multi_gpx([first, second]))

        assert track.total_distance == pytest.approx(haversine_m(45.0, 7.0, 45.0156, 7.0), rel=0.01)
