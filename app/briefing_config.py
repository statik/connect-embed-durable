"""The briefing config: which repositories, what to watch for, and how far back to look.

It lives in a YAML file so people and agents can read, edit, diff, and commit it. The agent takes
the same content as JSON (`to_agent`), and checks it again on its side.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
MAX_REPOS = 20
LOOKBACK_CHOICES = {24: "Last 24 hours", 48: "Last 2 days", 72: "Last 3 days", 168: "Last 7 days"}


class ConfigError(ValueError):
    """The config file or a value in it is wrong; the message says how."""


@dataclass(frozen=True)
class Repo:
    name: str
    focus: str = ""


@dataclass(frozen=True)
class BriefingConfig:
    repos: tuple[Repo, ...] = field(default_factory=tuple)
    lookback_hours: int = 24
    instructions: str = ""

    def with_repo(self, name: str) -> BriefingConfig:
        """Add a repository, keeping order; adding one already present changes nothing."""
        check_repo_name(name)
        if any(r.name.lower() == name.lower() for r in self.repos):
            return self
        if len(self.repos) >= MAX_REPOS:
            raise ConfigError(f"A briefing covers at most {MAX_REPOS} repositories")
        return BriefingConfig((*self.repos, Repo(name)), self.lookback_hours, self.instructions)

    def keeping(self, names: list[str]) -> BriefingConfig:
        """Keep only the named repositories, with their focus notes, in their current order."""
        wanted = {n.lower() for n in names}
        kept = tuple(r for r in self.repos if r.name.lower() in wanted)
        return BriefingConfig(kept, self.lookback_hours, self.instructions)

    def to_agent(self) -> dict[str, Any]:
        """The JSON shape `pi-briefing run --config` reads."""
        repos = [{"name": r.name, **({"focus": r.focus} if r.focus else {})} for r in self.repos]
        agent: dict[str, Any] = {"repos": repos, "lookbackHours": self.lookback_hours}
        if self.instructions:
            agent["instructions"] = self.instructions
        return agent

    def to_yaml(self) -> str:
        repos = [{"name": r.name, **({"focus": r.focus} if r.focus else {})} for r in self.repos]
        data: dict[str, Any] = {"lookback_hours": self.lookback_hours, "repos": repos}
        if self.instructions:
            data["instructions"] = self.instructions
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def check_repo_name(name: str) -> None:
    if not REPO_NAME.match(name):
        raise ConfigError(f'"{name}" is not a repository name; use the form owner/repo')


def _text(value: object, where: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ConfigError(f"{where} must be text")
    return value.strip()


def _repo(value: object, index: int) -> Repo:
    if isinstance(value, str):
        value = {"name": value}
    if not isinstance(value, dict):
        raise ConfigError(f"repos[{index}] must be a name or a mapping with a name")
    name = _text(value.get("name"), f"repos[{index}].name")
    check_repo_name(name)
    return Repo(name, _text(value.get("focus"), f"repos[{index}].focus"))


def from_mapping(data: object) -> BriefingConfig:
    """Build a config from parsed YAML, checking every field."""
    if data is None:
        return BriefingConfig()
    if not isinstance(data, dict):
        raise ConfigError("The config must be a mapping with a repos list")
    raw_repos = data.get("repos") or []
    if not isinstance(raw_repos, list):
        raise ConfigError("repos must be a list")
    lookback = data.get("lookback_hours", 24)
    if lookback not in LOOKBACK_CHOICES:
        raise ConfigError(f"lookback_hours must be one of {', '.join(map(str, LOOKBACK_CHOICES))}")
    if len(raw_repos) > MAX_REPOS:
        raise ConfigError(f"repos lists {len(raw_repos)} repositories; the limit is {MAX_REPOS}")
    repos = tuple(_repo(raw, index) for index, raw in enumerate(raw_repos))
    seen: set[str] = set()
    for repo in repos:
        if repo.name.lower() in seen:
            raise ConfigError(f"repos lists {repo.name} twice")
        seen.add(repo.name.lower())
    instructions = _text(data.get("instructions"), "instructions")
    return BriefingConfig(repos, lookback, instructions)


def load_config(path: Path) -> BriefingConfig:
    """Read the config file; a missing file is an empty config, a broken one an error."""
    if not path.exists():
        return BriefingConfig()
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as error:
        raise ConfigError(f"{path} is not valid YAML: {error}") from error
    return from_mapping(data)


def save_config(config: BriefingConfig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(config.to_yaml())
    temporary.replace(path)
