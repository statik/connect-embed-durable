from pathlib import Path

import pytest

from app_state import VIEWER_HEADER, AppState
from briefing_config import BriefingConfig, ConfigError, Repo, save_config


def state(
    tmp_path: Path,
    *,
    state_dir: str = "state",
    model: str = "openrouter/anthropic/claude-sonnet-5.5",
    config_secret: str | None = None,
    require_viewer: bool = False,
) -> AppState:
    return AppState(
        state_dir=tmp_path / state_dir,
        model=model,
        github_token=None,
        config_secret=config_secret,
        require_viewer=require_viewer,
        default_config=tmp_path / "default.yaml",
    )


def test_config_comes_from_the_wizard_then_the_secret_then_the_default(tmp_path: Path) -> None:
    save_config(BriefingConfig((Repo("public/default"),)), tmp_path / "default.yaml")
    app = state(tmp_path, config_secret="repos: [private/secret]")
    assert app.load_config() == (
        BriefingConfig((Repo("private/secret"),)),
        "the BRIEFING_CONFIG_YAML secret",
    )
    save_config(BriefingConfig((Repo("saved/here"),)), app.saved_config)
    assert app.load_config()[0].repos == (Repo("saved/here"),)
    assert state(tmp_path, state_dir="fresh").load_config() == (
        BriefingConfig((Repo("public/default"),)),
        "the default in the repository",
    )


def test_a_broken_secret_names_itself(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="BRIEFING_CONFIG_YAML is not valid YAML"):
        state(tmp_path, config_secret="repos: [unclosed").load_config()


def test_viewers_need_a_connect_session_only_when_required(tmp_path: Path) -> None:
    assert state(tmp_path).viewer_allowed({})
    guarded = state(tmp_path, require_viewer=True)
    assert not guarded.viewer_allowed({})
    assert not guarded.viewer_allowed({VIEWER_HEADER: ""})
    assert guarded.viewer_allowed({VIEWER_HEADER: "header.payload.signature"})


def test_the_price_comes_from_the_agents_catalog(tmp_path: Path) -> None:
    assert state(tmp_path).price == {"input": 2, "output": 10, "cacheRead": 0.1, "cacheWrite": 2.5}
    assert state(tmp_path, model="openrouter/no/such-model").price is None
