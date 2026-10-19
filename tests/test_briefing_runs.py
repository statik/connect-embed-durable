import asyncio
from pathlib import Path

import pytest

from briefing_config import BriefingConfig, Repo
from briefing_runs import BriefingRuns
from pi_briefing import BriefingError
from traces import TraceStore


def runs(tmp_path: Path) -> BriefingRuns:
    return BriefingRuns(tmp_path, TraceStore(tmp_path / "traces"))


def result(key: str) -> dict:
    return {"type": "result", "key": key, "date": key.split(":")[1], "markdown": key}


def test_history_lists_saved_briefings_newest_day_first(tmp_path: Path) -> None:
    store = runs(tmp_path)
    for key in ["briefing:2026-10-17:aaa", "briefing:2026-10-19:bbb", "briefing:2026-10-18:ccc"]:
        store._save(result(key))
    assert [s.date for s in store.history()] == ["2026-10-19", "2026-10-18", "2026-10-17"]
    assert store.load("briefing:2026-10-18:ccc")["markdown"] == "briefing:2026-10-18:ccc"
    assert [r["date"] for r in store.results()] == ["2026-10-19", "2026-10-18", "2026-10-17"]


def test_history_is_empty_before_any_run(tmp_path: Path) -> None:
    assert runs(tmp_path / "fresh").history() == []


async def test_concurrent_failures_each_report_trace_and_release_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-unused")
    store = runs(tmp_path)
    bad = BriefingConfig((Repo("a/b"),), lookback_hours=24)
    outcomes = await asyncio.gather(
        store.run(bad, date="2999-01-01"), store.run(bad, date="2999-01-01"), return_exceptions=True
    )
    assert all(isinstance(o, BriefingError) for o in outcomes)
    assert not store.results_dir.exists()
    traces = store.traces.recent("pi-durable")
    assert [t.status for t in traces] == ["failed", "failed"]
    assert all("is in the future" in t.error for t in traces)
    waited = [t for t in traces if t.events and "Waiting" in t.events[0].get("message", "")]
    assert len(waited) == 1


async def test_a_run_streams_its_trace_before_failing_at_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real run with a key the provider rejects: the trace shows the model call it tried."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-invalid")
    store = runs(tmp_path)
    with pytest.raises(BriefingError, match="No answer"):
        await store.run(BriefingConfig((Repo("posit-dev/commons"),)))
    (trace,) = store.traces.recent("pi-durable")
    assert trace.status == "failed"
    assert {"kind": "note", "message": "New briefing"} in trace.events
    assert any(e["kind"] == "turn" for e in trace.events)


async def test_asking_about_a_missing_briefing_is_a_usage_error(tmp_path: Path) -> None:
    store = runs(tmp_path)
    with pytest.raises(BriefingError, match="There is no briefing") as error:
        await store.ask("briefing:2026-10-18:000000000000", "What merged?", "r1")
    assert error.value.usage_error
    (trace,) = store.traces.recent("pi-durable")
    assert trace.kind == "question"
    assert trace.status == "failed"
