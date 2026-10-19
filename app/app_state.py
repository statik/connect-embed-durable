"""What every page and session shares: settings from the environment, the runner, the traces.

Environment:
    OPENROUTER_API_KEY       the key for the default model; see BRIEFING_MODEL
    BRIEFING_MODEL           provider/model for both agents, openrouter/anthropic/claude-sonnet-5.5
                             by default
    GITHUB_TOKEN             optional; reads private repositories and raises GitHub's rate limit
    BRIEFING_CONFIG_YAML     optional; the config as YAML, for configs naming private repositories
    BRIEFING_REQUIRE_VIEWER  set to 1 to show nothing unless Posit Connect identifies the viewer
    BRIEFING_STATE_DIR       storage, results, traces, and saved config; app/state by default
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from briefing_config import BriefingConfig, load_active_config
from briefing_runs import BriefingRuns
from pi_briefing import BriefingError, model_price
from traces import TraceStore

APP_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL = "openrouter/anthropic/claude-sonnet-5.5"
VIEWER_HEADER = "posit-connect-user-session-token"


@dataclass
class AppState:
    state_dir: Path
    model: str
    github_token: str | None
    config_secret: str | None
    require_viewer: bool
    default_config: Path = APP_DIR / "briefing.yaml"
    traces: TraceStore = field(init=False)
    runs: BriefingRuns = field(init=False)

    def __post_init__(self) -> None:
        self.traces = TraceStore(self.state_dir / "traces")
        self.runs = BriefingRuns(self.state_dir, self.traces)

    @classmethod
    def from_environment(cls) -> AppState:
        state_dir = os.environ.get("BRIEFING_STATE_DIR") or APP_DIR / "state"
        return cls(
            state_dir=Path(state_dir),
            model=os.environ.get("BRIEFING_MODEL") or DEFAULT_MODEL,
            github_token=os.environ.get("GITHUB_TOKEN") or None,
            config_secret=os.environ.get("BRIEFING_CONFIG_YAML") or None,
            require_viewer=os.environ.get("BRIEFING_REQUIRE_VIEWER") == "1",
        )

    @property
    def saved_config(self) -> Path:
        return self.state_dir / "briefing.yaml"

    def load_config(self) -> tuple[BriefingConfig, str]:
        """The config in effect and where it came from; raises `ConfigError` if it is broken."""
        return load_active_config(self.saved_config, self.config_secret, self.default_config)

    @cached_property
    def price(self) -> dict[str, float] | None:
        """The model's catalog price per million tokens, or None if the catalog lacks it."""
        try:
            return model_price(self.model)
        except BriefingError:
            return None

    def viewer_allowed(self, headers: dict[str, str]) -> bool:
        """Whether to show anything: always, unless the viewer must be identified by Connect."""
        return not self.require_viewer or bool(headers.get(VIEWER_HEADER))
