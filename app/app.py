"""Shiny app: a daily GitHub briefing from a pi-durable agent, questions answered by both a
commons agent and the pi-durable agent, and a live view of their work and its cost.

    uv run shiny run app/app.py

See app_state.py for the environment variables.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import commons
from shiny import App, Inputs, Outputs, Session, reactive, ui

from app_state import AppState
from ask_page import ASK_PANEL, ask_server
from briefing_config import BriefingConfig, ConfigError
from briefing_page import BRIEFING_PANEL, briefing_server
from observe_page import TRACE_PANEL, USAGE_PANEL, observe_server
from setup_page import setup_panel, setup_server

if TYPE_CHECKING:
    from starlette.requests import Request

STATE = AppState.from_environment()

_PRIVATE_NOTICE = (
    "This app can show private repository data, so it only opens for viewers that Posit Connect "
    "identifies. Share it privately (for example, within your organization) and sign in."
)


def app_ui(request: Request) -> ui.Tag:
    if not STATE.viewer_allowed(dict(request.headers)):
        return ui.page_fixed(ui.div(_PRIVATE_NOTICE, class_="alert alert-warning mt-4"))
    try:
        names = [r.name for r in STATE.load_config()[0].repos]
    except ConfigError:
        names = []
    return ui.page_navbar(
        BRIEFING_PANEL,
        ASK_PANEL,
        TRACE_PANEL,
        USAGE_PANEL,
        setup_panel(names, has_token=STATE.github_token is not None),
        title="GitHub briefing",
        id="page",
        fillable=False,
        theme=commons.ui.theme(),
    )


def server(input: Inputs, output: Outputs, session: Session) -> None:
    # The request behind the session; `Session` does not declare it, its concrete class does.
    connection = getattr(session, "http_conn", None)
    headers = dict(connection.headers) if connection is not None else {}
    if not STATE.viewer_allowed(headers):
        return
    try:
        (initial, _source), problem = STATE.load_config(), ""
    except ConfigError as error:
        initial, problem = BriefingConfig(), str(error)
    saved = reactive.value(initial)
    shown: reactive.Value[dict[str, Any] | None] = reactive.value(None)
    history_version = briefing_server(input, STATE, saved, reactive.value(problem), shown)
    ask_server(input, STATE, shown, history_version)
    observe_server(input, output, STATE)
    setup_server(input, STATE, saved)


app = App(app_ui, server)
