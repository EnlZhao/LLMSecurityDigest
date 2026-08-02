from __future__ import annotations

import os
import secrets
import shutil
import time
from pathlib import Path

from . import config


def _safe_root() -> Path:
    root = config.CACHE_ROOT.resolve()
    home = Path.home().resolve()
    if root == Path("/").resolve() or root == home or str(root) == "":
        raise ValueError("refusing to operate on unsafe cache root")
    return root


def create_run_dir() -> Path:
    root = _safe_root()
    root.mkdir(parents=True, exist_ok=True)
    name = f"{config.RUN_PREFIX}{secrets.token_hex(8)}"
    path = root / name
    path.mkdir()
    return path


def _is_run_child(target: Path, root: Path) -> bool:
    try:
        target_resolved = target.resolve()
    except FileNotFoundError:
        target_resolved = target.absolute()
    if target_resolved == root:
        return False
    if target_resolved.parent != root:
        return False
    if not target.name.startswith(config.RUN_PREFIX):
        return False
    if target.is_symlink():
        return False
    if "/" in target.name or "\\" in target.name or target.name in (".", ".."):
        return False
    return True


def cleanup_run(run_dir: Path) -> None:
    root = _safe_root()
    if not _is_run_child(run_dir, root):
        raise ValueError(f"refusing to delete non-managed path: {run_dir}")
    shutil.rmtree(run_dir)


def prune_stale() -> list[Path]:
    root = _safe_root()
    if not root.exists():
        return []
    cutoff = time.time() - config.STALE_AFTER_HOURS * 3600
    removed: list[Path] = []
    for child in root.iterdir():
        try:
            if child.is_dir() and not child.is_symlink():
                if not child.name.startswith(config.RUN_PREFIX):
                    continue
                stat = child.stat()
                if stat.st_mtime < cutoff:
                    shutil.rmtree(child)
                    removed.append(child)
        except OSError:
            continue
    return removed
