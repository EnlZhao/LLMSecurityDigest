from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import config


@contextmanager
def SingleInstanceLock() -> Iterator[bool]:
    path = config.LOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = path.open("w")
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield True
        except BlockingIOError:
            yield False
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        fd.close()
