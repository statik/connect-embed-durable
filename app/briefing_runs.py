"""Run the briefing agent for the app, one run at a time, and keep each result for history.

The agent's SQLite storage allows one process at a time, so runs take a file lock that also holds
across app processes. A second request for the same briefing waits, then gets the stored answer
from the agent without new model calls.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from briefing_config import BriefingConfig
from pi_briefing import stream_briefing


@dataclass(frozen=True)
class SavedBriefing:
    key: str
    date: str
    path: Path

    @property
    def label(self) -> str:
        return f"{self.date} · config {self.key.rsplit(':', 1)[-1][:6]}"


class BriefingRuns:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.results_dir = state_dir / "results"
        self._lock = asyncio.Lock()

    async def run(
        self,
        config: BriefingConfig,
        on_progress: Callable[[str], None],
        date: str | None = None,
    ) -> dict[str, Any]:
        """Write (or fetch the stored) briefing for `config`, and save the result."""
        async with self._lock:
            lock_file = await asyncio.to_thread(self._acquire_file_lock)
            try:
                result = await self._stream(config, on_progress, date)
            finally:
                lock_file.close()
        self._save(result)
        return result

    async def _stream(
        self, config: BriefingConfig, on_progress: Callable[[str], None], date: str | None
    ) -> dict[str, Any]:
        events = stream_briefing(config.to_agent(), state_dir=self.state_dir, date=date)
        async for event in events:
            if event["type"] == "progress":
                on_progress(event["message"])
            elif event["type"] == "result":
                return event
        raise RuntimeError("The agent finished without a result")

    def _acquire_file_lock(self):
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
