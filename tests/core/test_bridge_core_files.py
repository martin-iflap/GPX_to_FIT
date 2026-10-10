"""pyodideBridge.js CORE_FILES must name every core/ module, or it silently doesn't exist in the browser.

A module missing from the list only fails once the real GUI imports it inside
Pyodide; pytest, pyrefly and the GUI's own tests all pass without it.
"""

import re
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "src" / "gpx2fit" / "core"
BRIDGE = CORE.parent / "gui" / "js" / "pyodideBridge.js"


def _listed() -> set[str]:
    source = BRIDGE.read_text(encoding="utf-8")
    block = re.search(r"const CORE_FILES = \[(.*?)\];", source, re.DOTALL)
    assert block is not None, "CORE_FILES not found in pyodideBridge.js"
    return set(re.findall(r"'([^']+)'", block.group(1)))


def _on_disk() -> set[str]:
    # __init__.py files are written by the bridge itself, empty.
    return {
        path.relative_to(CORE).as_posix()
        for pattern in ("*.py", "*.json")
        for path in CORE.rglob(pattern)
        if path.name != "__init__.py" and "__pycache__" not in path.parts
    }


def test_every_core_file_is_shipped_to_the_browser():
    assert _on_disk() - _listed() == set()


def test_every_listed_file_exists():
    assert _listed() - _on_disk() == set()
