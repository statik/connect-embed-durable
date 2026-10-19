"""Run the pi-durable briefing agent from Python.

The agent is TypeScript, compiled with Bun into one self-contained executable per platform. This
package ships that executable in a platform wheel, the way go-to-wheel ships Go binaries, so a
Python host such as Posit Connect Cloud needs no Node.js.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

__version__ = "0.2.1"
__all__ = [
    "BriefingError",
    "binary_path",
    "main",
    "model_price",
    "stream_ask",
    "stream_briefing",
]

# The agent prints one JSON object per line; a briefing result can be long.
_LINE_LIMIT = 16 * 1024 * 1024
_USAGE_EXIT_CODE = 2


class BriefingError(RuntimeError):
    """The agent failed. `usage_error` means something must change before a retry can work."""

    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code

    @property
    def usage_error(self) -> bool:
        return self.exit_code == _USAGE_EXIT_CODE


def binary_path() -> Path:
    """Return the bundled `pi-briefing` executable, making sure it is executable.

    Raises:
        FileNotFoundError: The wheel for this platform was not installed.
        PermissionError: The file is not executable and this user may not change that.
    """
    binary = Path(__file__).parent / "bin" / "pi-briefing"
    if not binary.exists():
        raise FileNotFoundError(
            f"{binary} is missing: install the pi-briefing wheel built for this platform"
        )
    if os.access(binary, os.X_OK):
        return binary
    try:
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except PermissionError as error:
        raise PermissionError(
            f"{binary} is not executable, and this user cannot change that. The installer "
            "dropped its execute bit; reinstall pi-briefing 0.2.1 or later, whose wheels "
            "mark it so that pip keeps it."
        ) from error
    return binary


async def stream_briefing(
    config: Mapping[str, Any],
    *,
    state_dir: Path,
    date: str | None = None,
    model: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Write one briefing, yielding the agent's events as they arrive.

    Trace events look like `{"type": "trace", "event": {"kind": ...}}`. The last event is
    `{"type": "result", ...}`. The run is durable: if it is interrupted, calling this again with
    the same config and date resumes it, and a finished briefing comes back without model calls.
    Only one run may use a `state_dir` at a time.

    Args:
        config: The briefing config: `repos`, `lookbackHours`, and optional `instructions`.
        state_dir: Where the agent keeps its SQLite storage.
        date: The day to brief, `YYYY-MM-DD` in UTC; today when omitted.
        model: `provider/model`; `BRIEFING_MODEL` or the agent's default when omitted.

    Raises:
        BriefingError: The agent reported an error, or exited without a result.
    """
    options = [] if date is None else ["--date", date]
    options += [] if model is None else ["--model", model]
    with tempfile.TemporaryDirectory(prefix="briefing-config-") as scratch:
        config_path = Path(scratch) / "config.json"
        config_path.write_text(json.dumps(dict(config)))
        async for event in _stream(["run", "--config", str(config_path), *options], state_dir):
            yield event


async def stream_ask(
    *, state_dir: Path, key: str, question: str, request_id: str
) -> AsyncIterator[dict[str, Any]]:
    """Ask a follow-up question in a briefing's own conversation, yielding the agent's events.

    `key` is the briefing's key, from its result. Asking again with the same `request_id`
    returns the stored answer. Events are as for `stream_briefing`.

    Raises:
        BriefingError: The agent reported an error; `usage_error` when there is no such briefing.
    """
    options = ["--key", key, "--question", question, "--request-id", request_id]
    async for event in _stream(["ask", *options], state_dir):
        yield event


def model_price(model: str | None = None) -> dict[str, float]:
    """The model's catalog price in USD per million tokens: input, output, cacheRead, cacheWrite.

    Raises:
        BriefingError: The model is not in the catalog.
    """
    command = [str(binary_path()), "price", *([] if model is None else ["--model", model])]
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    event = json.loads(done.stdout.splitlines()[-1]) if done.stdout else {}
    if done.returncode != 0 or event.get("type") != "price":
        raise BriefingError(event.get("message", done.stderr.strip()), done.returncode)
    return event["perMillionUsd"]


async def _stream(args: list[str], state_dir: Path) -> AsyncIterator[dict[str, Any]]:
    state_dir.mkdir(parents=True, exist_ok=True)
    process = await asyncio.create_subprocess_exec(
        str(binary_path()),
        *args,
        "--state",
        str(state_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=_LINE_LIMIT,
    )
    try:
        async for event in _events(process):
            yield event
    finally:
        if process.returncode is None:
            process.terminate()
            await process.wait()


async def _events(process: asyncio.subprocess.Process) -> AsyncIterator[dict[str, Any]]:
    assert process.stdout is not None and process.stderr is not None
    error: str | None = None
    finished = False
    async for line in process.stdout:
        event = json.loads(line)
        if event.get("type") == "error":
            error = str(event.get("message", "unknown error"))
            continue
        finished = finished or event.get("type") == "result"
        yield event
    stderr = (await process.stderr.read()).decode(errors="replace").strip()
    exit_code = await process.wait()
    if error is not None or exit_code != 0 or not finished:
        message = error or stderr or f"pi-briefing exited with code {exit_code} and no result"
        raise BriefingError(message, exit_code)


def main() -> None:
    """Run the bundled executable with this process's arguments, as the `pi-briefing` command."""
    binary = str(binary_path())
    if sys.platform == "win32":
        raise SystemExit("pi-briefing has no Windows build")
    os.execv(binary, [binary, *sys.argv[1:]])
