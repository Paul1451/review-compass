"""GitHub REST/GraphQL client: the only module that talks to the GitHub API."""

from __future__ import annotations

import json as jsonlib
import math
import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from review_compass.models import Bucket, Issue
from review_compass.render import MARKER

LABELS: dict[str, tuple[str, str]] = {
    "low": ("review:low", "2da44e"),
    "medium": ("review:medium", "bf8700"),
    "high": ("review:high", "cf222e"),
}

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_CLOSING_RE = re.compile(r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)")
_NEXT_LINK_RE = re.compile(r'<([^>]+)>\s*;\s*rel="next"')
_MAX_PAGES = 10
_MAX_RETRY_AFTER = 60.0


class GitHubError(RuntimeError):
    """A failed GitHub call. Carries the status, never the body or the token."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


class GitHubClient:
    def __init__(
        self,
        token: str,
        repo: str,
        *,
        base_url: str = "https://api.github.com",
        transport: httpx.BaseTransport | None = None,
        bot_login: str = "github-actions[bot]",
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not _REPO_RE.match(repo):
            raise ValueError("repo must look like 'owner/name'")
        self._repo = repo
        base = urlsplit(base_url)
        self._origin = (base.scheme, base.netloc)
        self._bot_login = bot_login
        self._sleep = sleep
        self._client = httpx.Client(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "Accept": "application/vnd.github+json",
                "User-Agent": "review-compass",
            },
            timeout=30.0,
            transport=transport,
        )

    def __repr__(self) -> str:
        return f"GitHubClient(repo={self._repo!r})"

    # -- low level ---------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        ok_statuses: tuple[int, ...] = (),
    ) -> httpx.Response:
        """Send a request; retry once on a short rate limit; raise on non-2xx."""
        for attempt in (0, 1):
            try:
                response = self._client.request(method, path, json=json, params=params)
            except httpx.HTTPError as exc:
                # Never include the exception text: it can carry URLs or headers.
                raise GitHubError(f"network error: {method} {path.split('?')[0]}", 0) from exc
            if response.is_success or response.status_code in ok_statuses:
                return response
            status = response.status_code
            if attempt == 0 and status in (403, 429):
                delay = _retry_after(response)
                if delay is not None and delay <= _MAX_RETRY_AFTER:
                    self._sleep(delay)
                    continue
            break
        raise GitHubError(f"{method} {path.split('?')[0]} failed with HTTP {status}", status)

    def _pages(self, path: str) -> list[Any]:
        """All items of a paginated listing (per_page=100, at most 10 pages)."""
        items: list[Any] = []
        url = path
        params: dict[str, Any] | None = {"per_page": 100}
        for _ in range(_MAX_PAGES):
            response = self._request("GET", url, params=params)
            items.extend(response.json())
            match = _NEXT_LINK_RE.search(response.headers.get("Link", ""))
            if match is None:
                return items
            url = match.group(1)
            parts = urlsplit(url)
            if parts.netloc and (parts.scheme, parts.netloc) != self._origin:
                raise GitHubError("refusing to follow pagination link to another host", 0)
            params = None
        raise GitHubError(f"pagination cap reached: GET {path}", 0)

    def _path(self, suffix: str) -> str:
        return f"/repos/{self._repo}{suffix}"

    # -- operations --------------------------------------------------------

    def linked_issues(self, pr_body: str) -> list[Issue]:
        numbers: list[int] = []
        for raw in _CLOSING_RE.findall(pr_body):
            number = int(raw)
            if number not in numbers:
                numbers.append(number)
        issues: list[Issue] = []
        for number in numbers[:3]:
            response = self._request("GET", self._path(f"/issues/{number}"), ok_statuses=(404,))
            if response.status_code == 404:
                continue
            data = response.json()
            if "pull_request" in data:
                continue
            issues.append(
                Issue(number=number, title=data.get("title") or "", body=data.get("body") or "")
            )
        return issues

    def upsert_comment(self, pr: int, body: str) -> None:
        mine = [
            c
            for c in self._pages(self._path(f"/issues/{int(pr)}/comments"))
            if str(c.get("body", "")).startswith(MARKER)
            and (c.get("user") or {}).get("login") == self._bot_login
        ]
        if not mine:
            self._request("POST", self._path(f"/issues/{int(pr)}/comments"), json={"body": body})
            return
        self._request(
            "PATCH", self._path(f"/issues/comments/{int(mine[0]['id'])}"), json={"body": body}
        )
        for extra in mine[1:]:
            self._request("DELETE", self._path(f"/issues/comments/{int(extra['id'])}"))

    def set_bucket_label(self, pr: int, bucket: Bucket) -> None:
        name, color = LABELS[bucket]
        labels_path = self._path(f"/issues/{int(pr)}/labels")
        add = {"labels": [name]}
        response = self._request("POST", labels_path, json=add, ok_statuses=(404, 422))
        if not response.is_success:
            # 422 "already exists" is fine
            new = {"name": name, "color": color}
            self._request("POST", self._path("/labels"), json=new, ok_statuses=(422,))
            self._request("POST", labels_path, json=add)
        present = {str(label.get("name")) for label in self._pages(labels_path)}
        for other, _ in LABELS.values():
            if other != name and other in present:
                self._request(
                    "DELETE", self._path(f"/issues/{int(pr)}/labels/{quote(other, safe='')}")
                )

    def head_sha(self, pr: int) -> str:
        data = self._request("GET", self._path(f"/pulls/{int(pr)}")).json()
        return str((data.get("head") or {}).get("sha") or "")

    def approve(self, pr: int, head_sha: str, body: str) -> None:
        self._request(
            "POST",
            self._path(f"/pulls/{int(pr)}/reviews"),
            json={"event": "APPROVE", "commit_id": head_sha, "body": body},
        )

    def dismiss_own_approvals(self, pr: int, message: str) -> None:
        if not message:
            raise ValueError("dismissal message must not be empty")
        first_error: GitHubError | None = None
        for review in self._pages(self._path(f"/pulls/{int(pr)}/reviews")):
            if (
                review.get("state") == "APPROVED"
                and (review.get("user") or {}).get("login") == self._bot_login
            ):
                try:
                    self._request(
                        "PUT",
                        self._path(f"/pulls/{int(pr)}/reviews/{int(review['id'])}/dismissals"),
                        json={"message": message},
                    )
                except GitHubError as error:
                    first_error = first_error or error
        if first_error is not None:
            raise first_error

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        response = self._request("POST", "/graphql", json={"query": query, "variables": variables})
        try:
            payload = response.json()
        except (ValueError, jsonlib.JSONDecodeError):
            payload = None
        if not isinstance(payload, dict):
            raise GitHubError("invalid GraphQL response", response.status_code)
        errors = payload.get("errors")
        if errors:
            first = errors[0] if isinstance(errors[0], dict) else {}
            detail = str(first.get("type") or first.get("message") or "unknown")[:120]
            raise GitHubError(f"POST /graphql returned an error: {detail}", response.status_code)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise GitHubError("invalid GraphQL response", response.status_code)
        return data


def _retry_after(response: httpx.Response) -> float | None:
    try:
        value = float(response.headers["Retry-After"])
    except (KeyError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None
