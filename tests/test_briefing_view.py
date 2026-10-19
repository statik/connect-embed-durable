from briefing_view import provenance_marker, run_details, safe_markdown, totals


def test_markdown_from_github_titles_cannot_inject_html() -> None:
    hostile = (
        "Merged <script>alert(1)</script> and <img src=x onerror=alert(1)> "
        "[click](javascript:alert(1)) and [ok](https://github.com/a/b/pull/1)"
    )
    html = str(safe_markdown(hostile))
    assert "<script>" not in html
    assert "<img" not in html
    assert 'href="javascript:' not in html
    assert '<a href="https://github.com/a/b/pull/1">ok</a>' in html


def test_markdown_renders_headings_and_lists() -> None:
    html = str(safe_markdown("## a/b\n- one\n- two"))
    assert "<h2>a/b</h2>" in html
    assert html.count("<li>") == 2


def test_provenance_marker_lists_unverified_links() -> None:
    marker = str(
        provenance_marker({"outcome": "unverified", "cited": 3, "unverified": ["https://x/1"]})
    )
    assert "alert-warning" in marker
    assert "https://x/1" in marker
    assert "alert-success" in str(
        provenance_marker({"outcome": "verified", "cited": 3, "unverified": []})
    )


def test_totals_sum_every_repository() -> None:
    activity = {
        "a/b": {"counts": {"pr_merged": 2, "release": 1}, "commits": 5, "truncated": False},
        "c/d": {"counts": {"pr_merged": 1}, "commits": 100, "truncated": True},
    }
    summed = totals(activity)
    assert summed["pr_merged"] == 3
    assert summed["release"] == 1
    assert summed["issue_opened"] == 0
    assert summed["commits"] == 105


def test_run_details_says_where_the_briefing_came_from() -> None:
    result = {
        "resumed": True,
        "window": {"since": "2026-10-18T09:30:00.000Z", "until": "2026-10-19T09:30:00.000Z"},
        "model": "openrouter/anthropic/claude-sonnet-5.5",
        "usage": {"input": 1200, "output": 300, "costUsd": 0.0123},
    }
    assert run_details(result) == (
        "Served from durable storage · 2026-10-18T09:30Z to 2026-10-19T09:30Z · "
        "openrouter/anthropic/claude-sonnet-5.5 · 1,500 tokens · $0.0123"
    )
