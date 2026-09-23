"""Keep decoded FIT files on disk, and decode the missing ones in parallel.

Decoding is what the harness spends its time on. fit-tool rebuilds a field
object for every field of every record, which for a corpus of two dozen
activities is tens of millions of short-lived objects.

Two mechanisms, and they compose:

- **The cache.** `decoded_activity` looks the file up by the SHA-256 of its
  contents and returns the stored arrays if they are there. A hit costs a few
  milliseconds. Because the key is the content hash, a re-exported or renamed
  file is simply a different entry, and nothing has to be invalidated by hand.
- **Parallel warming.** `warm_cache` decodes everything missing across a
  process pool, since decoding is pure-Python and GIL-bound. That is what the
  first run on a new corpus pays, and it drops with the core count.

Only `fit_reader.decode_fit_bytes` is cached — the raw numbers. Every
judgment about them runs on every read, so changing a
rule never needs the cache cleared. `_FORMAT_VERSION` covers the one case that
does: a change to what `decode_fit_bytes` itself extracts.

Cached data is also in tuning_out/ under decoded and therefore in .gitignore.
"""

import hashlib
import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from tuning.fit_reader import DecodedFit, UnreadableActivity, decode_fit_bytes

DEFAULT_CACHE_DIR = Path("tuning_out") / "decoded"

# Bump when `decode_fit_bytes` changes which columns it extracts or what they
# mean. Entries written by an older version are ignored (and overwritten), so
# a bump costs one slow run, not a stale corpus.
_FORMAT_VERSION = 1


def content_key(fit_bytes: bytes) -> str:
    """The cache entry name for a file's contents.

    Keyed by content rather than by path or mtime: re-exporting an activity,
    renaming it, or moving it between sport folders then reuses the same entry,
    and an edited file can never be served from a stale one. Also, what the CLI
    uses to spot the same activity exported twice under two names.
    """
    return hashlib.sha256(fit_bytes).hexdigest()


def _entry_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"{key}.npz"


def _store(path: Path, decoded: DecodedFit) -> None:
    """Write one decoded activity, atomically.

    Via a temporary file and `os.replace` so an interrupted run — Ctrl-C
    halfway through warming a corpus — can't leave a half-written entry that
    the next run would read back as a truncated activity.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = json.dumps({
        "version": _FORMAT_VERSION,
        "sport_value": decoded.sport_value,
        "elapsed_seconds": decoded.elapsed_seconds,
        "timer_seconds": decoded.timer_seconds,
        "distance_m": decoded.distance_m,
        "ascent_m": decoded.ascent_m,
    })
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        np.savez(handle, records=decoded.records, events=decoded.events, meta=np.array(meta))
    os.replace(temporary, path)


def _is_usable(path: Path) -> bool:
    """Whether an entry exists and was written by this format version.

    Reads only the metadata member, not the arrays — an .npz is a zip, so this
    stays cheap enough to run over a whole corpus before deciding what needs
    decoding.
    """
    if not path.exists():
        return False
    try:
        with np.load(path) as data:
            return json.loads(str(data["meta"])).get("version") == _FORMAT_VERSION
    except Exception:
        return False


def _load(path: Path) -> DecodedFit | None:
    """Read one decoded activity back, or None if the entry can't be used.

    A missing, corrupt or older-format entry is not an error: the caller just
    decodes the file again and overwrites it. The cache is derived data, so it
    is never a reason to fail a run.
    """
    if not path.exists():
        return None
    try:
        with np.load(path) as data:
            meta = json.loads(str(data["meta"]))
            if meta.get("version") != _FORMAT_VERSION:
                return None
            return DecodedFit(
                records=data["records"],
                events=data["events"],
                sport_value=meta["sport_value"],
                elapsed_seconds=meta["elapsed_seconds"],
                timer_seconds=meta["timer_seconds"],
                distance_m=meta["distance_m"],
                ascent_m=meta["ascent_m"],
            )
    except Exception:  # A damaged entry is re-decoded, never raised on.
        return None


def decoded_activity(path: Path, cache_dir: Path | None = DEFAULT_CACHE_DIR) -> DecodedFit:
    """One file's raw numbers, from the cache if they're there and by decoding if not.

    Args:
        path: The .fit file.
        cache_dir: Where entries live. None disables the cache entirely, which
            is what `--no-cache` is for when decode is under suspicion.

    Returns:
        The decoded file, which is also stored for next time.
    Raises:
        UnreadableActivity: If the file can't be read or decoded.
    """
    try:
        fit_bytes = path.read_bytes()
    except OSError as error:
        raise UnreadableActivity(f"{path}: could not be read ({error}).") from error

    if cache_dir is None:
        return decode_fit_bytes(fit_bytes, path.stem)

    entry = _entry_path(Path(cache_dir), content_key(fit_bytes))
    cached = _load(entry)
    if cached is not None:
        return cached

    decoded = decode_fit_bytes(fit_bytes, path.stem)
    _store(entry, decoded)
    return decoded


def _decode_into_cache(arguments: tuple[str, str]) -> None:
    """Pool worker: decode one file and store it, swallowing anything it raises.

    A file that can't be decoded is left for the main process to hit in
    `decoded_activity`, where the failure is reported against the activity it
    belongs to and in the order the report expects. Decoding a broken file
    twice costs nothing — it fails on the header.

    Takes one packed tuple because it is called through `Executor.map`, and
    must stay importable at module level for the spawn start method Windows
    uses.
    """
    path, cache_dir = arguments
    try:
        decoded_activity(Path(path), Path(cache_dir))
    except Exception:
        pass


def warm_cache(
    paths: list[Path],
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
    workers: int | None = None,
) -> int:
    """Decode every not-yet-cached file across a process pool.

    Decoding is pure-Python and GIL-bound, so this wants processes rather than
    threads. Files already in the cache are skipped without being read past
    their hash, so a warm corpus costs one hash per file and no pool at all.

    Args:
        paths: The .fit files about to be read.
        cache_dir: Where entries live; None disables caching, so there is
            nothing to warm.
        workers: Pool size. Defaults to one per core, never more than there
            are files to decode.
    Returns:
        How many files were decoded, i.e. how many were missing.
    """
    if cache_dir is None:
        return 0

    cache_dir = Path(cache_dir)
    missing = []
    for path in paths:
        try:
            key = content_key(path.read_bytes())
        except OSError:
            continue  # Reported properly when the activity itself is read.
        if not _is_usable(_entry_path(cache_dir, key)):
            missing.append(path)

    if not missing:
        return 0

    pool_size = workers if workers is not None else (os.cpu_count() - 1 or 1)
    pool_size = max(1, min(pool_size, len(missing)))
    arguments = [(str(path), str(cache_dir)) for path in missing]

    if pool_size == 1:
        for argument in arguments:
            _decode_into_cache(argument)
        return len(missing)

    with ProcessPoolExecutor(max_workers=pool_size) as pool:
        list(pool.map(_decode_into_cache, arguments))
    return len(missing)
