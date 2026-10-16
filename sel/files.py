"""JSON files that one thread replaces while another reads them.

Writes go to a temporary file and replace the old one, so a reader never sees
half a file. On Windows a replace fails while another thread has the file
open, and an open fails while it is being replaced (both raise
PermissionError, a sharing violation); both are retried briefly.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

RETRIES = 20
WAIT_S = 0.01


def _retry(fn):
    for attempt in range(RETRIES):
        try:
            return fn()
        except PermissionError:
            if attempt == RETRIES - 1:
                raise
            time.sleep(WAIT_S)
    raise AssertionError("unreachable")


def read_text(path: Path) -> str:
    return _retry(lambda: Path(path).read_text(encoding="utf-8"))


def read_json(path: Path) -> Any:
    return json.loads(read_text(path))


def write_json(path: Path, value: Any) -> None:
    path = Path(path)
    tmp = path.with_name(path.name + f".{os.getpid()}.{id(value):x}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    _retry(lambda: os.replace(tmp, path))
