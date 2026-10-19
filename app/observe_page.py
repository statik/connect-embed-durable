"""The Trace and Usage pages: watch both agents work, live, and see what it cost."""

from __future__ import annotations

from collections.abc import Callable

from shiny import Inputs, Outputs, reactive, render, ui

from app_state import AppState
from trace_view import trace_panel, usage_summary, usage_table
from traces import AGENTS

LATEST = "latest"
FAST_REFRESH_S = 0.5
SLOW_REFRESH_S = 3.0


def _picker_id(agent: str) -> str:
    return f"trace_pick_{agent.replace('-', '_')}"


def _output_id(agent: str) -> str:
    return f"trace_{agent.replace('-', '_')}"


TRACE_PANEL = ui.nav_panel(
    "Trace",
    ui.p(
        "Each agent's work, as it happens: model calls, streamed text, tool calls with their "
        "arguments and output, and tokens. Pick an earlier run or question from the lists.",
        class_="text-muted",
    ),
    ui.layout_columns(
        *[
            ui.div(
                ui.input_select(_picker_id(agent), None, {LATEST: "Latest"}, width="100%"),
                ui.output_ui(_output_id(agent)),
            )
            for agent in AGENTS
        ]
    ),
)

USAGE_PANEL = ui.nav_panel(
    "Usage",
    ui.output_ui("usage_summary"),
    ui.card(ui.card_header("Every agent run and question"), ui.output_ui("usage_table")),
)


def observe_server(input: Inputs, output: Outputs, state: AppState) -> None:
    def refresh() -> None:
        busy = bool(state.traces.running())
        reactive.invalidate_later(FAST_REFRESH_S if busy else SLOW_REFRESH_S)

    for agent in AGENTS:
        _trace_column(input, output, state, agent, refresh)

    @output(id="usage_summary")
    @render.ui
    def _usage_summary() -> ui.TagChild:
        refresh()
        return _summary(state)

    @output(id="usage_table")
    @render.ui
    def _usage_table() -> ui.TagChild:
        refresh()
        return usage_table(state.traces.recent())


def _trace_column(
    input: Inputs, output: Outputs, state: AppState, agent: str, refresh: Callable[[], None]
) -> None:
    picker = _picker_id(agent)
    known: list[str] = []

    @reactive.effect
    def _choices() -> None:
        refresh()
        traces = state.traces.recent(agent)
        ids = [t.id for t in traces]
        if ids == known:
            return
        known[:] = ids
        choices = {LATEST: "Latest"} | {t.id: f"{t.status} · {t.label}"[:90] for t in traces}
        with reactive.isolate():
            current = input[picker]()
        ui.update_select(
            picker, choices=choices, selected=current if current in choices else LATEST
        )

    @output(id=_output_id(agent))
    @render.ui
    def _panel() -> ui.TagChild:
        refresh()
        traces = state.traces.recent(agent)
        chosen = input[picker]()
        trace = next((t for t in traces if t.id == chosen), traces[0] if traces else None)
        return trace_panel(trace, f"{agent} agent")


def _summary(state: AppState) -> ui.TagChild:
    summary = usage_summary(state.traces.recent())
    boxes = []
    for agent in AGENTS:
        usage = summary["by_agent"].get(agent)
        tokens = f"{usage.tokens:,} tokens" if usage else "0 tokens"
        cost = f"${usage.cost_usd:.4f}" if usage else "$0.0000"
        boxes.append(ui.value_box(f"{agent} total", cost, tokens))
    average = summary["average_briefing"]
    if average:
        boxes.append(
            ui.value_box(
                "Typical briefing", f"${average['cost']:.4f}", f"{average['tokens']:,} tokens"
            )
        )
    price = state.price
    note = (
        f"Costs are estimates from the model catalog's prices for {state.model}: "
        f"${price['input']}/M input, ${price['cacheRead']}/M cache read, "
        f"${price['cacheWrite']}/M cache write, ${price['output']}/M output. "
        "The provider's bill can differ."
        if price
        else f"The model catalog has no price for {state.model}, so costs show as $0."
    )
    return ui.TagList(ui.layout_columns(*boxes), ui.p(note, class_="small text-muted"))
