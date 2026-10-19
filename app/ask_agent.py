"""The commons agent that answers questions about saved briefings.

Its data is every saved briefing's items and per-repository counts, in DuckDB. Trusted
calculations (measures) answer the common questions; commons' SQL path answers the rest, and
each answer carries a commons provenance outcome. `ObservedCommons` copies the stream into a
live trace, in the same event vocabulary the pi-durable agent uses.
"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping, Sequence
from functools import cache
from pathlib import Path
from tempfile import mkdtemp
from typing import Annotated, Any

import chatlas
import commons
import pandas as pd
from pydantic import Field

from traces import TraceLog

OUTPUT_PREVIEW_CHARS = 4000
_ASIDE_LABEL = re.compile(r'<shiny-aside[^>]*\blabel="([^"]*)"')

NOTES = """\
The data comes from daily GitHub briefings. Each briefing covers a time window (usually the 24
hours before it was written) for a list of repositories.

The items table has one row per thing that happened in a briefing's window. kind is one of
pr_merged (a pull request merged), pr_opened (a pull request opened and not merged in the
window), issue_opened, issue_closed, or release. happened_at is when it happened, in UTC.
number is the pull request or issue number, empty for releases. labels is a comma-separated
list. url identifies an item.

When briefing windows overlap, one item can appear in more than one briefing. To count things
that happened, count distinct url and kind, not rows.

The repos table has one row per repository per briefing: commits on the default branch in the
window, and truncated, which is true when GitHub's page limit cut a list short so counts are
lower bounds.
"""


def briefing_frames(results: list[dict[str, Any]]) -> dict[str, pd.DataFrame]:
    """The `items` and `repos` tables, from saved briefing results."""
    items, repos = [], []
    for result in results:
        date = result["date"]
        for item in result.get("items", []):
            labels = ", ".join(item.get("labels", []))
            row = {key: value for key, value in item.items() if key != "at"}
            items.append(
                {"briefing_date": date, **row, "happened_at": item["at"], "labels": labels}
            )
        for repo, activity in result.get("activity", {}).items():
            repos.append(
                {
                    "briefing_date": date,
                    "repo": repo,
                    "commits": activity["commits"],
                    "truncated": activity["truncated"],
                }
            )
    item_columns = ["briefing_date", "repo", "kind", "number", "title", "author", "happened_at"]
    item_columns += ["labels", "url"]
    repo_columns = ["briefing_date", "repo", "commits", "truncated"]
    return {
        "items": pd.DataFrame(items, columns=item_columns).astype({"number": "Int64"}),
        "repos": pd.DataFrame(repos, columns=repo_columns),
    }


@commons.measure(
    description=(
        "Pull requests, issues, and releases in each repository, by kind, from the most recent "
        "briefing only."
    )
)
def latest_activity(briefings: commons.Injected[Any]) -> pd.DataFrame:
    return briefings.execute(
        """
        SELECT repo, kind, COUNT(*) AS items
        FROM items
        WHERE briefing_date = (SELECT MAX(briefing_date) FROM items)
        GROUP BY repo, kind ORDER BY repo, kind
        """
    ).fetchdf()


@commons.measure(
    description=(
        "People ranked by how many distinct pull requests, issues, and releases they authored "
        "across all briefings. Each item counts once even when briefings overlap."
    )
)
def top_contributors(
    briefings: commons.Injected[Any],
    kind: Annotated[
        str | None,
        Field(
            description="Only this kind: pr_merged, pr_opened, issue_opened, issue_closed, release."
        ),
    ] = None,
) -> pd.DataFrame:
    return briefings.execute(
        """
        SELECT author, COUNT(DISTINCT kind || ' ' || url) AS items
        FROM items WHERE $kind IS NULL OR kind = $kind
        GROUP BY author ORDER BY items DESC, author LIMIT 15
        """,
        {"kind": kind},
    ).fetchdf()


@commons.measure(
    description=(
        "Merged pull requests across all briefings, newest first, each listed once, with title, "
        "author, labels, and link."
    )
)
def merged_pull_requests(
    briefings: commons.Injected[Any],
    repo: Annotated[str | None, Field(description="Only this repository, as owner/repo.")] = None,
) -> pd.DataFrame:
    return briefings.execute(
        """
        SELECT DISTINCT repo, number, title, author, labels, happened_at AS merged_at, url
        FROM items WHERE kind = 'pr_merged' AND ($repo IS NULL OR repo = $repo)
        ORDER BY merged_at DESC
        """,
        {"repo": repo},
    ).fetchdf()


@cache
def _notes_file() -> Path:
    path = Path(mkdtemp(prefix="briefing-notes-")) / "briefing-data-notes.md"
    path.write_text(NOTES)
    return path


def chat_client(model: str) -> chatlas.Chat:
    """A chatlas client for a `provider/model` name in the agent's format."""
    provider, _, name = model.partition("/")
    if provider == "openrouter":
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise ValueError("No OpenRouter API key found. Set OPENROUTER_API_KEY.")
        return chatlas.ChatOpenRouter(model=name, api_key=key)
    return chatlas.ChatAuto(model)


def _preview(value: object) -> str:
    text = str(value)
    if len(text) <= OUTPUT_PREVIEW_CHARS:
        return text
    return f"{text[:OUTPUT_PREVIEW_CHARS]}\n… {len(text) - OUTPUT_PREVIEW_CHARS:,} more characters"


def chunk_events(chunk: object) -> list[dict[str, Any]]:
    """The trace events one commons stream chunk stands for."""
    if isinstance(chunk, chatlas.ContentToolRequest):
        return [{"kind": "tool_start", "id": chunk.id, "name": chunk.name, "args": chunk.arguments}]
    if isinstance(chunk, chatlas.ContentToolResult):
        request = chunk.request
        failed = chunk.error is not None
        return [
            {
                "kind": "tool_end",
                "id": request.id if request else "",
                "name": request.name if request else "",
                "isError": failed,
                "output": _preview(chunk.error if failed else chunk.value),
                "tag": str((chunk.extra or {}).get("commons_tag", "")),
            }
        ]
    if isinstance(chunk, str):
        label = _ASIDE_LABEL.search(chunk)
        if label:
            return [{"kind": "note", "message": f"Provenance: {label.group(1)}"}]
        return [{"kind": "text", "delta": chunk}] if chunk else []
    return []


def priced_usage(turns: Sequence[Mapping[str, Any]], price: dict[str, float]) -> dict[str, Any]:
    """Usage of chatlas turns, priced per million tokens like the pi-durable agent's."""
    fresh = sum(t["tokens"] for t in turns if t["role"] == "user")
    cached = sum(t.get("tokens_cached", 0) for t in turns if t["role"] == "user")
    output = sum(t["tokens"] for t in turns if t["role"] == "assistant")
    cost = fresh * price["input"] + cached * price["cacheRead"] + output * price["output"]
    usage = {"input": fresh, "cacheRead": cached, "cacheWrite": 0, "output": output}
    return usage | {"costUsd": cost / 1_000_000}


class ObservedCommons(commons.Commons):
    """A commons agent whose every answer is also written to a live trace."""

    def __init__(
        self,
        *args: Any,
        on_question: Callable[[str], TraceLog],
        price: dict[str, float],
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._on_question = on_question
        self._price = price

    async def stream_async(self, *args: Any, **kwargs: Any) -> AsyncGenerator[Any]:
        stream = await super().stream_async(*args, **kwargs)
        question = next((a for a in args if isinstance(a, str)), "")
        return self._observe(stream, self._on_question(question))

    async def _observe(self, stream: AsyncIterator[Any], trace: TraceLog) -> AsyncGenerator[Any]:
        turns_before = len(self.get_tokens())
        trace.add({"kind": "turn"})
        try:
            async for chunk in stream:
                for event in chunk_events(chunk):
                    trace.add(event)
                yield chunk
        except BaseException as error:
            trace.finish(str(error) or type(error).__name__)
            raise
        usage = priced_usage(self.get_tokens()[turns_before:], self._price)
        trace.add({"kind": "message_end", "text": "", "stopReason": "stop", "usage": usage})
        trace.finish()


def build_agent(
    results: list[dict[str, Any]],
    *,
    model: str,
    price: dict[str, float],
    on_question: Callable[[str], TraceLog],
) -> ObservedCommons:
    """A commons agent over the saved briefings, writing each answer's work to a trace."""
    source = commons.data_source(**briefing_frames(results))
    return ObservedCommons(
        client=chat_client(model),
        data_sources={"briefings": source},
        semantic_layer=commons.semantic_layer(
            latest_activity, top_contributors, merged_pull_requests
        ),
        context_layer=commons.context_layer(files=[_notes_file()]),
        on_question=on_question,
        price=price,
    )
