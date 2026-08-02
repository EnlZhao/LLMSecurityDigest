# LLM Security Daily 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development 或 superpowers:executing-plans。步骤采用 `- [ ]` 复选框追踪。

**Goal:** 在本机 macOS 上每天 06:00（Asia/Shanghai）自动调用 MiniMax-M3（最高推理强度）生成 5 篇 LLM Security 论文的中文 Notion 笔记，全程对 PDF 缓存进行严格路径边界保护。

**Architecture:** LaunchAgent 触发单一 Python 入口 `run_digest.py`；入口获取单实例锁、创建受保护的运行临时目录、调用 `codex exec` 启动 Agent 完成检索与写作；不论成败，finally 块仅清理本次运行目录，遗留清理只触及受控缓存根目录下的过期 `run-*` 直接子目录。

**Tech Stack:** Python 3.12、pytest、uv、macOS launchd、Codex CLI、Notion MCP、`paper-search` 技能。

**关键解释：** Codex 环境下 `MiniMax-M3` 的最高可用推理强度为 `high`（`none`/`high`）。本计划与提示词统一使用 `high`，与用户表述的 "max" 对应。

## 文件结构

```text
/Users/ez/llm-security-digest/
├── pyproject.toml
├── README.md
├── .gitignore
├── uv.lock
├── src/llm_security_digest/
│   ├── __init__.py
│   ├── config.py            # 路径、Notion DB 名等常量
│   ├── cache.py             # 受保护的 run 目录与清理（安全关键）
│   ├── lock.py              # fcntl 单实例锁
│   ├── prompt.py            # 生成 Agent 提示词
│   └── runner.py            # 调度 + 调用 codex exec + finally 清理
├── tests/
│   ├── test_cache.py        # 清理安全不变量
│   ├── test_lock.py
│   ├── test_config.py
│   ├── test_prompt.py
│   └── conftest.py
├── scripts/
│   ├── install-launchd.sh   # 安装/卸载 LaunchAgent
│   └── uninstall-launchd.sh
├── launchd/
│   └── com.llm-security-digest.daily.plist
├── cache/                   # 运行时创建，git 忽略
└── logs/                    # 运行时创建，git 忽略
```

## Task 1：项目骨架

**Files:**
- Create: `pyproject.toml`、`README.md`、`.gitignore`、`src/llm_security_digest/__init__.py`

- [ ] **Step 1: 写 `pyproject.toml`**

```toml
[project]
name = "llm-security-digest"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = []

[dependency-groups]
dev = ["pytest>=8"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/llm_security_digest"]
```

- [ ] **Step 2: 写 `.gitignore`**

```text
__pycache__/
*.pyc
.venv/
cache/
logs/
run.lock
.DS_Store
```

- [ ] **Step 3: 写 `src/llm_security_digest/__init__.py`**（空文件）

- [ ] **Step 4: 写 `README.md` 标题与目录占位**

```markdown
# LLM Security Daily

每天 06:00（Asia/Shanghai）自动从 AI 顶会、网络安全顶会和 arXiv 选取 5 篇 LLM Security 论文并写入 Notion。

详细设计与计划：
- 设计：`docs/superpowers/specs/2026-08-02-llm-security-daily-design.md`
- 实施计划：`docs/superpowers/plans/2026-08-02-llm-security-daily-impl.md`
```

- [ ] **Step 5: 创建虚拟环境并安装**

```bash
cd /Users/ez/llm-security-digest
uv venv --python 3.12
uv sync --group dev
uv run python -c "import llm_security_digest; print('ok')"
```

Expected：`ok`

- [ ] **Step 6: 提交**

```bash
git add pyproject.toml .gitignore README.md src/llm_security_digest/__init__.py
git commit -m "feat: project skeleton"
```

## Task 2：配置模块

**Files:**
- Create: `src/llm_security_digest/config.py`、`tests/test_config.py`、`tests/conftest.py`

- [ ] **Step 1: 写失败测试 `tests/test_config.py`**

```python
from pathlib import Path
from llm_security_digest import config


def test_project_root_under_home():
    assert str(config.PROJECT_ROOT).startswith(str(Path.home()))


def test_cache_root_is_project_cache():
    assert config.CACHE_ROOT.parent == config.PROJECT_ROOT
    assert config.CACHE_ROOT.name == "cache"


def test_notion_db_name_is_stable():
    assert config.NOTION_DB_NAME == "LLM Security Daily"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/test_config.py -q
```

Expected：`ModuleNotFoundError` 或 `ImportError`。

- [ ] **Step 3: 写最小实现 `src/llm_security_digest/config.py`**

```python
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_ROOT = PROJECT_ROOT / "cache"
LOGS_ROOT = PROJECT_ROOT / "logs"
LOCK_PATH = PROJECT_ROOT / "run.lock"
NOTION_DB_NAME = "LLM Security Daily"
PAPERS_PER_DAY = 5
MAX_PDF_BYTES = 25 * 1024 * 1024  # 25 MiB
RUN_PREFIX = "run-"
STALE_AFTER_HOURS = 24
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/test_config.py -q
```

Expected：1 passed。

- [ ] **Step 5: 提交**

```bash
git add src/llm_security_digest/config.py tests/test_config.py
git commit -m "feat: config module with project paths"
```

## Task 3：缓存与清理（安全关键）

**Files:**
- Create: `src/llm_security_digest/cache.py`、`tests/test_cache.py`

- [ ] **Step 1: 写失败测试 `tests/test_cache.py`**

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/test_cache.py -q
```

Expected：`ImportError: cannot import name 'cache'`。

- [ ] **Step 3: 写实现 `src/llm_security_digest/cache.py`**

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/test_cache.py -q
```

Expected：9 passed。

- [ ] **Step 5: 提交**

```bash
git add src/llm_security_digest/cache.py tests/test_cache.py
git commit -m "feat: cache module with path-safe cleanup"
```

## Task 4：单实例锁

**Files:**
- Create: `src/llm_security_digest/lock.py`、`tests/test_lock.py`

- [ ] **Step 1: 写失败测试 `tests/test_lock.py`**

```python
import fcntl
from pathlib import Path

import pytest

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
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/test_lock.py -q
```

- [ ] **Step 3: 写实现 `src/llm_security_digest/lock.py`**

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/test_lock.py -q
```

Expected：2 passed。

- [ ] **Step 5: 提交**

```bash
git add src/llm_security_digest/lock.py tests/test_lock.py
git commit -m "feat: single-instance lock via fcntl"
```

## Task 5：Agent 提示词

**Files:**
- Create: `src/llm_security_digest/prompt.py`、`tests/test_prompt.py`

- [ ] **Step 1: 写失败测试 `tests/test_prompt.py`**

```python
from datetime import date

from llm_security_digest import prompt, config


def test_prompt_contains_date_and_run_dir():
    today = date(2026, 8, 2)
    run = config.PROJECT_ROOT / "cache" / "run-abc"
    text = prompt.build_prompt(run_dir=run, today=today)
    assert "2026-08-02" in text
    assert "run-abc" in text
    assert "LLM Security Daily" in text
    assert "12" in text or "十二" in text  # note sections
    assert "jailbreak" in text.lower() or "prompt injection" in text.lower()


def test_prompt_forbids_deletion():
    run = config.PROJECT_ROOT / "cache" / "run-abc"
    text = prompt.build_prompt(run_dir=run, today=date.today())
    assert "delete" in text.lower()
    assert "run dir" in text.lower() or "运行目录" in text
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 写实现 `src/llm_security_digest/prompt.py`**

```python
from __future__ import annotations

from datetime import date
from pathlib import Path

from . import config

PROMPT_TEMPLATE = """You are running an automated daily digest for LLM Security research.

Date (Asia/Shanghai): {today}
Run directory (your ONLY writable workspace): {run_dir}
Notion database name: {db_name}
Target papers today: {n}

## Hard rules (must obey)
1. Do NOT delete any file or directory. Cleanup is handled by the parent runner.
2. Do NOT write outside {run_dir}. All PDFs and analysis stay inside it.
3. Do NOT log or expose any API keys, tokens, or credentials.
4. Do NOT download files unrelated to the selected papers.
5. If a PDF cannot be legally obtained, do not include that paper; use the abstract.
6. Never fabricate results, citations, or numbers. Mark unverified facts as "未核验".

## Source coverage
You MUST end with exactly {n} papers. Try to satisfy:
- At least 1 paper from a top AI venue (AAAI/NeurIPS/ICML/ICLR/ACL/EMNLP).
- At least 1 paper from a top security venue (IEEE S&P, USENIX Security, ACM CCS, NDSS).
- At least 1 paper from arXiv.
- Remaining slots by overall quality.
If a source class is unavailable, expand the time window and retry; do not lower quality.

## Coverage of LLM security (both directions)
Include BOTH:
- LLM-itself security: jailbreak, prompt injection, backdoor, data poisoning, privacy
  leakage, model stealing, hallucination, agent/tool-call security, supply chain, alignment.
- LLM-for-security: vulnerability discovery, code audit, malware analysis, offense/defense,
  threat intelligence, forensics.

## Quality scoring (0-100)
- LLM-security relevance: 30
- Method / threat model clarity: 20
- Experimental completeness (baselines, ablations, datasets, metrics): 20
- Venue tier or empirical strength: 15
- Novelty and real-world impact: 10
- Verifiability of metadata, full text, and results: 5
Record the score and short justification in each note.

## Notion protocol
1. Use mcp__notion__notion_search to find a database named "{db_name}".
2. If absent, create it with properties: 论文标题 (title), 收录日期 (date),
   发表日期 (date), 来源类型 (select), 会议或来源 (rich_text),
   研究类别 (multi_select), 主题标签 (multi_select), 质量评分 (number),
   论文主页 (url), PDF (url), 唯一标识 (rich_text).
3. Before writing a paper, query Notion for an existing entry with the same
   unique key (DOI > arXiv id > normalized title). Skip if already recorded today.
4. Write one Notion page per paper, with the note template below as the page body.

## Note template (12 sections, Chinese)
1. 一句话结论
2. 研究问题与背景
3. 威胁模型或安全应用场景
4. 核心方法
5. 实验设置
6. 关键结果及原文依据
7. 主要贡献
8. 优点
9. 局限与可能失效条件
10. 对 LLM Security 研究的启示
11. 可复现性与代码情况
12. 论文主页、PDF、代码等可核验链接

Mark unverifiable items as "论文未说明" or "未能核验".

## PDF handling
- Download into {run_dir}/papers/ using a deterministic filename.
- Cap each file at 25 MiB; reject responses that do not start with %PDF-.
- After you finish, leave PDFs in place; the runner will delete {run_dir}.

## Tools
- Use the `paper-search` skill for candidate discovery.
- Use Notion MCP tools (mcp__notion__*) for database and page operations.
- Use bash/curl to download PDFs into {run_dir}/papers/.

When done, output a short JSON line:
{{"status":"ok","written":N,"skipped":M}}
"""


def build_prompt(run_dir: Path, today: date) -> str:
    return PROMPT_TEMPLATE.format(
        today=today.isoformat(),
        run_dir=run_dir,
        db_name=config.NOTION_DB_NAME,
        n=config.PAPERS_PER_DAY,
    )
```

- [ ] **Step 4: 跑测试确认通过**

- [ ] **Step 5: 提交**

```bash
git add src/llm_security_digest/prompt.py tests/test_prompt.py
git commit -m "feat: agent prompt template"
```

## Task 6：Runner 入口

**Files:**
- Create: `src/llm_security_digest/runner.py`

- [ ] **Step 1: 写实现 `src/llm_security_digest/runner.py`**

```python
from __future__ import annotations

import json
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
        "--cd", str(run_dir),
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
        (run_dir / "papers").mkdir(exist_ok=True)
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
```

- [ ] **Step 2: 自检启动**

```bash
uv run python -c "from llm_security_digest.runner import run_once; print('import ok')"
```

- [ ] **Step 3: 提交**

```bash
git add src/llm_security_digest/runner.py
git commit -m "feat: runner entry with safe cleanup"
```

## Task 7：LaunchAgent 与安装脚本

**Files:**
- Create: `launchd/com.llm-security-digest.daily.plist`、`scripts/install-launchd.sh`、`scripts/uninstall-launchd.sh`

- [ ] **Step 1: 写 `launchd/com.llm-security-digest.daily.plist`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.llm-security-digest.daily</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/ez/llm-security-digest/.venv/bin/python</string>
    <string>-m</string>
    <string>llm_security_digest.runner</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/Users/ez/llm-security-digest</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>TZ</key>
    <string>Asia/Shanghai</string>
    <key>PATH</key>
    <string>/Users/ez/llm-security-digest/.venv/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>6</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/Users/ez/llm-security-digest/logs/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/ez/llm-security-digest/logs/launchd.err.log</string>
  <key>ProcessType</key>
  <string>Background</string>
</dict>
</plist>
```

> 备注：`RunAtLoad: true` 让电脑从睡眠唤醒或开机后补跑当天任务；runner 内部通过 Notion 已存在记录数判断是否真的需要写入，从而保持幂等。

- [ ] **Step 2: 验证 plist 语法**

```bash
plutil -lint launchd/com.llm-security-digest.daily.plist
```

Expected：`OK`。

- [ ] **Step 3: 写 `scripts/install-launchd.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
PLIST_SRC="$(cd "$(dirname "$0")/.." && pwd)/launchd/com.llm-security-digest.daily.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.llm-security-digest.daily.plist"
mkdir -p "$HOME/Library/LaunchAgents"
cp "$PLIST_SRC" "$PLIST_DST"
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"
launchctl list | grep llm-security-digest || true
echo "installed: $PLIST_DST"
```

- [ ] **Step 4: 写 `scripts/uninstall-launchd.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
PLIST_DST="$HOME/Library/LaunchAgents/com.llm-security-digest.daily.plist"
if [ -f "$PLIST_DST" ]; then
  launchctl unload "$PLIST_DST" 2>/dev/null || true
  rm -f "$PLIST_DST"
fi
echo "uninstalled"
```

- [ ] **Step 5: 加执行权限并自检**

```bash
chmod +x scripts/install-launchd.sh scripts/uninstall-launchd.sh
bash -n scripts/install-launchd.sh && bash -n scripts/uninstall-launchd.sh
```

- [ ] **Step 6: 提交**

```bash
git add launchd scripts
git commit -m "feat: launchd agent and installer"
```

## Task 8：手动试运行

- [ ] **Step 1: 跑一次完整流程**

```bash
cd /Users/ez/llm-security-digest
uv run python -m llm_security_digest.runner
```

- [ ] **Step 2: 验证缓存已清空**

```bash
ls cache/ 2>/dev/null || echo 'cache empty (ok)'
ls logs/ 2>/dev/null
```

Expected：`cache empty (ok)`，日志存在。

- [ ] **Step 3: 验证 Notion 已新增 5 条记录**

在 Notion 工作区中确认数据库 `LLM Security Daily` 存在，并已写入今天 5 条新记录。

- [ ] **Step 4: 安装 LaunchAgent**

```bash
bash scripts/install-launchd.sh
launchctl list | grep llm-security-digest
```

Expected：显示已加载的 LaunchAgent 标签与 PID（或 `-`）。

- [ ] **Step 5: 二次跑确认幂等**

```bash
uv run python -m llm_security_digest.runner
```

Expected：日志中显示"Notion 已有 5 条"或类似幂等消息，Notion 不会新增条目。

- [ ] **Step 6: 提交运行结果备注（如有）并推送**

```bash
git push
```
