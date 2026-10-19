"""Run the pi-durable agent for the app, one call at a time, with a live trace of each.

The agent's SQLite storage allows one process at a time, so every call takes a file lock that
also holds across app processes. A second request for the same briefing waits, then gets the
stored answer from the agent without new model calls.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from briefing_config import BriefingConfig
from pi_briefing import stream_ask, stream_briefing
from traces import TraceLog, TraceStore


@dataclass(frozen=True)
class SavedBriefing:
    key: str
    date: str
    path: Path

    @property
    def label(self) -> str:
        return f"{self.date} · config {self.key.rsplit(':', 1)[-1][:6]}"


class BriefingRuns:
    def __init__(self, state_dir: Path, traces: TraceStore) -> None:
        self.state_dir = state_dir
        self.results_dir = state_dir / "results"
        self.traces = traces
        self._lock = asyncio.Lock()

    async def run(self, config: BriefingConfig, date: str | None = None) -> dict[str, Any]:
        """Write (or fetch the stored) briefing for `config`, trace it, and save the result."""
        day = date or datetime.now(UTC).date().isoformat()
        label = f"Briefing {day}: {', '.join(r.name for r in config.repos)}"
        trace = self.traces.start("pi-durable", "briefing", label)
        events = stream_briefing(config.to_agent(), state_dir=self.state_dir, date=date)
        result = await self._traced(events, trace)
        self._save(result)
        return result

    async def ask(self, key: str, question: str, request_id: str) -> dict[str, Any]:
        """Ask a follow-up question in the conversation of briefing `key`, and trace it."""
        trace = self.traces.start("pi-durable", "question", f"Q: {question}")
        events = stream_ask(
            state_dir=self.state_dir, key=key, question=question, request_id=request_id
        )
        return await self._traced(events, trace)

    async def _traced(self, events: AsyncIterator[dict[str, Any]], trace: TraceLog) -> dict:
        if self._lock.locked():
            trace.add({"kind": "note", "message": "Waiting for another agent call to finish"})
        try:
            async with self._lock:
                handle = await asyncio.to_thread(self._acquire_file_lock)
                try:
                    result = await self._consume(events, trace)
                finally:
                    handle.close()
        except BaseException as error:
            trace.finish(str(error) or type(error).__name__)
            raise
        trace.model = result.get("model", "")
        trace.finish()
        return result

    @staticmethod
    async def _consume(events: AsyncIterator[dict[str, Any]], trace: TraceLog) -> dict[str, Any]:
        async for event in events:
            if event["type"] == "trace":
                trace.add(event["event"])
            elif event["type"] == "result":
                return event
        raise RuntimeError("The agent finished without a result")

    def _acquire_file_lock(self) -> IO[str]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        handle = (self.state_dir / "run.lock").open("w")
        fcntl.flock(handle, fcntl.LOCK_EX)
        return handle

    def _save(self, result: dict[str, Any]) -> None:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        name = result["key"].replace(":", "--") + ".json"
        (self.results_dir / name).write_text(json.dumps(result, indent=2))

    def history(self) -> list[SavedBriefing]:
        """Saved briefings, newest day first."""
        saved = []
        for path in self.results_dir.glob("briefing--*.json"):
            key = path.stem.replace("--", ":")
            saved.append(SavedBriefing(key=key, date=key.split(":")[1], path=path))
        return sorted(saved, key=lambda s: (s.date, s.path.stat().st_mtime), reverse=True)

    def load(self, key: str) -> dict[str, Any]:
        path = self.results_dir / (key.replace(":", "--") + ".json")
        return json.loads(path.read_text())

    def results(self) -> list[dict[str, Any]]:
        """Every saved briefing result, for the commons agent's data."""
        return [json.loads(saved.path.read_text()) for saved in self.history()]
