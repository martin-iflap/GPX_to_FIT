"""Replace a recording's missing or noisy elevation with DEM heights from Valhalla.

Production input is a planned GPX whose `<ele>` came from a DEM, rounded to
whole metres. `prepare.py` only *emulates* that by rounding the recorded
elevation. That works for a barometric trace, but two kinds of recording
break it:

- **Missing.** Some exports carry no altitude at all, so every point is at the
  0.0 sentinel and the activity can't be paced on gradient.
- **Noisy.** Phone-GPS altitude swings tens of metres while standing still.
  The 70 m smoothing window can't rescue that: it turns into phantom climbs
  and ±99% gradient buckets.

Both have perfectly good positions and timing, so the fix is to take elevation
from where production takes it: a DEM. Valhalla's `/height` (the same public
server the app already uses for surface) returns whole-metre heights for a list
of positions, which makes the emulation exact rather than approximate.

**This sends the athlete's recorded positions to a third party**, so it only
runs when the caller asks for it (`--dem-elevation`). Clean recordings are
never sent. Each file is fetched once: the heights are cached next to the
decode cache, keyed by the file's content hash, so later runs make no request.
Requests are spaced at least `_MIN_REQUEST_INTERVAL_S` apart, since the public
server allows about one per second.
"""

import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

import numpy as np

from tuning.fit_reader import RECORD_COLUMNS, DecodedFit, UnreadableActivity

DEFAULT_CACHE_DIR = Path("tuning_out") / "elevation"

_HEIGHT_URL = "https://valhalla1.openstreetmap.de/height"
_USER_AGENT = "gpx2fit-tuning (development harness; one request per activity, cached)"

# The public server's usage policy is about one request per second. The margin
# keeps clock jitter from tipping two requests under it.
_MIN_REQUEST_INTERVAL_S = 1.1

# Points per request. The server took 20,000 in one go when probed, but a
# smaller body is kinder to a free service and a failed chunk costs less.
_CHUNK_POINTS = 5000

# A failed request (a 429, a 5xx, a timeout) is retried after a growing pause.
_RETRIES = 3
_RETRY_BACKOFF_S = 5.0
_TIMEOUT_S = 60.0

# Bump if what is cached changes meaning (a different DEM source, say), so old
# entries are refetched rather than silently mixed in.
_FORMAT_VERSION = 1

# More than this share of points at the 0.0 sentinel means the file has no
# elevation. Matches prepare.py's own gate.
_MAX_SENTINEL_SHARE = 0.5

# Median |Δelevation| per metre traveled, over legs of at least
# `_MIN_NOISE_LEG_M`. Barometric recordings measured 0.14–0.17 on this corpus
# and phone-GPS altitude 5.1–6.5, so anything in between is a clear split.
NOISY_ELEVATION_RATIO = 1.0
_MIN_NOISE_LEG_M = 0.5

_EARTH_RADIUS_M = 6_371_000.0

_last_request_at = -math.inf


def _leg_lengths(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Haversine length of each leg, in metres."""
    phi = np.radians(lat)
    d_phi = np.diff(phi)
    d_lambda = np.radians(np.diff(lon))
    a = np.sin(d_phi / 2) ** 2 + np.cos(phi[:-1]) * np.cos(phi[1:]) * np.sin(d_lambda / 2) ** 2
    return 2 * _EARTH_RADIUS_M * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def elevation_noise(lat: np.ndarray, lon: np.ndarray, elevation: np.ndarray) -> float:
    """Median |Δelevation| per metre traveled, the measure `NOISY_ELEVATION_RATIO` is set against.

    A median of per-leg ratios rather than total ascent over distance, so a
    genuinely steep stretch can't be mistaken for noise: noise shows up on
    every leg, a climb only on some. Legs shorter than `_MIN_NOISE_LEG_M` are
    left out, since standing still makes the ratio meaningless.
    """
    lengths = _leg_lengths(lat, lon)
    moving = lengths >= _MIN_NOISE_LEG_M
    if not moving.any():
        return 0.0
    return float(np.median(np.abs(np.diff(elevation))[moving] / lengths[moving]))


def elevation_problem(lat: np.ndarray, lon: np.ndarray, elevation: np.ndarray) -> str | None:
    """Why this recording's elevation can't be used, or None if it can.

    Args:
        lat: Position in degrees, one per point.
        lon: Position in degrees, one per point.
        elevation: Elevation in metres. NaN or 0.0 means "not recorded".
    Returns:
        "missing", "noisy", or None.
    """
    recorded = np.nan_to_num(elevation, nan=0.0)
    if len(recorded) == 0 or np.count_nonzero(recorded == 0.0) > _MAX_SENTINEL_SHARE * len(recorded):
        return "missing"
    if len(np.unique(np.round(recorded))) < 2:
        return "missing"
    if elevation_noise(lat, lon, recorded) > NOISY_ELEVATION_RATIO:
        return "noisy"
    return None


def _wait_for_rate_limit() -> None:
    """Sleep until at least `_MIN_REQUEST_INTERVAL_S` has passed since the last request."""
    global _last_request_at
    remaining = _last_request_at + _MIN_REQUEST_INTERVAL_S - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)
    _last_request_at = time.monotonic()


def _request_heights(lat: np.ndarray, lon: np.ndarray, name: str) -> np.ndarray:
    """One `/height` request, retried on failure. Heights in whole metres."""
    body = json.dumps({
        "shape": [{"lat": round(float(a), 6), "lon": round(float(b), 6)} for a, b in zip(lat, lon)],
        "range": False,
    }).encode("utf-8")
    request = urllib.request.Request(
        _HEIGHT_URL, data=body,
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
    )

    last_error: Exception | None = None
    for attempt in range(_RETRIES):
        if attempt:
            time.sleep(_RETRY_BACKOFF_S * attempt)
        _wait_for_rate_limit()
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
                heights = json.loads(response.read())["height"]
        except urllib.error.HTTPError as error:
            last_error = error
            # A 4xx other than rate limiting means the request itself is
            # wrong, and sending it again won't change the answer.
            if 400 <= error.code < 500 and error.code != 429:
                break
            continue
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as error:
            last_error = error
            continue
        if len(heights) != len(lat):
            last_error = ValueError(f"asked for {len(lat)} heights, got {len(heights)}")
            continue
        # The DEM has holes (open sea, some voids), which come back as null.
        return np.array([math.nan if height is None else float(height) for height in heights])

    raise UnreadableActivity(f"{name}: DEM elevation lookup failed ({last_error}).")


def _fill_gaps(heights: np.ndarray, name: str) -> np.ndarray:
    """Interpolate over DEM voids by point index; reject a file that is mostly void."""
    missing = np.isnan(heights)
    if missing.sum() > _MAX_SENTINEL_SHARE * len(heights):
        raise UnreadableActivity(f"{name}: the DEM has no height for most of this route.")
    if missing.any():
        index = np.arange(len(heights))
        heights = heights.copy()
        heights[missing] = np.interp(index[missing], index[~missing], heights[~missing])
    return heights


def _entry_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"{key}.npz"


def _load(path: Path, rows: int) -> np.ndarray | None:
    """Cached heights for a file, or None if absent, stale or the wrong length."""
    if not path.exists():
        return None
    try:
        with np.load(path) as data:
            if int(data["version"]) != _FORMAT_VERSION:
                return None
            heights = data["heights"]
    except Exception:  # A damaged entry is refetched, never raised on.
        return None
    return heights if len(heights) == rows else None


def _store(path: Path, heights: np.ndarray) -> None:
    """Write heights atomically, the same way cache.py writes decoded files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        np.savez(handle, heights=heights, version=np.array(_FORMAT_VERSION))
    os.replace(temporary, path)


def dem_heights(decoded: DecodedFit, key: str, name: str, cache_dir: Path = DEFAULT_CACHE_DIR) -> np.ndarray:
    """DEM heights for every record of a decoded file, from the cache or from Valhalla.

    Args:
        decoded: The file's raw numbers.
        key: Its `cache.content_key`, which keys the cache entry.
        name: For error messages.
        cache_dir: Where heights are kept.
    Returns:
        One height per row of `decoded.records`, in metres.
    Raises:
        UnreadableActivity: If the lookup fails or the DEM covers too little of the route.
    """
    rows = len(decoded.records)
    entry = _entry_path(Path(cache_dir), key)
    cached = _load(entry, rows)
    if cached is not None:
        return cached

    lat = decoded.records[:, RECORD_COLUMNS["lat"]]
    lon = decoded.records[:, RECORD_COLUMNS["lon"]]
    # Some records carry FIT's "invalid" position, which decodes to ~180°
    # latitude. fit_reader drops them later as teleports, but one of them in a
    # request makes the server reject the whole request, so they're not sent.
    valid = np.isfinite(lat) & np.isfinite(lon) & (np.abs(lat) <= 90) & (np.abs(lon) <= 180)
    valid_lat, valid_lon = lat[valid], lon[valid]
    requests = math.ceil(len(valid_lat) / _CHUNK_POINTS)
    print(f"  fetching DEM heights for {name}: {len(valid_lat)} points in {requests} request(s)", file=sys.stderr)
    chunks = [
        _request_heights(valid_lat[start:start + _CHUNK_POINTS], valid_lon[start:start + _CHUNK_POINTS], name)
        for start in range(0, len(valid_lat), _CHUNK_POINTS)
    ]
    heights = np.full(rows, math.nan)
    if chunks:
        heights[valid] = np.concatenate(chunks)
    heights = _fill_gaps(heights, name)
    _store(entry, heights)
    return heights


def with_dem_elevation(
    decoded: DecodedFit, key: str, name: str, cache_dir: Path = DEFAULT_CACHE_DIR
) -> tuple[DecodedFit, str | None]:
    """The decoded file with DEM heights in place of its elevation, if its own is unusable.

    Only a recording whose elevation is missing or noisy is sent anywhere. A
    clean one comes back untouched, so the corpus keeps the elevation it was
    recorded with wherever that is trustworthy.

    Returns:
        (the file to read, what was wrong with its elevation or None if it was kept).
    """
    records = decoded.records
    problem = elevation_problem(
        records[:, RECORD_COLUMNS["lat"]],
        records[:, RECORD_COLUMNS["lon"]],
        records[:, RECORD_COLUMNS["elevation"]],
    )
    if problem is None or len(records) == 0:
        return decoded, None

    patched = records.copy()
    patched[:, RECORD_COLUMNS["elevation"]] = dem_heights(decoded, key, name, cache_dir)
    return replace(decoded, records=patched), problem

# todo: import the haversine somehow or something, i feel like it is duplicated at many points in the code.
# todo: we are also calling the elevation noise both in __main__ and in prepare.py, check that.