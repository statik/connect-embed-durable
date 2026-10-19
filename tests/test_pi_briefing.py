"""The installed wheel: its bundled executable and the Python wrapper around it.

These run the real compiled agent. The end-to-end test also calls a real model and GitHub, as
commons tests do, and is skipped without an API key.
"""

import os
import subprocess
from pathlib import Path

import pytest

import pi_briefing
from pi_briefing import BriefingError, binary_path, model_price, stream_ask, stream_briefing


async def collect(config: dict, state_dir: Path, **options: str) -> list[dict]:
    return [event async for event in stream_briefing(config, state_dir=state_dir, **options)]


def test_wheel_ships_an_executable_of_the_same_version() -> None:
    binary = binary_path()
    assert os.access(binary, os.X_OK)
    version = subprocess.run([binary, "--version"], capture_output=True, text=True, check=True)
    assert version.stdout.strip() == pi_briefing.__version__


def test_console_script_runs_the_executable() -> None:
    result = subprocess.run(
        ["pi-briefing", "--version"], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == pi_briefing.__version__


async def test_a_bad_config_is_a_usage_error_with_the_agents_message(tmp_path: Path) -> None:
    with pytest.raises(
        BriefingError, match=r'repos\[0\]\.name must look like "owner/repo"'
    ) as error:
        await collect({"repos": [{"name": "nope"}]}, tmp_path)
    assert error.value.usage_error


async def test_a_bad_model_name_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(BriefingError, match="--model must look like") as error:
        await collect({"repos": [{"name": "a/b"}]}, tmp_path, model="no-slash")
    assert error.value.usage_error


async def test_a_missing_model_key_is_a_usage_error_naming_the_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("BRIEFING_MODEL", raising=False)
    with pytest.raises(BriefingError, match="usually as OPENROUTER_API_KEY") as error:
        await collect({"repos": [{"name": "a/b"}]}, tmp_path)
    assert error.value.usage_error
    assert not (tmp_path / "briefings.sqlite").exists()


async def test_a_future_date_fails_without_calling_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-unused")
    with pytest.raises(BriefingError, match="is in the future") as error:
        await collect({"repos": [{"name": "a/b"}]}, tmp_path, date="2999-01-01")
    assert not error.value.usage_error


def test_model_price_is_the_catalog_price_per_million_tokens() -> None:
    assert model_price("openrouter/anthropic/claude-sonnet-5.5") == {
        "input": 2,
        "output": 10,
        "cacheRead": 0.1,
        "cacheWrite": 2.5,
    }
    with pytest.raises(BriefingError, match="No model openrouter/no/such") as error:
        model_price("openrouter/no/such")
    assert error.value.usage_error


async def test_asking_without_a_briefing_is_a_usage_error(tmp_path: Path) -> None:
    events = stream_ask(
        state_dir=tmp_path, key="briefing:2026-10-18:x", question="q", request_id="r"
    )
    with pytest.raises(BriefingError, match="There is no briefing") as error:
        _ = [event async for event in events]
    assert error.value.usage_error


@pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="needs OPENROUTER_API_KEY")
async def test_writes_a_real_briefing_then_serves_it_from_storage(tmp_path: Path) -> None:
    config = {"repos": [{"name": "posit-dev/commons"}], "lookbackHours": 168}
    first = await collect(config, tmp_path)
    result = first[-1]
    assert result["type"] == "result"
    assert result["resumed"] is False
    assert "posit-dev/commons" in result["markdown"]
    assert any(e["type"] == "trace" and e["event"]["kind"] == "tool_start" for e in first)
    assert result["items"]
    assert result["usage"]["output"] > 0

    again = (await collect(config, tmp_path))[-1]
    assert again["resumed"] is True
    assert again["markdown"] == result["markdown"]
