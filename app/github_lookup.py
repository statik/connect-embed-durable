"""Look up repositories on GitHub, so the setup wizard can check what the user types.

With GITHUB_TOKEN set, private repositories the token can read are found too.
"""

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
    private: bool
    url: str

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> RepoInfo:
        return cls(
            name=data["full_name"],
            description=data.get("description") or "",
            stars=int(data.get("stargazers_count") or 0),
            pushed_at=(data.get("pushed_at") or "")[:10],
            archived=bool(data.get("archived")),
            private=bool(data.get("private")),
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
            message = (
                "GITHUB_TOKEN cannot read a repository or account by that name."
                if token
                else "GitHub has no public repository or account by that name. "
                "Set GITHUB_TOKEN to use private repositories."
            )
            raise GitHubLookupError(message) from error
        if error.code in (403, 429) and error.headers.get("x-ratelimit-remaining") == "0":
            hint = "" if token else " Set GITHUB_TOKEN to raise the limit."
            raise GitHubLookupError(f"GitHub's rate limit is used up.{hint}") from error
        raise GitHubLookupError(f"GitHub answered {error.code} {error.reason}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise GitHubLookupError(f"Could not reach GitHub: {error}") from error


async def lookup_repo(name: str, token: str | None) -> RepoInfo:
    """One repository the token (or anyone) can read, or `GitHubLookupError` saying why not."""
    data = await asyncio.to_thread(_get, f"/repos/{name}", token)
    return RepoInfo.from_api(data)


async def list_owner_repos(owner: str, token: str | None, limit: int = 30) -> list[RepoInfo]:
    """An owner's unarchived repositories, most recently pushed first.

    With a token, an organization's private repositories the token can read are included. A
    user's private repositories are not listed by GitHub's API; add those by name.
    """
    query = f"sort=pushed&direction=desc&per_page={limit}"
    data = None
    if token:
        try:
            data = await asyncio.to_thread(_get, f"/orgs/{owner}/repos?type=all&{query}", token)
        except GitHubLookupError:
            data = None  # Not an organization; fall back to the user listing below.
    if data is None:
        data = await asyncio.to_thread(_get, f"/users/{owner}/repos?type=owner&{query}", token)
    return [RepoInfo.from_api(item) for item in data if not item.get("archived")]
