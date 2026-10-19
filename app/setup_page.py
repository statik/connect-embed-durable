"""The Set up page: a three-step wizard that builds the briefing config."""

from __future__ import annotations

import re
from typing import Any

from shiny import Inputs, reactive, render, ui

from app_state import AppState
from briefing_config import (
    LOOKBACK_CHOICES,
    BriefingConfig,
    ConfigError,
    Repo,
    check_repo_name,
    save_config,
)
from github_lookup import GitHubLookupError, list_owner_repos, lookup_repo


def focus_id(name: str) -> str:
    return "focus_" + re.sub(r"\W", "_", name.lower())


def _step_nav(back: str | None, forward: str | None) -> ui.Tag:
    buttons = []
    if back:
        buttons.append(ui.input_action_button(back, "Back", class_="btn-outline-secondary"))
    if forward:
        buttons.append(ui.input_action_button(forward, "Next", class_="btn-primary"))
    return ui.div(*buttons, class_="d-flex gap-2 mt-3")


def _repos_step(names: list[str], has_token: bool):
    """Step 1. The list starts with the saved repositories, so the page never shows it empty."""
    reach = (
        "public repositories, and private ones GITHUB_TOKEN can read"
        if has_token
        else "public repositories (set GITHUB_TOKEN to add private ones)"
    )
    return ui.nav_panel(
        "Repositories",
        ui.h4("1. Choose repositories"),
        ui.p(f"Add {reach}, one at a time or from an organization or user."),
        ui.layout_columns(
            ui.card(
                ui.card_header("Add a repository"),
                ui.input_text("repo_name", "Repository", placeholder="owner/repo"),
                ui.input_action_button("check_repo", "Check and add"),
                ui.output_ui("repo_check"),
            ),
            ui.card(
                ui.card_header("Browse an organization or user"),
                ui.input_text("owner", "Organization or user", placeholder="e.g. posit-dev"),
                ui.input_action_button("browse", "List recently active repositories"),
                ui.output_ui("browse_results"),
            ),
        ),
        ui.input_selectize(
            "selected_repos",
            "In this briefing",
            choices=names,
            selected=names,
            multiple=True,
            options={"plugins": ["remove_button"]},
            width="100%",
        ),
        _step_nav(None, "to_focus"),
        value="repos",
    )


_FOCUS_STEP = ui.nav_panel(
    "Focus",
    ui.h4("2. Say what matters"),
    ui.p("A focus note tells the agent what to put first for that repository. All are optional."),
    ui.output_ui("focus_inputs"),
    ui.input_select("lookback", "Cover", {str(k): v for k, v in LOOKBACK_CHOICES.items()}),
    ui.input_text_area(
        "instructions",
        "Anything else the writer should know",
        placeholder="e.g. I maintain the R package; skip dependency bumps.",
        width="100%",
    ),
    _step_nav("back_to_repos", "to_review"),
    value="focus",
)

_REVIEW_STEP = ui.nav_panel(
    "Review",
    ui.h4("3. Review and save"),
    ui.p("The config is a small YAML file. Saving keeps it in the app's state folder."),
    ui.output_code("config_yaml"),
    ui.help_text(
        "On Posit Connect Cloud the app's files reset when it restarts. To keep this config, "
        "download it and either commit it as app/briefing.yaml (public repositories only) or "
        "paste it into the BRIEFING_CONFIG_YAML secret (required when it names private "
        "repositories, because this code repository is public)."
    ),
    ui.div(
        ui.input_action_button("back_to_focus", "Back", class_="btn-outline-secondary"),
        ui.download_button("download_config", "Download briefing.yaml"),
        ui.input_action_button("save", "Save and write today's briefing", class_="btn-primary"),
        class_="d-flex gap-2 mt-3",
    ),
    value="review",
)


def setup_panel(names: list[str], has_token: bool):
    wizard = ui.navset_hidden(_repos_step(names, has_token), _FOCUS_STEP, _REVIEW_STEP, id="wizard")
    return ui.nav_panel("Set up", wizard)


def setup_server(input: Inputs, state: AppState, saved: reactive.Value[BriefingConfig]) -> None:
    with reactive.isolate():
        draft = reactive.value(saved.get())
    _repos_server(input, state, draft)

    @render.ui
    def focus_inputs() -> ui.TagList:
        repos = draft.get().repos
        return ui.TagList(
            *[ui.input_text(focus_id(r.name), r.name, value=r.focus, width="100%") for r in repos]
        )

    def collect_details() -> None:
        config = draft.get()
        repos = tuple(Repo(r.name, (input[focus_id(r.name)]() or "").strip()) for r in config.repos)
        instructions = (input.instructions() or "").strip()
        draft.set(BriefingConfig(repos, int(input.lookback()), instructions))

    @reactive.effect
    def _load_details() -> None:
        with reactive.isolate():
            config = draft.get()
        ui.update_select("lookback", selected=str(config.lookback_hours))
        ui.update_text_area("instructions", value=config.instructions)

    @reactive.effect
    @reactive.event(input.to_focus)
    def _to_focus() -> None:
        if not draft.get().repos:
            ui.notification_show("Add at least one repository first.", type="warning")
            return
        ui.update_navset("wizard", selected="focus")

    @reactive.effect
    @reactive.event(input.to_review)
    def _to_review() -> None:
        collect_details()
        ui.update_navset("wizard", selected="review")

    @reactive.effect
    @reactive.event(input.back_to_repos)
    def _back_to_repos() -> None:
        collect_details()
        ui.update_navset("wizard", selected="repos")

    @reactive.effect
    @reactive.event(input.back_to_focus)
    def _back_to_focus() -> None:
        ui.update_navset("wizard", selected="focus")

    @render.code
    def config_yaml() -> str:
        return draft.get().to_yaml()

    @render.download_button(filename="briefing.yaml")
    def download_config():
        yield draft.get().to_yaml()

    @reactive.effect
    @reactive.event(input.save)
    def _save() -> None:
        try:
            save_config(draft.get(), state.saved_config)
        except OSError as error:
            message = f"Could not save {state.saved_config}: {error}"
            ui.notification_show(message, type="error", duration=None)
            return
        saved.set(draft.get())
        ui.update_navset("page", selected="Briefing")
        ui.notification_show("Saved. Writing today's briefing.", type="message")


def _repo_label(repo: Any) -> ui.Tag:
    private = ui.span(" private", class_="badge text-bg-warning ms-1") if repo.private else ""
    return ui.span(
        ui.tags.strong(repo.name),
        private,
        f" · pushed {repo.pushed_at} · ★ {repo.stars:,}",
        ui.div(repo.description, class_="small text-muted"),
    )


def _repos_server(input: Inputs, state: AppState, draft: reactive.Value[BriefingConfig]) -> None:
    check_message: reactive.Value[ui.TagChild] = reactive.value(None)
    found: reactive.Value[list[Any]] = reactive.value([])

    def add(name: str) -> None:
        draft.set(draft.get().with_repo(name))

    @reactive.effect
    def _sync_selectize() -> None:
        names = [r.name for r in draft.get().repos]
        ui.update_selectize("selected_repos", choices=names, selected=names)

    @reactive.effect
    @reactive.event(input.selected_repos, ignore_none=False)
    def _removed() -> None:
        kept = list(input.selected_repos() or [])
        if {n.lower() for n in kept} != {r.name.lower() for r in draft.get().repos}:
            draft.set(draft.get().keeping(kept))

    @reactive.effect
    @reactive.event(input.check_repo)
    async def _check() -> None:
        name = (input.repo_name() or "").strip().removeprefix("https://github.com/").strip("/")
        try:
            check_repo_name(name)
            info = await lookup_repo(name, state.github_token)
            add(info.name)
        except (ConfigError, GitHubLookupError) as error:
            check_message.set(ui.div(str(error), class_="text-danger small mt-2"))
            return
        notes = " It is archived, so expect no activity." if info.archived else ""
        notes += " It is private: keep this config in BRIEFING_CONFIG_YAML." if info.private else ""
        about = info.description or "no description"
        text = f"Added {info.name}: {about} (★ {info.stars:,}).{notes}"
        check_message.set(ui.div(text, class_="text-success small mt-2"))
        ui.update_text("repo_name", value="")

    @render.ui
    def repo_check() -> ui.TagChild:
        return check_message.get()

    @reactive.effect
    @reactive.event(input.browse)
    async def _browse() -> None:
        owner = (input.owner() or "").strip().strip("/")
        try:
            found.set(await list_owner_repos(owner, state.github_token) if owner else [])
        except GitHubLookupError as error:
            found.set([])
            ui.notification_show(str(error), type="error")

    @render.ui
    def browse_results() -> ui.TagChild:
        repos = found.get()
        if not repos:
            return None
        return ui.TagList(
            ui.input_checkbox_group("browse_pick", None, {r.name: _repo_label(r) for r in repos}),
            ui.input_action_button("add_picked", "Add selected", class_="btn-sm"),
        )

    @reactive.effect
    @reactive.event(input.add_picked)
    def _add_picked() -> None:
        try:
            for name in input.browse_pick() or []:
                add(name)
        except ConfigError as error:
            ui.notification_show(str(error), type="warning")
