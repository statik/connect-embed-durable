from pathlib import Path

from briefing_page import status_line
from trace_view import trace_panel, usage_summary
from traces import TraceStore

USAGE = {"input": 100, "cacheRead": 0, "cacheWrite": 0, "output": 10, "costUsd": 0.02}


def test_tool_output_and_model_text_from_github_cannot_inject_html(tmp_path: Path) -> None:
    trace = TraceStore(tmp_path).start("pi-durable", "briefing", "<b>label</b>")
    trace.add({"kind": "tool_start", "id": "t", "name": "repo_activity", "args": {"repo": "<x>"}})
    trace.add(
        {
            "kind": "tool_end",
            "id": "t",
            "name": "repo_activity",
            "isError": False,
            "output": "<img src=x onerror=alert(1)>",
        }
    )
    trace.add({"kind": "text", "delta": "<script>alert(1)</script>"})
    html = str(trace_panel(trace, "pi-durable agent"))
    assert "<img" not in html
    assert "<script>" not in html
    assert "<b>label</b>" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_summary_averages_only_finished_briefings_that_called_the_model(tmp_path: Path) -> None:
    store = TraceStore(tmp_path)
    for cost in (0.02, 0.04):
        trace = store.start("pi-durable", "briefing", "b")
        trace.add({"kind": "message_end", "usage": USAGE | {"costUsd": cost}})
        trace.finish()
    store.start("pi-durable", "briefing", "from storage").finish()
    question = store.start("commons", "question", "q")
    question.add({"kind": "message_end", "usage": USAGE})
    question.finish()
    summary = usage_summary(store.recent())
    assert summary["average_briefing"]["cost"] == 0.03
    assert summary["by_agent"]["commons"].cost_usd == 0.02


def test_status_line_says_what_the_running_briefing_has_done(tmp_path: Path) -> None:
    trace = TraceStore(tmp_path).start("pi-durable", "briefing", "b")
    trace.add({"kind": "turn"})
    trace.add({"kind": "tool_start", "id": "1", "name": "repo_activity", "args": {"repo": "a/b"}})
    trace.add({"kind": "message_end", "usage": USAGE})
    line = status_line(trace)
    assert line.startswith("model call 1 · fetched a/b · 100 in")
    assert line.endswith("$0.0200")
