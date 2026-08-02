from datetime import date

from llm_security_digest import prompt, config


def test_prompt_contains_date_and_run_dir():
    today = date(2026, 8, 2)
    run = config.PROJECT_ROOT / "cache" / "run-abc"
    text = prompt.build_prompt(run_dir=run, today=today)
    assert "2026-08-02" in text
    assert "run-abc" in text
    assert "LLM Security Daily" in text
    assert "jailbreak" in text.lower() or "prompt injection" in text.lower()


def test_prompt_forbids_deletion():
    run = config.PROJECT_ROOT / "cache" / "run-abc"
    text = prompt.build_prompt(run_dir=run, today=date.today())
    assert "delete" in text.lower()
    assert "run dir" in text.lower() or "运行目录" in text
