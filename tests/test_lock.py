from pathlib import Path

from llm_security_digest import lock, config


def test_lock_acquires_and_releases(tmp_path: Path, monkeypatch):
    p = tmp_path / "run.lock"
    monkeypatch.setattr(config, "LOCK_PATH", p)
    with lock.SingleInstanceLock() as acquired:
        assert acquired is True
        # second acquisition must fail (non-blocking)
        with lock.SingleInstanceLock() as again:
            assert again is False


def test_lock_releases_after_exit(tmp_path: Path, monkeypatch):
    p = tmp_path / "run.lock"
    monkeypatch.setattr(config, "LOCK_PATH", p)
    with lock.SingleInstanceLock():
        pass
    with lock.SingleInstanceLock() as acquired:
        assert acquired is True
