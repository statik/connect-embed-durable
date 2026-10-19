"""Live traces of agent work, for the Trace and Usage tabs.

Both agents report in one small vocabulary of events (`kind`: turn, text, thinking,
message_end, tool_start, tool_end, retry, error, note). The pi-durable agent emits it from its
committed events; `ask_agent.ObservedCommons` emits it from the commons stream. A trace lives in
memory while it runs, so every session can watch it, and in a JSON-lines file afterwards.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Display order on every page: commons first, as on the Ask page, where its chat box is.
AGENTS = ("commons", "pi-durable")


@dataclass(frozen=True)
class Usage:
    """Tokens by kind, and the cost estimated from the model catalog's prices."""

    input: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0
    cost_usd: float = 0.0
    calls: int = 0

    @classmethod
    def from_event(cls, usage: dict[str, Any]) -> Usage:
        return cls(
            input=int(usage.get("input", 0)),
            cache_read=int(usage.get("cacheRead", 0)),
            cache_write=int(usage.get("cacheWrite", 0)),
            output=int(usage.get("output", 0)),
            cost_usd=float(usage.get("costUsd", 0.0)),
            calls=1,
        )

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input + other.input,
            self.cache_read + other.cache_read,
            self.cache_write + other.cache_write,
            self.output + other.output,
            self.cost_usd + other.cost_usd,
            self.calls + other.calls,
        )

    @property
    def tokens(self) -> int:
        return self.input + self.cache_read + self.cache_write + self.output


@dataclass
class TraceLog:
    """One agent's work on one request: a briefing run or one question."""

    agent: str
    kind: str
    label: str
    model: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started: float = field(default_factory=time.time)
    events: list[dict[str, Any]] = field(default_factory=list)
    status: str = "running"
    error: str = ""
    path: Path | None = None

    def add(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        self._write({"event": event})

    def finish(self, error: str = "") -> None:
        self.status = "failed" if error else "done"
        self.error = error
        self._write({"status": self.status, "error": error})

    def usage(self) -> Usage:
        total = Usage()
        for event in self.events:
            if event.get("kind") == "message_end":
                total += Usage.from_event(event.get("usage", {}))
        return total

    def _write(self, line: dict[str, Any]) -> None:
        if self.path is None:
            return
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            header: dict[str, Any] = {"id": self.id, "agent": self.agent, "kind": self.kind}
            header |= {"label": self.label, "model": self.model, "started": self.started}
            self.path.write_text(json.dumps(header) + "\n")
        with self.path.open("a") as handle:
            handle.write(json.dumps(line) + "\n")


def load_trace(path: Path) -> TraceLog:
    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    header, rest = lines[0], lines[1:]
    trace = TraceLog(
        agent=header["agent"],
        kind=header["kind"],
        label=header["label"],
        model=header.get("model", ""),
        id=header["id"],
        started=header["started"],
    )
    for line in rest:
        if "event" in line:
            trace.events.append(line["event"])
        else:
            trace.status, trace.error = line["status"], line.get("error", "")
    if trace.status == "running":
        trace.status, trace.error = "failed", "The app stopped before this finished"
    return trace


class TraceStore:
    """Every trace this app process knows: live ones in memory, finished ones on disk."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._live: dict[str, TraceLog] = {}
        # Finished traces never change, so each file is read once.
        self._loaded: dict[str, TraceLog] = {}

    def start(self, agent: str, kind: str, label: str, model: str = "") -> TraceLog:
        """A new live trace. `kind` is "briefing" or "question"."""
        trace = TraceLog(agent=agent, kind=kind, label=label, model=model)
        trace.path = self.directory / f"{int(trace.started * 1000)}-{trace.id}.jsonl"
        self._live[trace.id] = trace
        return trace

    def recent(self, agent: str | None = None, limit: int = 50) -> list[TraceLog]:
        """Traces newest first, of one agent or of all; live ones come from memory."""
        found: dict[str, TraceLog] = dict(self._live)
        for path in self.directory.glob("*.jsonl"):
            trace_id = path.stem.split("-", 1)[1]
            if trace_id in found:
                continue
            if trace_id not in self._loaded:
                self._loaded[trace_id] = load_trace(path)
            found[trace_id] = self._loaded[trace_id]
        traces = [t for t in found.values() if agent is None or t.agent == agent]
        return sorted(traces, key=lambda t: t.started, reverse=True)[:limit]

    def running(self) -> list[TraceLog]:
        return [t for t in self._live.values() if t.status == "running"]


def fold(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold a trace's events into display blocks, merging streamed text and pairing tool calls."""
    blocks: list[dict[str, Any]] = []
    tools: dict[str, dict[str, Any]] = {}
    current: dict[str, dict[str, Any]] = {}
    for event in events:
        kind = event.get("kind")
        if kind == "turn":
            current = {}
            blocks.append({"type": "turn", "n": sum(b["type"] == "turn" for b in blocks) + 1})
        elif kind in ("text", "thinking"):
            block = current.get(kind)
            if block is None:
                block = current[kind] = {"type": kind, "text": ""}
                blocks.append(block)
            block["text"] += event.get("delta", "")
        elif kind == "message_end":
            _finish_message(blocks, current, event)
            current = {}
        elif kind in ("tool_start", "tool_end"):
            _tool(blocks, tools, event)
        else:
            blocks.append({"type": kind, "message": event.get("message", "")})
    return blocks


def _finish_message(blocks: list[dict[str, Any]], current: dict, event: dict[str, Any]) -> None:
    final = event.get("text", "")
    streamed = current.get("text")
    if final and streamed is None:
        blocks.append({"type": "text", "text": final})
    elif final and streamed is not None:
        streamed["text"] = final
    blocks.append({"type": "usage", "usage": Usage.from_event(event.get("usage", {}))})


def _tool(blocks: list[dict[str, Any]], tools: dict, event: dict[str, Any]) -> None:
    tool_id = event.get("id", "")
    block = tools.get(tool_id)
    if block is None:
        block = tools[tool_id] = {"type": "tool", "name": event.get("name", ""), "done": False}
        block |= {"args": {}, "output": "", "is_error": False, "tag": ""}
        blocks.append(block)
    if event["kind"] == "tool_start":
        block["args"] = event.get("args", {})
    else:
        block["done"] = True
        block["output"] = event.get("output", "")
        block["is_error"] = bool(event.get("isError"))
        block["tag"] = event.get("tag", "")
