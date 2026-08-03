from __future__ import annotations

import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import cache, config, lock, prompt


def _today_local() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def _build_codex_cmd(run_dir: Path) -> list[str]:
    return [
        "codex",
        "exec",
        "-m", "MiniMax-M3",
        "-c", 'model_reasoning_effort="high"',
        "--sandbox", "workspace-write",
        "--cd", str(config.PROJECT_ROOT),
        "--skip-git-repo-check",
        "-",
    ]


def run_once() -> int:
    config.LOGS_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = config.LOGS_ROOT / "daily.log"
    today = _today_local()
    log_lines: list[str] = []
    log_lines.append(f"[{today}] start")

    with lock.SingleInstanceLock() as acquired:
        if not acquired:
            log_lines.append("skip: another run is in progress")
            log_path.write_text("\n".join(log_lines) + "\n")
            return 0

        run_dir = cache.create_run_dir()
        log_lines.append(f"run_dir: {run_dir}")
        status = 1
        try:
            prompt_text = prompt.build_prompt(
                run_dir=run_dir, today=datetime.now(ZoneInfo("Asia/Shanghai")).date()
            )
            cmd = _build_codex_cmd(run_dir)
            log_lines.append("exec: " + " ".join(shlex.quote(c) for c in cmd))
            result = subprocess.run(
                cmd,
                input=prompt_text,
                text=True,
                timeout=60 * 60,
            )
            log_lines.append(f"codex exit: {result.returncode}")
            status = result.returncode
        except subprocess.TimeoutExpired:
            log_lines.append("error: codex exec timed out after 60m")
            status = 124
        except FileNotFoundError as exc:
            log_lines.append(f"error: {exc}")
            status = 127
        finally:
            try:
                cache.cleanup_run(run_dir)
                log_lines.append("cleanup: ok")
            except ValueError as exc:
                log_lines.append(f"cleanup refused: {exc}")
            cache.prune_stale()
    log_lines.append("done")
    log_path.write_text("\n".join(log_lines) + "\n")
    return status


def main() -> int:
    return run_once()


if __name__ == "__main__":
    sys.exit(main())
