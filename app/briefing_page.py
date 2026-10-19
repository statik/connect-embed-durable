"""The Briefing page: today's briefing, past briefings, and a live status line while one runs."""

from __future__ import annotations

from typing import Any

from shiny import Inputs, reactive, render, ui

import briefing_view as view
from app_state import AppState
from briefing_config import BriefingConfig
from trace_view import usage_text
from traces import TraceLog

BRIEFING_PANEL = ui.nav_panel(
    "Briefing",
    ui.layout_columns(
        ui.input_select("history", "Briefing", choices={}, width="100%"),
        ui.input_task_button("write", "Write today's briefing", label_busy="Writing..."),
        col_widths=(9, 3),
        class_="align-items-end",
    ),
    ui.output_ui("live_status"),
    ui.output_ui("briefing"),
)


def status_line(trace: TraceLog) -> str:
    """One line about a running briefing: model calls, tool calls, and spend so far."""
    calls = sum(e.get("kind") == "turn" for e in trace.events)
    fetched = [
        str(e.get("args", {}).get("repo", e.get("name", "")))
        for e in trace.events
        if e.get("kind") == "tool_start"
    ]
    parts = [f"model call {calls}" if calls else "starting"]
    if fetched:
        parts.append(f"fetched {', '.join(fetched)}")
    parts.append(usage_text(trace.usage()))
    return " · ".join(parts)


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


def briefing_server(
    input: Inputs,
    state: AppState,
    saved: reactive.Value[BriefingConfig],
    problem: reactive.Value[str],
    shown: reactive.Value[dict[str, Any] | None],
) -> reactive.Value[int]:
    """Wire the page; returns a counter that goes up whenever a briefing is saved."""
    history_version = reactive.value(0)

    @ui.bind_task_button(button_id="write")
    @reactive.extended_task
    async def write_task(config: BriefingConfig) -> dict[str, Any]:
        return await state.runs.run(config)

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
        choices = {item.key: item.label for item in state.runs.history()}
        current = shown.get()
        selected = current["key"] if current else next(iter(choices), None)
        ui.update_select("history", choices=choices, selected=selected)

    @reactive.effect
    @reactive.event(input.history)
    def _pick() -> None:
        current = shown.get()
        if input.history() and (current is None or current["key"] != input.history()):
            shown.set(state.runs.load(input.history()))

    @render.ui
    def live_status() -> ui.TagChild:
        running = [t for t in state.traces.running() if t.kind == "briefing"]
        if not running:
            return None
        reactive.invalidate_later(0.5)
        return ui.div(
            ui.tags.strong("Writing: "),
            status_line(running[0]),
            ". Watch it on the Trace tab.",
            class_="alert alert-info small py-2",
        )

    @render.ui
    def briefing() -> ui.TagChild:
        if problem.get():
            message = f"The config has a problem: {problem.get()}"
            return ui.div(message, class_="alert alert-danger")
        result = shown.get()
        if result is None:
            if not saved.get().repos:
                message = "No repositories yet. Open Set up to choose some."
                return ui.div(message, class_="alert alert-secondary")
            return None
        return result_view(result)

    return history_version
