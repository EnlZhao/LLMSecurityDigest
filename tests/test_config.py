from pathlib import Path

from llm_security_digest import config


def test_project_root_under_home():
    assert str(config.PROJECT_ROOT).startswith(str(Path.home()))


def test_cache_root_is_project_cache():
    assert config.CACHE_ROOT.parent == config.PROJECT_ROOT
    assert config.CACHE_ROOT.name == "cache"


def test_notion_db_name_is_stable():
    assert config.NOTION_DB_NAME == "LLM Security Daily"
