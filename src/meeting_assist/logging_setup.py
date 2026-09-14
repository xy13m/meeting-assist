"""Per-meeting log file. The terminal belongs to the rich display, so
nothing is logged there."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_logging(path: Path, level: int = logging.WARNING) -> Iterator[None]:
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level > level or root.level == logging.NOTSET:
        root.setLevel(level)
    try:
        yield
    finally:
        root.removeHandler(handler)
        handler.close()
