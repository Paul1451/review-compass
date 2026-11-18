import json
import subprocess
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from review_compass.backtest.history import (
    HistoricPR,
    ensure_clone,
    fetch_merged_prs,
    fetch_pr_heads,
)
from review_compass.github import GitHubError
from review_compass.gitrepo import GitError, GitRepo


def node(n: int, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "number": n,
        "title": f"PR {n}",
        "body": "body",
        "author": {"login": "alice", "__typename": "User"},
        "headRefName": f"feat-{n}",
        "baseRefOid": "b" * 40,
        "headRefOid": "h" * 40,
        "mergeCommit": {"oid": "m" * 40},
        "createdAt": "2025-01-02T03:04:05Z",
        "mergedAt": "2025-01-03T03:04:05Z",
        "reviewThreads": {"totalCount": 3},
        "reviews": {"totalCount": 1},
    }
    base.update(over)
    return base


def page(numbers: range, next_cursor: str | None) -> dict[str, Any]:
    return {
        "repository": {
            "pullRequests": {
                "nodes": [node(n) for n in numbers],
                "pageInfo": {"hasNextPage": next_cursor is not None, "endCursor": next_cursor},
            }
        }
    }


class FakeGH:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.calls: list[dict[str, Any]] = []

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(variables)
        return self.pages[len(self.calls) - 1]


def two_pages() -> FakeGH:
    return FakeGH([page(range(100, 50, -1), "c1"), page(range(50, 0, -1), None)])


def test_paginates_until_limit(tmp_path: Path) -> None:
    gh = two_pages()
    prs = fetch_merged_prs(gh, "o/r", 60, tmp_path)
    assert len(gh.calls) == 2 and len(prs) == 60
    assert gh.calls[0] == {"owner": "o", "name": "r", "cursor": None}
    assert gh.calls[1]["cursor"] == "c1"
    assert [p.number for p in prs] == list(range(100, 40, -1))


def test_second_run_uses_cache(tmp_path: Path) -> None:
    fetch_merged_prs(two_pages(), "o/r", 60, tmp_path)
    gh = FakeGH([])
    prs = fetch_merged_prs(gh, "o/r", 60, tmp_path)
    assert gh.calls == [] and len(prs) == 60


def test_larger_limit_continues_from_cached_pages(tmp_path: Path) -> None:
    first = FakeGH([page(range(100, 50, -1), "c1")])
    fetch_merged_prs(first, "o/r", 50, tmp_path)
    gh = FakeGH([page(range(50, 0, -1), None)])
    prs = fetch_merged_prs(gh, "o/r", 80, tmp_path)
    assert [c["cursor"] for c in gh.calls] == ["c1"] and len(prs) == 80


def test_stale_or_corrupt_cache_is_refetched(tmp_path: Path) -> None:
    fetch_merged_prs(two_pages(), "o/r", 60, tmp_path)
    cache = tmp_path / "prs"
    stored = json.loads((cache / "page-1.json").read_text())
    stored["cursor"] = "other"
    (cache / "page-1.json").write_text(json.dumps(stored))
    gh = FakeGH([page(range(50, 0, -1), None)])
    fetch_merged_prs(gh, "o/r", 60, tmp_path)
    assert len(gh.calls) == 1
    (cache / "page-0.json").write_text("{not json")
    gh = FakeGH([page(range(100, 50, -1), "c1")])
    fetch_merged_prs(gh, "o/r", 60, tmp_path)
    assert len(gh.calls) == 1 and gh.calls[0]["cursor"] is None
    assert not list(cache.glob("*.tmp*"))


def test_invalid_repo_name_rejected(tmp_path: Path) -> None:
    for bad in ("nope", "a/b/c", "a/b; rm", "a b/c"):
        with pytest.raises(ValueError):
            fetch_merged_prs(FakeGH([]), bad, 10, tmp_path)
        with pytest.raises(ValueError):
            ensure_clone(bad, tmp_path)


def test_null_author_and_body(tmp_path: Path) -> None:
    pg = page(range(1, 3), None)
    nodes = pg["repository"]["pullRequests"]["nodes"]
    nodes[0].update(author=None, body=None, mergeCommit=None)
    nodes[1]["author"] = {"login": "dependabot", "__typename": "Bot"}
    a, b = fetch_merged_prs(FakeGH([pg]), "o/r", 10, tmp_path)
    assert (a.author, a.author_is_bot, a.body, a.merge_commit_sha) == ("ghost", False, "", None)
    assert (b.author, b.author_is_bot) == ("dependabot", True)


def test_to_meta_maps_fields(tmp_path: Path) -> None:
    (pr,) = fetch_merged_prs(FakeGH([page(range(5, 6), None)]), "o/r", 10, tmp_path)
    assert pr.created_at.utcoffset() == timedelta(0) and pr.merged_at.utcoffset() == timedelta(0)
    assert (pr.review_threads, pr.changes_requested) == (3, 1)
    meta = pr.to_meta()
    assert (meta.number, meta.title, meta.body, meta.author) == (5, "PR 5", "body", "alice")
    assert (meta.head_ref, meta.base_sha, meta.head_sha) == ("feat-5", "b" * 40, "h" * 40)
    assert meta.draft is False and meta.is_fork is False and meta.author_is_bot is False
    assert isinstance(pr, HistoricPR)


def test_ensure_clone_clones_then_reuses_and_updates(make_repo, tmp_path: Path) -> None:
    origin = make_repo()
    origin.commit({"a.py": "1\n"})
    cache = tmp_path / "cache"
    cache.mkdir()
    git = ensure_clone("o/r", cache, url=str(origin.path))
    assert git.path == cache / "repo.git" and (git.path / "HEAD").is_file()
    new = origin.commit({"a.py": "2\n"})
    again = ensure_clone("o/r", cache, url=str(origin.path))
    assert again.path == git.path and again.ref_exists(new)


def test_fetch_pr_heads_reports_missing(make_repo, tmp_path: Path) -> None:
    origin = make_repo()
    sha = origin.commit({"a.py": "1\n"})
    origin._git("update-ref", "refs/pull/1/head", sha)
    git = ensure_clone("o/r", tmp_path, url=str(origin.path))
    assert fetch_pr_heads(git, [1, 2]) == {2}
    assert git.ref_exists("refs/pull/1/head") and not git.ref_exists("refs/pull/2/head")
    # already-present refs are skipped without contacting origin
    broken = GitRepo(git.path)
    subprocess.run(
        ["git", "remote", "set-url", "origin", str(tmp_path / "gone")], cwd=git.path, check=True
    )
    assert fetch_pr_heads(broken, [1]) == set()


def test_fetch_pr_heads_batches(make_repo, tmp_path: Path) -> None:
    origin = make_repo()
    sha = origin.commit({"a.py": "1\n"})
    for n in (1, 2, 3):
        origin._git("update-ref", f"refs/pull/{n}/head", sha)
    git = ensure_clone("o/r", tmp_path, url=str(origin.path))
    assert fetch_pr_heads(git, [1, 2, 3, 4], batch=2) == {4}


def test_cache_is_bound_to_repository(tmp_path: Path) -> None:
    fetch_merged_prs(FakeGH([page(range(10, 8, -1), None)]), "o/one", 10, tmp_path)
    gh = FakeGH([page(range(20, 18, -1), None)])
    prs = fetch_merged_prs(gh, "o/two", 10, tmp_path)
    assert [c["name"] for c in gh.calls] == ["two"]
    assert [p.number for p in prs] == [20, 19]
    stored = json.loads((tmp_path / "prs" / "page-0.json").read_text())
    assert stored["repo"] == "o/two"
    legacy = {"cursor": None, "data": stored["data"]}
    (tmp_path / "prs" / "page-0.json").write_text(json.dumps(legacy))
    gh = FakeGH([page(range(20, 18, -1), None)])
    fetch_merged_prs(gh, "o/two", 10, tmp_path)
    assert len(gh.calls) == 1


def test_ensure_clone_rejects_foreign_origin(make_repo, tmp_path: Path) -> None:
    a, b = make_repo(), make_repo()
    a.commit({"a.py": "1\n"})
    b.commit({"b.py": "1\n"})
    ensure_clone("o/r", tmp_path, url=str(a.path))
    with pytest.raises(GitError) as err:
        ensure_clone("o/r", tmp_path, url=str(b.path))
    assert str(a.path) in str(err.value) and str(b.path) in str(err.value)


def test_duplicates_across_pages_dropped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gh = FakeGH([page(range(100, 50, -1), "c1"), page(range(51, 1, -1), None)])
    prs = fetch_merged_prs(gh, "o/r", 200, tmp_path)
    numbers = [p.number for p in prs]
    assert numbers == list(range(100, 1, -1)) and len(set(numbers)) == len(numbers)
    err = capsys.readouterr().err
    assert err == "review-compass: dropped 1 duplicate pull request(s) while paging o/r\n"


def test_malformed_fetched_page_raises_and_is_not_cached(tmp_path: Path) -> None:
    pg = page(range(1, 3), None)
    del pg["repository"]["pullRequests"]["nodes"][1]["mergedAt"]
    with pytest.raises(GitHubError) as err:
        fetch_merged_prs(FakeGH([pg]), "o/r", 10, tmp_path)
    assert "unexpected pull request page shape" in str(err.value) and err.value.status == 200
    assert not list((tmp_path / "prs").glob("*"))
    for bad in ({"nodes": []}, {"pageInfo": {"hasNextPage": False, "endCursor": None}}):
        with pytest.raises(GitHubError):
            fetch_merged_prs(FakeGH([{"repository": {"pullRequests": bad}}]), "o/r", 10, tmp_path)


def test_cached_page_failing_validation_is_refetched(tmp_path: Path) -> None:
    (tmp_path / "prs").mkdir()
    bad = {"repo": "o/r", "cursor": None, "data": {}}
    (tmp_path / "prs" / "page-0.json").write_text(json.dumps(bad))
    gh = FakeGH([page(range(3, 1, -1), None)])
    prs = fetch_merged_prs(gh, "o/r", 10, tmp_path)
    assert len(gh.calls) == 1 and len(prs) == 2


def test_null_node_skipped(tmp_path: Path) -> None:
    pg = page(range(1, 4), None)
    pg["repository"]["pullRequests"]["nodes"][1] = None
    prs = fetch_merged_prs(FakeGH([pg]), "o/r", 10, tmp_path)
    assert [p.number for p in prs] == [1, 3]


def test_clone_goes_through_partial_dir(make_repo, tmp_path: Path) -> None:
    origin = make_repo()
    origin.commit({"a.py": "1\n"})
    stale = tmp_path / "repo.git.partial"
    stale.mkdir()
    (stale / "junk").write_text("x")
    git = ensure_clone("o/r", tmp_path, url=str(origin.path))
    assert git.path == tmp_path / "repo.git" and (git.path / "HEAD").is_file()
    assert not stale.exists()
    assert git.ref_exists("HEAD")
