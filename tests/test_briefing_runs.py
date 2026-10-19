import asyncio
from pathlib import Path

import pytest

from briefing_config import BriefingConfig, Repo
from briefing_runs import BriefingRuns
from pi_briefing import BriefingError


def result(key: str) -> dict:
    return {"type": "result", "key": key, "date": key.split(":")[1], "markdown": key}


def test_history_lists_saved_briefings_newest_day_first(tmp_path: Path) -> None:
    runs = BriefingRuns(tmp_path)
    for key in ["briefing:2026-10-17:aaa", "briefing:2026-10-19:bbb", "briefing:2026-10-18:ccc"]:
        runs._save(result(key))
    assert [s.date for s in runs.history()] == ["2026-10-19", "2026-10-18", "2026-10-17"]
    assert runs.load("briefing:2026-10-18:ccc")["markdown"] == "briefing:2026-10-18:ccc"


def test_history_is_empty_before_any_run(tmp_path: Path) -> None:
    assert BriefingRuns(tmp_path / "fresh").history() == []


async def test_concurrent_failures_each_report_and_release_the_lock(tmp_path: Path) -> None:
    runs = BriefingRuns(tmp_path)
    bad = BriefingConfig((Repo("a/b"),), lookback_hours=24)
    future = {"date": "2999-01-01"}
    outcomes = await asyncio.gather(
        runs.run(bad, lambda _: None, **future),
        runs.run(bad, lambda _: None, **future),
        return_exceptions=True,
    )
    assert all(isinstance(o, BriefingError) for o in outcomes)
    assert not runs.results_dir.exists()


async def test_progress_messages_reach_the_callback(tmp_path: Path) -> None:
    """A run that fails at the model still reports its progress first."""
    runs = BriefingRuns(tmp_path)
    seen: list[str] = []
    config = BriefingConfig((Repo("posit-dev/commons"),))
    env = {"ANTHROPIC_API_KEY": "sk-invalid", "BRIEFING_MODEL": "anthropic/claude-haiku-5-5"}
    with pytest.MonkeyPatch.context() as patch:
        for name, value in env.items():
            patch.setenv(name, value)
        with pytest.raises(BriefingError, match="not answered"):
            await runs.run(config, seen.append)
    assert "Asking the model" in seen
