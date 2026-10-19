from pathlib import Path

import pytest

from briefing_config import (
    MAX_REPOS,
    BriefingConfig,
    ConfigError,
    Repo,
    from_mapping,
    load_config,
    save_config,
)


def test_round_trips_through_yaml(tmp_path: Path) -> None:
    config = BriefingConfig(
        (Repo("posit-dev/commons", "provenance"), Repo("earendil-works/pi")), 48, "Skip bots."
    )
    path = tmp_path / "briefing.yaml"
    save_config(config, path)
    assert load_config(path) == config


def test_the_committed_default_config_is_valid() -> None:
    config = load_config(Path(__file__).parent.parent / "app" / "briefing.yaml")
    assert [r.name for r in config.repos] == ["earendil-works/pi", "posit-dev/commons"]


def test_missing_file_is_an_empty_config(tmp_path: Path) -> None:
    assert load_config(tmp_path / "nope.yaml") == BriefingConfig()


def test_accepts_bare_repository_names() -> None:
    assert from_mapping({"repos": ["a/b"]}).repos == (Repo("a/b"),)


def test_agent_json_omits_empty_fields() -> None:
    config = BriefingConfig((Repo("a/b", "releases"), Repo("c/d")), 24, "")
    assert config.to_agent() == {
        "repos": [{"name": "a/b", "focus": "releases"}, {"name": "c/d"}],
        "lookbackHours": 24,
    }


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([], "must be a mapping"),
        ({"repos": "a/b"}, "repos must be a list"),
        ({"repos": ["not a repo"]}, "use the form owner/repo"),
        ({"repos": [{"focus": "x"}]}, "use the form owner/repo"),
        ({"repos": [{"name": "a/b", "focus": 1}]}, "focus must be text"),
        ({"repos": ["a/b", "A/B"]}, "lists A/B twice"),
        ({"repos": ["a/b"], "lookback_hours": 5}, "lookback_hours must be one of"),
        ({"repos": [f"o/r{i}" for i in range(MAX_REPOS + 1)]}, "the limit is 20"),
    ],
)
def test_rejects_bad_configs(data: object, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        from_mapping(data)


def test_broken_yaml_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "briefing.yaml"
    path.write_text("repos: [unclosed")
    with pytest.raises(ConfigError, match="is not valid YAML"):
        load_config(path)


def test_adding_keeps_order_ignores_duplicates_and_enforces_the_limit() -> None:
    config = BriefingConfig().with_repo("a/b").with_repo("c/d").with_repo("A/B")
    assert [r.name for r in config.repos] == ["a/b", "c/d"]
    full = BriefingConfig(tuple(Repo(f"o/r{i}") for i in range(MAX_REPOS)))
    with pytest.raises(ConfigError, match="at most 20"):
        full.with_repo("x/y")


def test_keeping_preserves_focus_notes() -> None:
    config = BriefingConfig((Repo("a/b", "focus"), Repo("c/d")))
    assert config.keeping(["A/B"]).repos == (Repo("a/b", "focus"),)
