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
