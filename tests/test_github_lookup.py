import io
import os
import urllib.error
import urllib.request
from email.message import Message

import pytest

from github_lookup import GitHubLookupError, list_owner_repos, lookup_repo


def http_error(code: int, headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
    message = Message()
    for name, value in (headers or {}).items():
        message[name] = value
    return urllib.error.HTTPError("https://api.github.com", code, "Error", message, io.BytesIO())


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch):
    """Replace the network: each test sets what GitHub answers."""
    state: dict = {"answer": None, "requests": []}

    def urlopen(request: urllib.request.Request, timeout: float):
        state["requests"].append(request)
        if isinstance(state["answer"], Exception):
            raise state["answer"]
        return io.BytesIO(state["answer"].encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return state


async def test_a_missing_repository_says_so(github: dict) -> None:
    github["answer"] = http_error(404)
    with pytest.raises(GitHubLookupError, match="no public repository"):
        await lookup_repo("acme/nope", None)


async def test_an_exhausted_rate_limit_suggests_a_token(github: dict) -> None:
    github["answer"] = http_error(403, {"x-ratelimit-remaining": "0"})
    with pytest.raises(GitHubLookupError, match="Set GITHUB_TOKEN"):
        await lookup_repo("acme/widgets", None)


async def test_an_unreachable_github_says_so(github: dict) -> None:
    github["answer"] = urllib.error.URLError("no route")
    with pytest.raises(GitHubLookupError, match="Could not reach GitHub"):
        await lookup_repo("acme/widgets", None)


async def test_a_private_repository_is_refused(github: dict) -> None:
    github["answer"] = '{"full_name": "acme/secret", "html_url": "u", "private": true}'
    with pytest.raises(GitHubLookupError, match="public repositories only"):
        await lookup_repo("acme/secret", "token")


async def test_requests_carry_no_personal_identifiers(github: dict) -> None:
    github["answer"] = '{"full_name": "acme/w", "html_url": "u"}'
    await lookup_repo("acme/w", None)
    request = github["requests"][0]
    assert request.get_header("User-agent") == "pi-briefing-setup"
    assert request.get_header("Authorization") is None


async def test_browsing_skips_archived_repositories(github: dict) -> None:
    github["answer"] = (
        '[{"full_name": "acme/a", "html_url": "u", "archived": true},'
        ' {"full_name": "acme/b", "html_url": "u", "stargazers_count": 3}]'
    )
    assert [r.name for r in await list_owner_repos("acme", None)] == ["acme/b"]


@pytest.mark.skipif(os.environ.get("BRIEFING_LIVE") != "1", reason="set BRIEFING_LIVE=1")
async def test_live_lookup_of_a_public_repository() -> None:
    info = await lookup_repo("posit-dev/commons", os.environ.get("GITHUB_TOKEN"))
    assert info.name == "posit-dev/commons"
    assert not info.archived
