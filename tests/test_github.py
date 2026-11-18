from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from review_compass.github import GitHubClient, GitHubError
from review_compass.render import MARKER

Handler = Callable[[httpx.Request], httpx.Response]
BOT = "github-actions[bot]"


class Recorder:
    def __init__(self, routes: dict[tuple[str, str], Any]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        route = self.routes.get((request.method, request.url.path), (200, {}))
        if callable(route):
            return route(request)
        status, payload = route
        return httpx.Response(status, json=payload)

    def methods(self, method: str) -> list[tuple[str, Any]]:
        return [(p, b) for m, p, b in self.calls if m == method]


def make(routes: dict[tuple[str, str], Any], **kw: Any) -> tuple[GitHubClient, Recorder]:
    rec = Recorder(routes)
    client = GitHubClient("tok", "o/r", transport=httpx.MockTransport(rec), **kw)
    return client, rec


def test_linked_issues_parses_keywords() -> None:
    client, rec = make(
        {
            ("GET", "/repos/o/r/issues/31"): (200, {"title": "A", "body": "ba"}),
            ("GET", "/repos/o/r/issues/32"): (200, {"title": "P", "pull_request": {}}),
        }
    )
    issues = client.linked_issues("Fixes #31, closes: #32 and see #99\nresolved #31")
    assert [p for p, _ in rec.methods("GET")] == [
        "/repos/o/r/issues/31",
        "/repos/o/r/issues/32",
    ]
    assert [(i.number, i.title, i.body) for i in issues] == [(31, "A", "ba")]


def test_linked_issues_skips_404_and_caps_at_three() -> None:
    client, rec = make({("GET", "/repos/o/r/issues/1"): (404, {})})
    assert [i.number for i in client.linked_issues("fixes #1 fixes #2 fixes #3 fixes #4")] == [2, 3]
    assert len(rec.methods("GET")) == 3


def test_upsert_creates_then_updates() -> None:
    client, rec = make({("GET", "/repos/o/r/issues/5/comments"): (200, [])})
    client.upsert_comment(5, f"{MARKER}\nhi")
    assert len(rec.methods("POST")) == 1 and not rec.methods("PATCH")

    existing = [{"id": 7, "body": f"{MARKER}\nold", "user": {"login": BOT}}]
    client, rec = make({("GET", "/repos/o/r/issues/5/comments"): (200, existing)})
    client.upsert_comment(5, f"{MARKER}\nnew")
    assert [p for p, _ in rec.methods("PATCH")] == ["/repos/o/r/issues/comments/7"]
    assert not rec.methods("POST")


def test_upsert_removes_duplicates() -> None:
    existing = [
        {"id": 7, "body": f"{MARKER}a", "user": {"login": BOT}},
        {"id": 8, "body": f"{MARKER}b", "user": {"login": BOT}},
    ]
    client, rec = make({("GET", "/repos/o/r/issues/5/comments"): (200, existing)})
    client.upsert_comment(5, f"{MARKER}c")
    assert [p for p, _ in rec.methods("PATCH")] == ["/repos/o/r/issues/comments/7"]
    assert [p for p, _ in rec.methods("DELETE")] == ["/repos/o/r/issues/comments/8"]


def test_upsert_ignores_marker_comment_by_someone_else() -> None:
    existing = [{"id": 9, "body": f"{MARKER}spoof", "user": {"login": "mallory"}}]
    client, rec = make({("GET", "/repos/o/r/issues/5/comments"): (200, existing)})
    client.upsert_comment(5, f"{MARKER}real")
    assert len(rec.methods("POST")) == 1
    assert not rec.methods("PATCH") and not rec.methods("DELETE")


def test_pagination_follows_link_header() -> None:
    def page(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("page") == "2":
            mine = {"id": 2, "body": MARKER, "user": {"login": BOT}}
            return httpx.Response(200, json=[mine])
        link = '<https://api.github.com/repos/o/r/issues/5/comments?page=2>; rel="next"'
        first = [{"id": 1, "body": "x", "user": {}}]
        return httpx.Response(200, json=first, headers={"Link": link})

    client, rec = make({("GET", "/repos/o/r/issues/5/comments"): page})
    client.upsert_comment(5, MARKER)
    assert [p for p, _ in rec.methods("PATCH")] == ["/repos/o/r/issues/comments/2"]


def test_set_bucket_label_swaps() -> None:
    client, rec = make({("GET", "/repos/o/r/issues/5/labels"): (200, [{"name": "review:high"}])})
    client.set_bucket_label(5, "low")
    assert rec.methods("POST") == [("/repos/o/r/issues/5/labels", {"labels": ["review:low"]})]
    assert [p for p, _ in rec.methods("DELETE")] == ["/repos/o/r/issues/5/labels/review:high"]


def test_set_bucket_label_creates_missing_label() -> None:
    state = {"n": 0}

    def add(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(404 if state["n"] == 1 else 200, json={})

    client, rec = make(
        {
            ("POST", "/repos/o/r/issues/5/labels"): add,
            ("POST", "/repos/o/r/labels"): (422, {"message": "already_exists"}),
            ("GET", "/repos/o/r/issues/5/labels"): (200, []),
        }
    )
    client.set_bucket_label(5, "medium")
    created = [b for p, b in rec.methods("POST") if p == "/repos/o/r/labels"]
    assert created == [{"name": "review:medium", "color": "bf8700"}]
    assert state["n"] == 2


def test_approve_and_dismiss() -> None:
    reviews = [
        {"id": 1, "state": "APPROVED", "user": {"login": BOT}},
        {"id": 2, "state": "APPROVED", "user": {"login": "alice"}},
        {"id": 3, "state": "COMMENTED", "user": {"login": BOT}},
    ]
    client, rec = make({("GET", "/repos/o/r/pulls/5/reviews"): (200, reviews)})
    client.approve(5, "abc123", "ok")
    [(_, body)] = rec.methods("POST")
    assert body["event"] == "APPROVE" and body["commit_id"] == "abc123"
    client.dismiss_own_approvals(5, "stale")
    assert rec.methods("PUT") == [
        ("/repos/o/r/pulls/5/reviews/1/dismissals", {"message": "stale"})
    ]


def test_403_raises_github_error() -> None:
    client, _ = make(
        {("GET", "/repos/o/r/issues/5/comments"): (403, {"message": "secret tok echo"})}
    )
    with pytest.raises(GitHubError) as info:
        client.upsert_comment(5, MARKER)
    assert info.value.status == 403
    assert "tok" not in str(info.value).replace("token", "")
    assert "echo" not in str(info.value)


def test_short_retry_after_sleeps_and_retries_once() -> None:
    state = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json=[])

    slept: list[float] = []
    client, rec = make({("GET", "/repos/o/r/issues/5/comments"): flaky}, sleep=slept.append)
    client.upsert_comment(5, MARKER)
    assert slept == [7.0] and len(rec.methods("POST")) == 1


def test_long_retry_after_raises_without_sleeping() -> None:
    slept: list[float] = []
    client, _ = make(
        {
            ("GET", "/repos/o/r/issues/5/comments"): lambda r: httpx.Response(
                403, headers={"Retry-After": "600"}
            )
        },
        sleep=slept.append,
    )
    with pytest.raises(GitHubError):
        client.upsert_comment(5, MARKER)
    assert slept == []


def test_graphql_returns_data_and_raises_on_errors() -> None:
    client, rec = make({("POST", "/graphql"): (200, {"data": {"x": 1}})})
    assert client.graphql("query { x }", {"a": 1}) == {"x": 1}
    assert rec.calls[0][2] == {"query": "query { x }", "variables": {"a": 1}}
    client, _ = make({("POST", "/graphql"): (200, {"errors": [{"type": "NOT_FOUND"}]})})
    with pytest.raises(GitHubError, match="NOT_FOUND"):
        client.graphql("q", {})


def test_invalid_repo_and_repr_hides_token() -> None:
    with pytest.raises(ValueError):
        GitHubClient("tok", "no-slash")
    with pytest.raises(ValueError):
        GitHubClient("tok", "a/b/../c")
    assert "tok" not in repr(GitHubClient("tok", "o/r"))


def _paged_reviews(total_pages: int) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        headers = {}
        if page < total_pages:
            nxt = f"https://api.github.com/repos/o/r/pulls/5/reviews?page={page + 1}"
            headers["Link"] = f'<{nxt}>; rel="next"'
        return httpx.Response(200, json=[], headers=headers)

    return handler


def test_pagination_cap_fails_closed() -> None:
    client, _ = make({("GET", "/repos/o/r/pulls/5/reviews"): _paged_reviews(11)})
    with pytest.raises(GitHubError, match="pagination cap reached: GET") as info:
        client.dismiss_own_approvals(5, "stale")
    assert info.value.status == 0


def test_exactly_ten_pages_is_fine() -> None:
    client, rec = make({("GET", "/repos/o/r/pulls/5/reviews"): _paged_reviews(10)})
    client.dismiss_own_approvals(5, "stale")
    assert len(rec.methods("GET")) == 10


def test_pagination_refuses_other_host() -> None:
    hosts: list[str] = []

    def evil(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        link = '<https://evil.example/steal?page=2>; rel="next"'
        return httpx.Response(200, json=[], headers={"Link": link})

    client = GitHubClient("tok", "o/r", transport=httpx.MockTransport(evil))
    with pytest.raises(GitHubError, match="another host") as info:
        client.upsert_comment(5, MARKER)
    assert info.value.status == 0
    assert "evil.example" not in hosts


def test_dismiss_attempts_all_then_raises() -> None:
    reviews = [{"id": i, "state": "APPROVED", "user": {"login": BOT}} for i in (1, 2, 3)]

    def dismiss(request: httpx.Request) -> httpx.Response:
        status = 500 if request.url.path.endswith("/1/dismissals") else 200
        return httpx.Response(status, json={})

    routes: dict[tuple[str, str], Any] = {("GET", "/repos/o/r/pulls/5/reviews"): (200, reviews)}
    for i in (1, 2, 3):
        routes[("PUT", f"/repos/o/r/pulls/5/reviews/{i}/dismissals")] = dismiss
    client, rec = make(routes)
    with pytest.raises(GitHubError) as info:
        client.dismiss_own_approvals(5, "stale")
    assert info.value.status == 500
    assert len(rec.methods("PUT")) == 3


def test_dismiss_empty_message_raises_before_request() -> None:
    client, rec = make({})
    with pytest.raises(ValueError):
        client.dismiss_own_approvals(5, "")
    assert rec.calls == []


@pytest.mark.parametrize("value", ["-5", "abc", "inf", "nan"])
def test_invalid_retry_after_is_not_retried(value: str) -> None:
    slept: list[float] = []
    client, rec = make(
        {
            ("GET", "/repos/o/r/issues/5/comments"): lambda r: httpx.Response(
                429, headers={"Retry-After": value}
            )
        },
        sleep=slept.append,
    )
    with pytest.raises(GitHubError):
        client.upsert_comment(5, MARKER)
    assert slept == [] and len(rec.calls) == 1


def test_second_rate_limit_raises() -> None:
    slept: list[float] = []
    client, rec = make(
        {
            ("GET", "/repos/o/r/issues/5/comments"): lambda r: httpx.Response(
                429, headers={"Retry-After": "1"}
            )
        },
        sleep=slept.append,
    )
    with pytest.raises(GitHubError) as info:
        client.upsert_comment(5, MARKER)
    assert info.value.status == 429
    assert slept == [1.0] and len(rec.calls) == 2


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json=[1, 2]),
        httpx.Response(200, json={"data": None}),
    ],
)
def test_graphql_invalid_response_raises(response: httpx.Response) -> None:
    client, _ = make({("POST", "/graphql"): lambda r: response})
    with pytest.raises(GitHubError, match="invalid GraphQL response") as info:
        client.graphql("q", {})
    assert info.value.status == 200


def test_token_never_leaks() -> None:
    secret = "ghs_SECRETTOKEN123"
    transport = httpx.MockTransport(lambda r: httpx.Response(403, json={"message": secret}))
    client = GitHubClient(secret, "o/r", transport=transport)
    assert secret not in repr(client)
    with pytest.raises(GitHubError) as info:
        client.upsert_comment(5, MARKER)
    assert secret not in str(info.value) and secret not in repr(info.value)


def test_transport_error_becomes_github_error_status_0() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("secret detail", request=request)

    client = GitHubClient("tok", "o/r", transport=httpx.MockTransport(boom))
    with pytest.raises(GitHubError) as info:
        client.upsert_comment(1, MARKER + "x")
    assert info.value.status == 0
    assert "secret detail" not in str(info.value)
    assert str(info.value).startswith("network error: GET /repos/o/r/issues/1/comments")


def test_head_sha() -> None:
    client, rec = make({("GET", "/repos/o/r/pulls/5"): (200, {"head": {"sha": "abc"}})})
    assert client.head_sha(5) == "abc"


def test_custom_base_url_keeps_token_on_that_host() -> None:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        link = '<https://api.github.com/repos/o/r/issues/5/comments?page=2>; rel="next"'
        return httpx.Response(200, json=[], headers={"Link": link})

    client = GitHubClient(
        "tok",
        "o/r",
        base_url="https://ghe.example.com/api/v3",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(GitHubError, match="another host"):
        client.upsert_comment(5, MARKER)
    assert hosts == ["ghe.example.com"]
