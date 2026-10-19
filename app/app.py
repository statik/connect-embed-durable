"""Shiny app: set up a daily GitHub briefing, then read it.

    uv run shiny run app/app.py

Environment:
    OPENROUTER_API_KEY  the key for the agent's default model; see BRIEFING_MODEL
    BRIEFING_MODEL      provider/model, openrouter/anthropic/claude-sonnet-5.5 by default
    GITHUB_TOKEN        optional; raises GitHub's rate limit for the agent and the setup wizard
    BRIEFING_CONFIG     the config file; app/briefing.yaml by default
    BRIEFING_STATE_DIR  the agent's durable storage and saved results; app/state by default
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shiny import App, Inputs, Outputs, Session, reactive, render, ui

import briefing_view as view
from briefing_config import (
    LOOKBACK_CHOICES,
    BriefingConfig,
    ConfigError,
    Repo,
    check_repo_name,
    load_config,
    save_config,
)
from briefing_runs import BriefingRuns
from github_lookup import GitHubLookupError, list_owner_repos, lookup_repo

if TYPE_CHECKING:
    from starlette.requests import Request

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("BRIEFING_CONFIG") or APP_DIR / "briefing.yaml")
STATE_DIR = Path(os.environ.get("BRIEFING_STATE_DIR") or APP_DIR / "state")
RUNS = BriefingRuns(STATE_DIR)
STEPS = ("repos", "focus", "review")


def github_token() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or None


def focus_id(name: str) -> str:
    return "focus_" + re.sub(r"\W", "_", name.lower())


def step_nav(back: str | None, forward: str | None, forward_label: str = "Next") -> ui.Tag:
    buttons = []
    if back:
        buttons.append(ui.input_action_button(back, "Back", class_="btn-outline-secondary"))
    if forward:
        buttons.append(ui.input_action_button(forward, forward_label, class_="btn-primary"))
    return ui.div(*buttons, class_="d-flex gap-2 mt-3")


def repos_step(names: list[str]):
    """Step 1. The list starts with the saved repositories, so the page never shows it empty."""
    return ui.nav_panel(
        "Repositories",
        ui.h4("1. Choose repositories"),
        ui.p("Add public repositories one at a time, or pick them from an organization or user."),
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
        step_nav(None, "to_focus"),
        value="repos",
    )


focus_step = ui.nav_panel(
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
    step_nav("back_to_repos", "to_review"),
    value="focus",
)

review_step = ui.nav_panel(
    "Review",
    ui.h4("3. Review and save"),
    ui.p(f"The config is a small YAML file, saved to {CONFIG_PATH}."),
    ui.output_code("config_yaml"),
    ui.help_text(
        "On Posit Connect Cloud the app's files reset when it restarts. To keep this config, "
        "download it and commit it next to app.py as briefing.yaml."
    ),
    ui.div(
        ui.input_action_button("back_to_focus", "Back", class_="btn-outline-secondary"),
        ui.download_button("download_config", "Download briefing.yaml"),
        ui.input_action_button("save", "Save and write today's briefing", class_="btn-primary"),
        class_="d-flex gap-2 mt-3",
    ),
    value="review",
)

briefing_page = ui.nav_panel(
    "Briefing",
    ui.layout_columns(
        ui.input_select("history", "Briefing", choices={}, width="100%"),
        ui.input_task_button("write", "Write today's briefing", label_busy="Writing..."),
        col_widths=(9, 3),
        class_="align-items-end",
    ),
    ui.output_ui("progress"),
    ui.output_ui("briefing"),
)


def app_ui(request: Request) -> ui.Tag:
    try:
        names = [r.name for r in load_config(CONFIG_PATH).repos]
    except ConfigError:
        names = []
    wizard = ui.navset_hidden(repos_step(names), focus_step, review_step, id="wizard")
    return ui.page_navbar(
        briefing_page,
        ui.nav_panel("Set up", wizard),
        title="GitHub briefing",
        id="page",
        fillable=False,
    )


def server(input: Inputs, output: Outputs, session: Session) -> None:
    try:
        initial, problem = load_config(CONFIG_PATH), ""
    except ConfigError as error:
        initial, problem = BriefingConfig(), str(error)
    saved = reactive.value(initial)
    config_problem = reactive.value(problem)
    draft = reactive.value(initial)
    briefing_server(input, saved, config_problem)
    wizard_server(input, saved, draft)


def briefing_server(
    input: Inputs, saved: reactive.Value[BriefingConfig], problem: reactive.Value[str]
) -> None:
    progress_lines: list[str] = []
    shown: reactive.Value[dict[str, Any] | None] = reactive.value(None)
    history_version = reactive.value(0)

    @ui.bind_task_button(button_id="write")
    @reactive.extended_task
    async def write_task(config: BriefingConfig) -> dict[str, Any]:
        progress_lines.clear()
        return await RUNS.run(config, progress_lines.append)

    @reactive.effect
    def _write_when_saved() -> None:
        """On load and after each save. Cheap when today's briefing exists: storage has it."""
        config = saved.get()
        if config.repos:
            with reactive.isolate():
                write_task(config)

    @reactive.effect
    @reactive.event(input.write)
    def _write() -> None:
        write_task(saved.get())

    @reactive.effect
    def _finished() -> None:
        if write_task.status() == "success":
            shown.set(write_task.result())
            with reactive.isolate():
                history_version.set(history_version.get() + 1)
        elif write_task.status() == "error":
            try:
                write_task.result()
            except Exception as error:
                ui.notification_show(f"The briefing failed: {error}", type="error", duration=None)

    @reactive.effect
    def _history_choices() -> None:
        history_version.get()
        choices = {item.key: item.label for item in RUNS.history()}
        current = shown.get()
        selected = current["key"] if current else next(iter(choices), None)
        ui.update_select("history", choices=choices, selected=selected)

    @reactive.effect
    @reactive.event(input.history)
    def _pick() -> None:
        current = shown.get()
        if input.history() and (current is None or current["key"] != input.history()):
            shown.set(RUNS.load(input.history()))

    @render.ui
    def progress() -> ui.Tag | None:
        if write_task.status() != "running":
            return None
        reactive.invalidate_later(0.5)
        lines = progress_lines[-6:] or ["Starting the agent"]
        return ui.div(*[ui.div(line) for line in lines], class_="alert alert-info small py-2")

    @render.ui
    def briefing() -> ui.TagChild:
        if problem.get():
            return ui.div(
                f"The config file has a problem: {problem.get()}", class_="alert alert-danger"
            )
        result = shown.get()
        if result is None:
            if not saved.get().repos:
                return ui.div(
                    "No repositories yet. Open Set up to choose some.",
                    class_="alert alert-secondary",
                )
            return None
        return result_view(result)


def result_view(result: dict[str, Any]) -> ui.TagChild:
    summed = view.totals(result["activity"])
    boxes = [ui.value_box(label, str(summed[kind])) for kind, label in view.KINDS.items()]
    boxes.append(ui.value_box("Commits", str(summed["commits"])))
    return ui.TagList(
        ui.layout_columns(*boxes, col_widths=2),
        ui.card(
            ui.card_header(f"Briefing for {result['date']}"),
            view.provenance_marker(result["verification"]),
            view.safe_markdown(result["markdown"]),
            ui.card_footer(ui.help_text(view.run_details(result))),
        ),
        ui.card(ui.card_header("Activity by repository"), view.activity_table(result["activity"])),
    )


def wizard_server(
    input: Inputs, saved: reactive.Value[BriefingConfig], draft: reactive.Value[BriefingConfig]
) -> None:
    repos_server(input, draft)

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

    @render.download(filename="briefing.yaml")
    def download_config():
        yield draft.get().to_yaml()

    @reactive.effect
    @reactive.event(input.save)
    def _save() -> None:
        try:
            save_config(draft.get(), CONFIG_PATH)
        except OSError as error:
            ui.notification_show(
                f"Could not save {CONFIG_PATH}: {error}", type="error", duration=None
            )
            return
        saved.set(draft.get())
        ui.update_navset("page", selected="Briefing")
        ui.notification_show("Saved. Writing today's briefing.", type="message")


def repos_server(input: Inputs, draft: reactive.Value[BriefingConfig]) -> None:
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
            info = await lookup_repo(name, github_token())
            add(info.name)
        except (ConfigError, GitHubLookupError) as error:
            check_message.set(ui.div(str(error), class_="text-danger small mt-2"))
            return
        archived = " It is archived, so expect no activity." if info.archived else ""
        about = info.description or "no description"
        text = f"Added {info.name}: {about} (★ {info.stars:,}).{archived}"
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
            found.set(await list_owner_repos(owner, github_token()) if owner else [])
        except GitHubLookupError as error:
            found.set([])
            ui.notification_show(str(error), type="error")

    @render.ui
    def browse_results() -> ui.TagChild:
        repos = found.get()
        if not repos:
            return None
        choices = {
            r.name: ui.span(
                ui.tags.strong(r.name),
                f" · pushed {r.pushed_at} · ★ {r.stars:,}",
                ui.div(r.description, class_="small text-muted"),
            )
            for r in repos
        }
        return ui.TagList(
            ui.input_checkbox_group("browse_pick", None, choices),
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


app = App(app_ui, server)
