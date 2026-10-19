from pathlib import Path

from traces import TraceStore, Usage, fold, load_trace

USAGE = {"input": 100, "cacheRead": 1000, "cacheWrite": 50, "output": 20, "costUsd": 0.01}


def test_streamed_text_merges_and_the_final_text_wins() -> None:
    blocks = fold(
        [
            {"kind": "turn"},
            {"kind": "text", "delta": "Mer"},
            {"kind": "text", "delta": "ged"},
            {"kind": "message_end", "text": "Merged PR 1.", "usage": USAGE},
        ]
    )
    assert [b["type"] for b in blocks] == ["turn", "text", "usage"]
    assert blocks[1]["text"] == "Merged PR 1."


def test_a_message_that_finished_before_any_delta_still_shows_its_text() -> None:
    blocks = fold([{"kind": "turn"}, {"kind": "message_end", "text": "Short.", "usage": USAGE}])
    assert blocks[1] == {"type": "text", "text": "Short."}


def test_tool_calls_pair_by_id_even_when_they_overlap() -> None:
    blocks = fold(
        [
            {"kind": "tool_start", "id": "a", "name": "repo_activity", "args": {"repo": "x/y"}},
            {"kind": "tool_start", "id": "b", "name": "repo_activity", "args": {"repo": "z/w"}},
            {
                "kind": "tool_end",
                "id": "b",
                "name": "repo_activity",
                "isError": True,
                "output": "no",
            },
        ]
    )
    first, second = blocks
    assert first["args"] == {"repo": "x/y"}
    assert not first["done"]
    assert second["done"]
    assert second["is_error"]
    assert second["output"] == "no"


def test_usage_sums_every_model_call() -> None:
    store = TraceStore(Path("/nonexistent"))
    trace = store.start("pi-durable", "briefing", "b")
    trace.path = None
    trace.events = [
        {"kind": "message_end", "usage": USAGE},
        {"kind": "message_end", "usage": USAGE},
    ]
    assert trace.usage() == Usage(200, 2000, 100, 40, 0.02, 2)
    assert trace.usage().tokens == 2340


def test_a_trace_survives_on_disk(tmp_path: Path) -> None:
    store = TraceStore(tmp_path)
    trace = store.start("commons", "question", "Q: hi", model="m")
    trace.add({"kind": "turn"})
    trace.finish()
    loaded = load_trace(next(tmp_path.glob("*.jsonl")))
    assert (loaded.agent, loaded.kind, loaded.label, loaded.model) == (
        "commons",
        "question",
        "Q: hi",
        "m",
    )
    assert loaded.events == [{"kind": "turn"}]
    assert loaded.status == "done"


def test_a_trace_the_app_never_finished_reads_as_failed(tmp_path: Path) -> None:
    trace = TraceStore(tmp_path).start("pi-durable", "briefing", "b")
    trace.add({"kind": "turn"})
    loaded = load_trace(next(tmp_path.glob("*.jsonl")))
    assert loaded.status == "failed"
    assert "stopped" in loaded.error


def test_recent_lists_newest_first_and_filters_by_agent(tmp_path: Path) -> None:
    store = TraceStore(tmp_path)
    first = store.start("pi-durable", "briefing", "first")
    first.started -= 10
    store.start("commons", "question", "second")
    third = store.start("pi-durable", "question", "third")
    for trace in (first, third):
        trace.finish()
    assert [t.label for t in store.recent("pi-durable")] == ["third", "first"]
    assert next(t.label for t in store.recent()) in {"second", "third"}
    assert [t.label for t in TraceStore(tmp_path).recent("pi-durable")] == ["third", "first"]
