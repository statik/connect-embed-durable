"""Turn an agent result into HTML.

Briefings quote pull request and issue titles, which anyone on GitHub can write, so markdown is
rendered with raw HTML switched off. markdown-it also refuses `javascript:` links by default.
"""

from __future__ import annotations

from typing import Any

from markdown_it import MarkdownIt
from shiny import ui

_MARKDOWN = MarkdownIt("commonmark", {"html": False}).enable("table")

KINDS = {
    "release": "Releases",
    "pr_merged": "Merged PRs",
    "pr_opened": "New PRs",
    "issue_opened": "New issues",
    "issue_closed": "Closed issues",
}


def safe_markdown(text: str) -> ui.HTML:
    return ui.HTML(_MARKDOWN.render(text))


def _verified_text(cited: int) -> str:
    if cited == 0:
        return "The answer links no pull requests, issues, or releases."
    items = "item" if cited == 1 else "items"
    return f"All {cited} linked {items} came from GitHub data fetched for this briefing."


def provenance_marker(verification: dict[str, Any]) -> ui.Tag:
    """The commons-style marker: verified only when every item link came from GitHub data."""
    if verification["outcome"] == "verified":
        return ui.div(
            ui.tags.strong("Verified. "),
            _verified_text(verification["cited"]),
            class_="alert alert-success py-2 mb-3",
        )
    links = [ui.tags.li(ui.tags.code(url)) for url in verification["unverified"]]
    return ui.div(
        ui.tags.strong("Unverified. "),
        f"{len(links)} linked {'item' if len(links) == 1 else 'items'} did not come from the "
        "GitHub data, so check before you rely on them:",
        ui.tags.ul(*links, class_="mb-0"),
        class_="alert alert-warning py-2 mb-3",
    )


def totals(activity: dict[str, Any]) -> dict[str, int]:
    """Counts summed across repositories, plus commits."""
    summed = dict.fromkeys(KINDS, 0)
    summed["commits"] = 0
    for repo in activity.values():
        for kind in KINDS:
            summed[kind] += repo["counts"].get(kind, 0)
        summed["commits"] += repo["commits"]
    return summed


def activity_table(activity: dict[str, Any]) -> ui.Tag:
    header = ui.tags.tr(
        ui.tags.th("Repository"),
        *[ui.tags.th(label) for label in KINDS.values()],
        ui.tags.th("Commits"),
    )
    rows = []
    for name, repo in sorted(activity.items()):
        more = "+" if repo["truncated"] else ""
        cells = [ui.tags.td(str(repo["counts"].get(kind, 0))) for kind in KINDS]
        link = ui.tags.a(name, href=f"https://github.com/{name}", target="_blank", rel="noopener")
        rows.append(ui.tags.tr(ui.tags.td(link), *cells, ui.tags.td(f"{repo['commits']}{more}")))
    return ui.tags.table(ui.tags.thead(header), ui.tags.tbody(*rows), class_="table table-sm mb-0")


def run_details(result: dict[str, Any]) -> str:
    usage = result["usage"]
    origin = "Served from durable storage" if result["resumed"] else "New run"
    tokens = sum(usage.get(kind, 0) for kind in ("input", "cacheRead", "cacheWrite", "output"))
    return (
        f"{origin} · {result['window']['since'][:16]}Z to {result['window']['until'][:16]}Z · "
        f"{result['model']} · {tokens:,} tokens · ${usage['costUsd']:.4f}"
    )
