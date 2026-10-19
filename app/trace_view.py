"""Render traces and usage as HTML.

Model text, tool arguments, and tool output can all quote GitHub content, which anyone can
write. Everything here goes through htmltools escaping or `safe_markdown`, never raw HTML.
"""

from __future__ import annotations

import json
import time
from typing import Any

from shiny import ui

from briefing_view import safe_markdown
from traces import TraceLog, Usage, fold

TAG_LABELS = {"A": "trusted calculation", "B": "needs a citation", "C": "untrusted"}
_WRAP = "white-space: pre-wrap; overflow-wrap: anywhere"
STATUS_CLASSES = {"running": "text-bg-info", "done": "text-bg-success", "failed": "text-bg-danger"}


def usage_text(usage: Usage) -> str:
    return (
        f"{usage.input:,} in · {usage.cache_read:,} cache read · {usage.cache_write:,} cache write"
        f" · {usage.output:,} out · ${usage.cost_usd:.4f}"
    )


def _tool_block(block: dict[str, Any]) -> ui.Tag:
    state = "running" if not block["done"] else ("error" if block["is_error"] else "done")
    badge_class = {"running": "text-bg-info", "error": "text-bg-danger", "done": "text-bg-light"}
    tag = TAG_LABELS.get(block["tag"], "")
    summary = ui.tags.summary(
        ui.tags.code(block["name"]),
        " ",
        ui.span(state, class_=f"badge {badge_class[state]}"),
        " " if tag else "",
        ui.span(f"commons tag {block['tag']}: {tag}", class_="badge text-bg-secondary")
        if tag
        else "",
    )
    args = json.dumps(block["args"], indent=2, ensure_ascii=False)
    return ui.tags.details(
        summary,
        ui.div("Arguments", class_="small text-muted mt-1"),
        ui.tags.pre(args, class_="small mb-1", style=_WRAP),
        ui.div("Output", class_="small text-muted") if block["done"] else "",
        ui.tags.pre(block["output"], class_="small mb-0", style=_WRAP) if block["done"] else "",
        class_="border rounded px-2 py-1 my-1",
    )


def _block(block: dict[str, Any]) -> ui.TagChild:
    kind = block["type"]
    if kind == "turn":
        return ui.div(f"Model call {block['n']}", class_="small fw-semibold text-muted mt-2")
    if kind == "text":
        return ui.div(safe_markdown(block["text"]), class_="border-start border-3 ps-2 my-1")
    if kind == "thinking":
        return ui.tags.details(
            ui.tags.summary("Thinking", class_="small text-muted"),
            ui.div(block["text"], class_="small text-muted", style="white-space: pre-wrap"),
        )
    if kind == "tool":
        return _tool_block(block)
    if kind == "usage":
        return ui.div(usage_text(block["usage"]), class_="small text-muted")
    style = {"error": "text-danger", "retry": "text-warning"}.get(kind, "text-secondary")
    return ui.div(block["message"], class_=f"small {style}")


def trace_panel(trace: TraceLog | None, title: str) -> ui.Tag:
    if trace is None:
        return ui.card(ui.card_header(title), ui.p("Nothing traced yet.", class_="text-muted"))
    status = ui.span(trace.status, class_=f"badge {STATUS_CLASSES[trace.status]}")
    started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(trace.started))
    body = [_block(block) for block in fold(trace.events)]
    if trace.error:
        body.append(ui.div(trace.error, class_="alert alert-danger py-1 small mt-2"))
    return ui.card(
        ui.card_header(title, " ", status),
        ui.div(trace.label, class_="fw-semibold"),
        ui.div(f"{started}{' · ' + trace.model if trace.model else ''}", class_="small text-muted"),
        *body,
        ui.card_footer(ui.span("Total: ", usage_text(trace.usage()), class_="small")),
    )


def usage_table(traces: list[TraceLog]) -> ui.Tag:
    header = ["When", "Agent", "Work", "Model", "Calls", "Input", "Cache read", "Cache write"]
    header += ["Output", "Est. cost", "Status"]
    rows = []
    for trace in traces:
        usage = trace.usage()
        when = time.strftime("%m-%d %H:%M", time.localtime(trace.started))
        cells = [when, trace.agent, trace.label, trace.model or "", f"{usage.calls}"]
        cells += [f"{usage.input:,}", f"{usage.cache_read:,}", f"{usage.cache_write:,}"]
        cells += [f"{usage.output:,}", f"${usage.cost_usd:.4f}", trace.status]
        rows.append(ui.tags.tr(*[ui.tags.td(cell) for cell in cells]))
    return ui.tags.table(
        ui.tags.thead(ui.tags.tr(*[ui.tags.th(name) for name in header])),
        ui.tags.tbody(*rows),
        class_="table table-sm small",
    )


def usage_summary(traces: list[TraceLog]) -> dict[str, Any]:
    """Totals by agent, and the average cost of a briefing that called the model."""
    by_agent: dict[str, Usage] = {}
    briefing_costs = []
    for trace in traces:
        by_agent[trace.agent] = by_agent.get(trace.agent, Usage()) + trace.usage()
        if trace.kind == "briefing" and trace.status == "done" and trace.usage().calls > 0:
            briefing_costs.append(trace.usage())
    average = None
    if briefing_costs:
        total = sum(briefing_costs, Usage())
        average = {
            "tokens": total.tokens // len(briefing_costs),
            "cost": total.cost_usd / len(briefing_costs),
        }
    return {"by_agent": by_agent, "average_briefing": average}
