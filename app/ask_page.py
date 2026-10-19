"""The Ask page: one question, answered by both agents side by side.

The commons agent answers from every saved briefing, through trusted measures or SQL. The
pi-durable agent answers as a follow-up in the shown briefing's own conversation, where it
already has the GitHub data it fetched. Each answer's work shows live on the Trace page.
"""

from __future__ import annotations

import uuid
from typing import Any

import commons
import shinychat
from shiny import Inputs, reactive, render, ui

import briefing_view as view
from app_state import AppState
from ask_agent import build_agent
from trace_view import usage_text
from traces import Usage

_GREETING = (
    "I answer questions about your saved briefings. For example: Who merged the most pull "
    "requests? Which repository had the most new issues in the latest briefing?"
)

ASK_PANEL = ui.nav_panel(
    "Ask",
    ui.p(
        "Ask a question in the box on the left. Both agents answer it: commons from all saved "
        "briefings, pi-durable as a follow-up in the briefing shown on the Briefing page. "
        "The Trace page shows each agent's work as it happens.",
        class_="text-muted",
    ),
    ui.layout_columns(
        ui.card(
            ui.card_header("commons"),
            ui.output_ui("commons_notes"),
            shinychat.chat_ui("ask_commons"),
            full_screen=True,
        ),
        ui.card(ui.card_header("pi-durable"), ui.output_ui("durable_answers"), full_screen=True),
    ),
)

_NO_PRICE = {"input": 0.0, "output": 0.0, "cacheRead": 0.0, "cacheWrite": 0.0}


def ask_server(
    input: Inputs,
    state: AppState,
    shown: reactive.Value[dict[str, Any] | None],
    history_version: reactive.Value[int],
) -> None:
    # The durable side's answers. Tasks fill in their own entry, so a queued question never
    # loses its answer; the panel polls while any is pending.
    entries: list[dict[str, Any]] = []
    asked = reactive.value(0)
    problem = reactive.value("")
    with reactive.isolate():
        data_version = history_version.get()

    @reactive.extended_task
    async def durable_task(entry: dict[str, Any], key: str) -> None:
        try:
            entry["result"] = await state.runs.ask(key, entry["question"], uuid.uuid4().hex)
        except Exception as error:
            entry["error"] = str(error)
        finally:
            entry["pending"] = False

    def ask_durable(question: str) -> None:
        current = shown.get()
        entry: dict[str, Any] = {"question": question, "pending": current is not None}
        entries.append(entry)
        if current is None:
            entry["error"] = "Write or pick a briefing first."
        else:
            entry["date"] = current["date"]
            durable_task(entry, current["key"])
        asked.set(asked.get() + 1)

    try:
        agent = build_agent(
            state.runs.results(),
            model=state.model,
            price=state.price or _NO_PRICE,
            on_question=lambda q: state.traces.start("commons", "question", f"Q: {q}", state.model),
        )
        chat = commons.ui.server("ask_commons", agent, greeting=_GREETING)

        @chat.on_user_submit
        def _also_ask_durable(question: str) -> None:
            ask_durable(question)

    except Exception as error:
        problem.set(f"The commons agent could not start: {error}")

    @render.ui
    def commons_notes() -> ui.TagChild:
        if problem.get():
            return ui.div(problem.get(), class_="alert alert-danger small")
        if history_version.get() != data_version:
            message = "New briefings were saved; reload the page to include them."
            return ui.div(message, class_="small text-warning")
        return None

    @render.ui
    def durable_answers() -> ui.TagChild:
        asked.get()
        if any(entry.get("pending") for entry in entries):
            reactive.invalidate_later(0.5)
        if not entries:
            return ui.p("Answers appear here.", class_="text-muted")
        return ui.TagList(*[_answer(entry, state) for entry in entries])


def _answer(entry: dict[str, Any], state: AppState) -> ui.Tag:
    question = ui.div(ui.tags.strong("Q: "), entry["question"], class_="mt-2")
    if "error" in entry:
        return ui.div(question, ui.div(entry["error"], class_="text-danger small"))
    if entry.get("pending"):
        live = next((t for t in state.traces.running() if t.agent == "pi-durable"), None)
        progress = usage_text(live.usage()) if live else "waiting"
        return ui.div(question, ui.div(f"Answering… {progress}", class_="small text-muted"))
    result = entry["result"]
    return ui.div(
        question,
        ui.div(f"About the briefing for {entry['date']}", class_="small text-muted"),
        view.provenance_marker(result["verification"]),
        view.safe_markdown(result["markdown"]),
        ui.div(usage_text(Usage.from_event(result["usage"])), class_="small text-muted pb-2"),
    )
