"""The commons agent's data, measures, and trace. Measures run on real DuckDB; the model call is
the only thing replaced, and only in the stream test."""

import os
from pathlib import Path
from typing import Any

import chatlas
import duckdb
import pytest

import ask_agent
from traces import TraceStore

PRICE = {"input": 2.0, "output": 10.0, "cacheRead": 0.1, "cacheWrite": 2.5}


def item(kind: str, number: int | None, author: str, url: str) -> dict[str, Any]:
    return {
        "repo": "acme/widgets",
        "kind": kind,
        "number": number,
        "title": f"{kind} {number}",
        "author": author,
        "at": "2026-10-18T01:00:00Z",
        "labels": ["bug", "ui"],
        "url": url,
    }


PULL = item("pr_merged", 1, "ada", "https://github.com/acme/widgets/pull/1")
RESULTS = [
    {
        "date": "2026-10-18",
        "items": [PULL],
        "activity": {"acme/widgets": {"commits": 3, "truncated": False}},
    },
    {
        "date": "2026-10-19",
        "items": [
            PULL,
            item("release", None, "bob", "https://github.com/acme/widgets/releases/tag/v1"),
        ],
        "activity": {"acme/widgets": {"commits": 5, "truncated": True}},
    },
]


@pytest.fixture
def briefings() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    for name, frame in ask_agent.briefing_frames(RESULTS).items():
        connection.register(name, frame)
    return connection


def test_frames_have_one_row_per_item_per_briefing(briefings: duckdb.DuckDBPyConnection) -> None:
    rows = briefings.execute(
        "SELECT briefing_date, kind, number, labels FROM items ORDER BY 1, 2"
    ).fetchall()
    assert rows == [
        ("2026-10-18", "pr_merged", 1, "bug, ui"),
        ("2026-10-19", "pr_merged", 1, "bug, ui"),
        ("2026-10-19", "release", None, "bug, ui"),
    ]
    assert briefings.execute("SELECT SUM(commits) FROM repos").fetchone() == (8,)


def test_frames_from_no_briefings_still_have_their_columns() -> None:
    frames = ask_agent.briefing_frames([])
    assert "happened_at" in frames["items"].columns
    assert frames["repos"].empty


def test_latest_activity_counts_only_the_newest_briefing(
    briefings: duckdb.DuckDBPyConnection,
) -> None:
    frame = ask_agent.latest_activity(briefings)
    assert frame.to_dict("records") == [
        {"repo": "acme/widgets", "kind": "pr_merged", "items": 1},
        {"repo": "acme/widgets", "kind": "release", "items": 1},
    ]


def test_contributors_count_an_item_once_across_overlapping_briefings(
    briefings: duckdb.DuckDBPyConnection,
) -> None:
    assert ask_agent.top_contributors(briefings).to_dict("records") == [
        {"author": "ada", "items": 1},
        {"author": "bob", "items": 1},
    ]
    assert ask_agent.top_contributors(briefings, kind="release")["author"].tolist() == ["bob"]


def test_merged_pull_requests_are_listed_once_and_filter_by_repo(
    briefings: duckdb.DuckDBPyConnection,
) -> None:
    assert ask_agent.merged_pull_requests(briefings)["number"].tolist() == [1]
    assert ask_agent.merged_pull_requests(briefings, repo="other/repo").empty


def test_stream_chunks_become_trace_events() -> None:
    request = chatlas.ContentToolRequest(id="c1", name="call_measure", arguments={"name": "x"})
    result = chatlas.ContentToolResult(value="rows", request=request, extra={"commons_tag": "A"})
    assert ask_agent.chunk_events(request) == [
        {"kind": "tool_start", "id": "c1", "name": "call_measure", "args": {"name": "x"}}
    ]
    assert ask_agent.chunk_events(result) == [
        {
            "kind": "tool_end",
            "id": "c1",
            "name": "call_measure",
            "isError": False,
            "output": "rows",
            "tag": "A",
        }
    ]
    marker = '<shiny-aside label="Trusted" icon="i.svg">Trusted calculation</shiny-aside>'
    assert ask_agent.chunk_events(marker) == [{"kind": "note", "message": "Provenance: Trusted"}]
    assert ask_agent.chunk_events("Ada merged") == [{"kind": "text", "delta": "Ada merged"}]
    assert ask_agent.chunk_events("") == []


def test_turns_are_priced_like_the_durable_agent() -> None:
    turns = [
        {"role": "user", "tokens": 1_000, "tokens_cached": 10_000, "tokens_total": 11_000},
        {"role": "assistant", "tokens": 500, "tokens_cached": 0, "tokens_total": 500},
    ]
    usage = ask_agent.priced_usage(turns, PRICE)
    assert usage == {
        "input": 1_000,
        "cacheRead": 10_000,
        "cacheWrite": 0,
        "output": 500,
        "costUsd": pytest.approx(0.008),
    }


def test_an_openrouter_model_without_a_key_says_what_to_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="Set OPENROUTER_API_KEY"):
        ask_agent.chat_client("openrouter/anthropic/claude-sonnet-5.5")


async def test_an_answer_is_traced_with_its_priced_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-unused")
    store = TraceStore(tmp_path)
    agent = ask_agent.build_agent(
        RESULTS,
        model="openrouter/anthropic/claude-sonnet-5.5",
        price=PRICE,
        on_question=lambda q: store.start("commons", "question", q),
    )
    earlier = {"role": "assistant", "tokens": 9_999, "tokens_cached": 0, "tokens_total": 9_999}
    turns = [earlier]
    monkeypatch.setattr(agent, "get_tokens", lambda: list(turns))

    async def answer():
        # chatlas records the turns while the answer streams.
        turns.append({"role": "user", "tokens": 1_000, "tokens_cached": 0, "tokens_total": 1_000})
        yield "Ada "
        turns.append({"role": "assistant", "tokens": 100, "tokens_cached": 0, "tokens_total": 100})
        yield "merged one."

    trace = store.start("commons", "question", "Who?")
    chunks = [chunk async for chunk in agent._observe(answer(), trace)]
    assert chunks == ["Ada ", "merged one."]
    assert [e["kind"] for e in trace.events] == ["turn", "text", "text", "message_end"]
    assert trace.status == "done"
    assert trace.usage().cost_usd == pytest.approx((1_000 * 2 + 100 * 10) / 1e6)


async def test_a_failed_answer_marks_its_trace_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-unused")
    store = TraceStore(tmp_path)
    agent = ask_agent.build_agent(
        [],
        model="openrouter/x/y",
        price=PRICE,
        on_question=lambda q: store.start("commons", "question", q),
    )
    monkeypatch.setattr(agent, "get_tokens", list)

    async def broken():
        yield "partial"
        raise RuntimeError("401 Unauthorized")

    trace = store.start("commons", "question", "Who?")
    with pytest.raises(RuntimeError):
        _ = [chunk async for chunk in agent._observe(broken(), trace)]
    assert trace.status == "failed"
    assert trace.error == "401 Unauthorized"


@pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="needs OPENROUTER_API_KEY")
async def test_live_answer_uses_a_trusted_measure(tmp_path: Path) -> None:
    store = TraceStore(tmp_path)
    agent = ask_agent.build_agent(
        RESULTS,
        model="openrouter/anthropic/claude-sonnet-5.5",
        price=PRICE,
        on_question=lambda q: store.start("commons", "question", q),
    )
    _ = [c async for c in await agent.stream_async("Who merged pull requests?", content="all")]
    (trace,) = store.recent("commons")
    assert any(e["kind"] == "tool_end" and e.get("tag") == "A" for e in trace.events)
    assert trace.usage().tokens > 0
