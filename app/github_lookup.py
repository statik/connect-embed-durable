"""Look up public repositories on GitHub, so the setup wizard can check what the user types."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

API = "https://api.github.com"
# Generic on purpose: no personal identifiers leave the host.
USER_AGENT = "pi-briefing-setup"
TIMEOUT_SECONDS = 15


class GitHubLookupError(Exception):
    """GitHub could not answer in a way the user can act on."""


@dataclass(frozen=True)
class RepoInfo:
    name: str
    description: str
    stars: int
    pushed_at: str
    archived: bool
    url: str

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> RepoInfo:
        return cls(
            name=data["full_name"],
            description=data.get("description") or "",
            stars=int(data.get("stargazers_count") or 0),
            pushed_at=(data.get("pushed_at") or "")[:10],
            archived=bool(data.get("archived")),
            url=data["html_url"],
        )


def _get(path: str, token: str | None) -> Any:
    request = urllib.request.Request(f"{API}{path}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", USER_AGENT)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise GitHubLookupError(
                "GitHub has no public repository or account by that name"
            ) from error
        if error.code in (403, 429) and error.headers.get("x-ratelimit-remaining") == "0":
            hint = "" if token else " Set GITHUB_TOKEN to raise the limit."
            raise GitHubLookupError(f"GitHub's rate limit is used up.{hint}") from error
        raise GitHubLookupError(f"GitHub answered {error.code} {error.reason}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise GitHubLookupError(f"Could not reach GitHub: {error}") from error


async def lookup_repo(name: str, token: str | None) -> RepoInfo:
    """One public repository, or `GitHubLookupError` saying why it cannot be used."""
    data = await asyncio.to_thread(_get, f"/repos/{name}", token)
    if data.get("private"):
        raise GitHubLookupError(f"{name} is private; the briefing covers public repositories only")
    return RepoInfo.from_api(data)


async def list_owner_repos(owner: str, token: str | None, limit: int = 30) -> list[RepoInfo]:
    """An organization's or user's public, unarchived repositories, most recently pushed first."""
    path = f"/users/{owner}/repos?type=owner&sort=pushed&direction=desc&per_page={limit}"
    data = await asyncio.to_thread(_get, path, token)
    return [RepoInfo.from_api(item) for item in data if not item.get("archived")]
