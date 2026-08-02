import os
import time
from pathlib import Path

import pytest

from llm_security_digest import cache, config


@pytest.fixture
def isolated_cache(monkeypatch, tmp_path: Path):
    fake_root = tmp_path / "cache"
    monkeypatch.setattr(config, "CACHE_ROOT", fake_root)
    return fake_root


def test_create_run_dir_under_cache_root(isolated_cache: Path):
    run = cache.create_run_dir()
    assert run.parent.resolve() == isolated_cache.resolve()
    assert run.name.startswith(config.RUN_PREFIX)
    assert run.is_dir()


def test_cleanup_run_removes_target(isolated_cache: Path):
    run = cache.create_run_dir()
    (run / "paper.pdf").write_bytes(b"%PDF-1.4\n")
    cache.cleanup_run(run)
    assert not run.exists()


def test_cleanup_run_rejects_cache_root(isolated_cache: Path):
    with pytest.raises(ValueError):
        cache.cleanup_run(isolated_cache)


def test_cleanup_run_rejects_non_run_prefix(isolated_cache: Path):
    bad = isolated_cache / "not-a-run"
    bad.mkdir()
    with pytest.raises(ValueError):
        cache.cleanup_run(bad)
    assert bad.exists()


def test_cleanup_run_rejects_path_outside_cache(isolated_cache: Path, tmp_path: Path):
    outsider = tmp_path / "elsewhere"
    outsider.mkdir()
    with pytest.raises(ValueError):
        cache.cleanup_run(outsider)
    assert outsider.exists()


def test_cleanup_run_rejects_symlink(isolated_cache: Path, tmp_path: Path):
    target = tmp_path / "victim"
    target.mkdir()
    (target / "important.txt").write_text("keep me")
    link = isolated_cache / "run-evil"
    link.symlink_to(target)
    with pytest.raises(ValueError):
        cache.cleanup_run(link)
    assert target.exists()
    assert (target / "important.txt").exists()


def test_prune_stale_removes_only_old_run_dirs(isolated_cache: Path):
    old = cache.create_run_dir()
    new = cache.create_run_dir()
    # Make `old` look 48h old.
    past = time.time() - 48 * 3600
    os.utime(old, (past, past))
    cache.prune_stale()
    assert not old.exists()
    assert new.exists()


def test_prune_stale_ignores_non_run_dirs(isolated_cache: Path):
    keep = isolated_cache / "sentinel.txt"
    keep.write_text("do not touch")
    past = time.time() - 48 * 3600
    os.utime(keep, (past, past))
    cache.prune_stale()
    assert keep.exists()


def test_prune_stale_does_not_touch_files_outside_cache(
    isolated_cache: Path, tmp_path: Path
):
    sentinel_dir = tmp_path / "outside"
    sentinel_dir.mkdir()
    sentinel = sentinel_dir / "guard.txt"
    sentinel.write_text("keep")
    past = time.time() - 48 * 3600
    os.utime(sentinel, (past, past))
    cache.prune_stale()
    assert sentinel.exists()
