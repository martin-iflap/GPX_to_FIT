"""write_fit's whole file against the frozen fit_tool encoding (fit_tool_reference.py).

The same decided content (_build_content) is encoded by write_fit and by the
oracle, and the bytes must be identical, over tracks chosen to reach every
branch: stops, rounding at the edges, no elevation, device fields, both
sports, both hemispheres, extreme altitudes, and a long paced hike.
"""

import random
from datetime import datetime, timedelta

import pytest
from fit_tool.data_message import DataMessage
from fit_tool.fit_file import FitFile

from gpx2fit.core.fit_writer import _build_content, write_fit
from gpx2fit.core.models import SportType, Track
from gpx2fit.core.pacing.anchors import add_start_end_anchors
from gpx2fit.core.pacing.combine import combine
from tests.core import fit_tool_reference
from tests.core.conftest import point, rolling_track

_T0 = datetime(2024, 1, 1, 8, 0, 0)


def _at(seconds: float) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def _simple() -> Track:
    return Track(points=[
        point(lat=45.0, lon=7.0, elevation=100.0, distance_from_start=0.0, timestamp=_at(0)),
        point(lat=45.001, lon=7.0, elevation=0.0, distance_from_start=100.0, timestamp=_at(30)),
        point(lat=45.002, lon=7.0, elevation=120.0, distance_from_start=250.0, timestamp=_at(60)),
    ], sport=SportType.RUNNING)


def _with_stops() -> Track:
    """Two stops, one of them shorter than half a second, and fractional times throughout."""
    return Track(points=[
        point(lat=46.0, lon=8.0, elevation=500.0, distance_from_start=0.0, timestamp=_at(0.4)),
        point(lat=46.001, lon=8.0, elevation=510.0, distance_from_start=100.0, timestamp=_at(30.4)),
        point(lat=46.001, lon=8.0, elevation=510.0, distance_from_start=100.0, timestamp=_at(90.6)),
        point(lat=46.002, lon=8.0, elevation=505.0, distance_from_start=200.0, timestamp=_at(120.1)),
        point(lat=46.002, lon=8.0, elevation=505.0, distance_from_start=200.0, timestamp=_at(120.3)),
        point(lat=46.003, lon=8.001, elevation=530.0, distance_from_start=330.0, timestamp=_at(600.0)),
        point(lat=46.003, lon=8.001, elevation=530.0, distance_from_start=330.0, timestamp=_at(1500.0)),
        point(lat=46.004, lon=8.002, elevation=520.0, distance_from_start=480.0, timestamp=_at(1610.7)),
    ], sport=SportType.HIKING)


def _southern_western() -> Track:
    """Below sea level near the start, south of the equator and west of Greenwich."""
    return Track(points=[
        point(lat=-33.8688 - i * 1e-4, lon=-70.6693 - i * 2e-4, elevation=-430.5 + i * 3.7,
              distance_from_start=i * 21.3, timestamp=_at(i * 7.3))
        for i in range(60)
    ], sport=None)


def _everest() -> Track:
    return Track(points=[
        point(lat=27.9881 + i * 1e-5, lon=86.9250, elevation=8848.86 - i * 0.4,
              distance_from_start=i * 2.5, timestamp=_at(i * 4.0))
        for i in range(40)
    ], sport=SportType.HIKING)


def _dense() -> Track:
    """A point every metre, 0.2 s apart: several points to each record."""
    return Track(points=[
        point(lat=45.0 + i * 9e-6, lon=7.0, elevation=200.0, distance_from_start=float(i), timestamp=_at(i * 0.2))
        for i in range(101)
    ], sport=SportType.RUNNING)


def _random_walk(seed: int, points: int = 400) -> Track:
    """Uneven legs and pauses at random, the shape a planner export plus stops takes."""
    rng = random.Random(seed)
    pts, distance, seconds, lat, lon = [], 0.0, rng.uniform(0, 1), 47.0, 11.0
    for index in range(points):
        if index and rng.random() < 0.02:
            seconds += rng.choice([0.2, 0.6, 45.0, 1200.5])
        else:
            leg = rng.uniform(0.5, 40.0)
            distance += leg
            seconds += leg / rng.uniform(0.8, 4.5)
            lat += leg * rng.uniform(-6e-6, 6e-6)
            lon += leg * rng.uniform(-9e-6, 9e-6)
        pts.append(point(lat=lat, lon=lon, elevation=900.0 + 80.0 * rng.random(),
                         distance_from_start=distance, timestamp=_at(seconds)))
    return Track(points=pts, sport=rng.choice([SportType.RUNNING, SportType.HIKING]))


def _paced_hike() -> Track:
    """15 km, 4 h, paced by the real model: about 14,400 records at 1 Hz."""
    track = rolling_track(15000.0, climb_per_km=60.0, leg_m=20.0)
    for index, pt in enumerate(track.points):
        pt.lat = 46.5 + index * 1.2e-4
        pt.lon = 9.8 + (index % 50) * 4e-5
        pt.elevation += 1500.0
    track.sport = SportType.HIKING
    anchors = add_start_end_anchors(track, _T0, duration=timedelta(hours=4))
    return combine(track, anchors, SportType.HIKING)


def _with_device(track: Track, *, manufacturer: int | None, product: int | None, serial: int | None,
                 name: str | None) -> Track:
    track.device_manufacturer = manufacturer
    track.device_product = product
    track.device_serial = serial
    track.device = name
    return track


def _without_elevation(track: Track) -> Track:
    track.has_elevation = False
    return track


_TRACKS = {
    "simple": _simple,
    "stops": _with_stops,
    "southern_western_below_sea_level": _southern_western,
    "everest": _everest,
    "dense": _dense,
    "no_elevation": lambda: _without_elevation(_with_stops()),
    "garmin_with_name": lambda: _with_device(_simple(), manufacturer=1, product=3076, serial=None,
                                             name="Forerunner 245"),
    "wahoo_with_serial_unicode_name": lambda: _with_device(_with_stops(), manufacturer=32, product=43,
                                                           serial=123456789, name="Fēnix ✓ – über"),
    "manufacturer_without_product": lambda: _with_device(_simple(), manufacturer=294, product=None,
                                                         serial=None, name=None),
    "long_name": lambda: _with_device(_simple(), manufacturer=None, product=None, serial=None, name="ü" * 50),
    **{f"random_{seed}": (lambda seed=seed: _random_walk(seed)) for seed in range(6)},
}


@pytest.mark.parametrize("resample", [True, False], ids=["1hz", "per_point"])
@pytest.mark.parametrize("name", list(_TRACKS))
def test_write_fit_matches_the_fit_tool_encoding(name, resample):
    track = _TRACKS[name]()
    _assert_same_file(write_fit(track, resample=resample),
                      fit_tool_reference.encode(_build_content(track, resample=resample)))


def test_a_long_paced_hike_matches_the_fit_tool_encoding():
    track = _paced_hike()
    ours = write_fit(track, record_interval_s=1)
    assert sum(1 for record in FitFile.from_bytes(ours).records if record.message.global_id == 20) > 14000
    _assert_same_file(ours, fit_tool_reference.encode(_build_content(track, resample=True, record_interval_s=1)))


@pytest.mark.parametrize("name", list(_TRACKS))
def test_output_decodes_with_a_valid_crc(name):
    FitFile.from_bytes(write_fit(_TRACKS[name]()), check_crc=True)


def test_core_never_imports_fit_tool():
    # core/ runs in Pyodide, where fit_tool is no longer installed.
    import ast
    from pathlib import Path

    import gpx2fit.core

    offenders = []
    for path in Path(gpx2fit.core.__file__).parent.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else (
                [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            offenders += [f"{path.name}: {name}" for name in names if name.split(".")[0] == "fit_tool"]
    assert offenders == []


def _assert_same_file(ours: bytes, theirs: bytes) -> None:
    """Byte equality; on failure, name the first message and field that differ."""
    if ours == theirs:
        return
    ours_messages = _data_messages(ours)
    their_messages = _data_messages(theirs)
    for index, (mine, reference) in enumerate(zip(ours_messages, their_messages)):
        mine_fields = {field.name: field.get_values() for field in mine.fields if field.is_valid()}
        their_fields = {field.name: field.get_values() for field in reference.fields if field.is_valid()}
        if type(mine) is not type(reference) or mine_fields != their_fields:
            pytest.fail(f"message {index} differs: ours {type(mine).__name__} {mine_fields}, "
                        f"fit_tool {type(reference).__name__} {their_fields}")
    first = next((i for i, (a, b) in enumerate(zip(ours, theirs)) if a != b), min(len(ours), len(theirs)))
    pytest.fail(f"decoded messages agree ({len(ours_messages)} vs {len(their_messages)}), but the bytes differ "
                f"from offset {first} (ours {len(ours)} B, fit_tool {len(theirs)} B)")


def _data_messages(data: bytes) -> list[DataMessage]:
    return [record.message for record in FitFile.from_bytes(data, check_crc=False).records
            if isinstance(record.message, DataMessage)]
