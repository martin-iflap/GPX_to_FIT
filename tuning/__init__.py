"""Offline harness that calibrates the pacing model against real FIT activities.

This package is development tooling, not part of the converter. It reads files
from disk and is never loaded into Pyodide, so it is deliberately kept outside
`src/gpx2fit/core/` — see that package's no-I/O rule in CLAUDE.md, and never add
anything from here to `pyodideBridge.js`'s `backendFiles`.

It only ever calls `core/`'s public functions; `core/` itself is not modified,
monkeypatched, or otherwise reached around.
"""
