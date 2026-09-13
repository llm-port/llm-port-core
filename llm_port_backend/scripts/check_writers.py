"""Throwaway local check: in-place vs atomic JSON writers in monitoring.py.

Run: uv run python scripts/check_writers.py   (from llm_port_backend/)
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm_port_backend.services.llm.monitoring import (  # noqa: E402
    _atomic_write_json,
    _atomic_write_json_inplace,
)


def check(label: str, path: Path, want: object) -> None:
    got = json.loads(path.read_text(encoding="utf-8"))
    assert got == want, f"{label}: {got!r} != {want!r}"
    print(f"ok  {label}: {len(json.dumps(got))} bytes in container-view file")


data = [
    {"targets": ["10.0.0.1:8000"], "labels": {"job": "llm-runtimes", "runtime_id": "a"}},
    {"targets": ["10.0.0.2:8091"], "labels": {"job": "llm-runtimes", "runtime_id": "b"}},
]

with tempfile.TemporaryDirectory() as td:
    td_path = Path(td)

    # in-place on an existing file (the seeded "[]" case, like the compose mount)
    p = td_path / "targets.json"
    p.write_text("[]\n", encoding="utf-8")
    before_inode = p.stat().st_ino
    _atomic_write_json_inplace(p, data)
    check("inplace seeded", p, data)
    assert p.stat().st_ino == before_inode, "in-place write changed the inode!"
    print("ok  inplace preserves inode")

    # in-place shrink (list shrinks back to [])
    _atomic_write_json_inplace(p, [])
    check("inplace shrink", p, [])
    assert p.stat().st_ino == before_inode, "in-place write changed the inode!"

    # in-place on a missing file (bootstrap path)
    p2 = td_path / "missing.json"
    _atomic_write_json_inplace(p2, data)
    check("inplace missing->created", p2, data)

    # atomic replace (dashboard-style)
    p3 = td_path / "dash.json"
    _atomic_write_json(p3, {"title": "x"})
    check("atomic replace", p3, {"title": "x"})
    assert not any(f.name.endswith(".tmp") for f in td_path.iterdir()), "leftover tmp"

print("ALL OK")
