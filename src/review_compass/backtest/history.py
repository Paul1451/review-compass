"""Merged-PR history for the backtest: GraphQL pages, a bare clone and PR head refs.

Caching: every GraphQL page is stored as ``cache_dir/prs/page-<index>.json`` together with
the cursor it was requested with. The cache is a snapshot, not a live view: new PRs merged
after the first run do not appear. Delete ``cache_dir/prs`` to refresh it. There is
deliberately no expiry logic.
"""

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from review_compass.github import GitHubError
from review_compass.gitrepo import GitError, GitRepo
from review_compass.models import PRMeta

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(
      states: MERGED, orderBy: {field: CREATED_AT, direction: DESC}, first: 50, after: $cursor
    ) {
      nodes {
        number title body author { login __typename } headRefName baseRefOid headRefOid
        mergeCommit { oid } createdAt mergedAt
        reviewThreads { totalCount }
        reviews(states: CHANGES_REQUESTED) { totalCount }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""


class GraphQLClient(Protocol):
    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]: ...


class HistoricPR(BaseModel):
    number: int
    title: str
    body: str
    author: str
    author_is_bot: bool
    head_ref: str
    base_sha: str
    head_sha: str
    merge_commit_sha: str | None
    created_at: datetime
    merged_at: datetime
    review_threads: int
    changes_requested: int

    def to_meta(self) -> PRMeta:
        return PRMeta(
            number=self.number,
            title=self.title,
            body=self.body,
            author=self.author,
            author_is_bot=self.author_is_bot,
            head_ref=self.head_ref,
            base_sha=self.base_sha,
            head_sha=self.head_sha,
            draft=False,
            is_fork=False,
        )


def _check_repo(repo: str) -> None:
    if not _REPO_RE.fullmatch(repo):
        raise ValueError(f"invalid repository {repo!r}; expected 'owner/name'")


def _parse_node(raw: dict[str, Any]) -> HistoricPR:
    author = raw.get("author") or {}
    merge_commit = raw.get("mergeCommit") or {}
    return HistoricPR(
        number=raw["number"],
        title=raw["title"],
        body=raw.get("body") or "",
        author=author.get("login") or "ghost",
        author_is_bot=author.get("__typename") == "Bot",
        head_ref=raw["headRefName"],
        base_sha=raw["baseRefOid"],
        head_sha=raw["headRefOid"],
        merge_commit_sha=merge_commit.get("oid"),
        created_at=raw["createdAt"],
        merged_at=raw["mergedAt"],
        review_threads=raw["reviewThreads"]["totalCount"],
        changes_requested=raw["reviews"]["totalCount"],
    )


def _read_cached(path: Path, cursor: str | None) -> dict[str, Any] | None:
    try:
        stored = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(stored, dict) or stored.get("cursor") != cursor:
        return None
    data = stored.get("data")
    return data if isinstance(data, dict) else None


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def fetch_merged_prs(
    gh: GraphQLClient, repo: str, limit: int, cache_dir: Path
) -> list[HistoricPR]:
    """Newest-first merged PRs, at most ``limit``; pages come from the cache when valid."""
    _check_repo(repo)
    owner, name = repo.split("/")
    prs: list[HistoricPR] = []
    cursor: str | None = None
    index = 0
    while len(prs) < limit:
        path = cache_dir / "prs" / f"page-{index}.json"
        conn = _read_cached(path, cursor)
        if conn is None:
            data = gh.graphql(QUERY, {"owner": owner, "name": name, "cursor": cursor})
            try:
                conn = data["repository"]["pullRequests"]
            except (KeyError, TypeError) as exc:
                raise GitHubError("unexpected GraphQL response shape", 0) from exc
            _write_atomic(path, {"cursor": cursor, "data": conn})
        prs.extend(_parse_node(n) for n in conn["nodes"])
        info = conn["pageInfo"]
        if not info["hasNextPage"]:
            break
        cursor = info["endCursor"]
        index += 1
    return prs[:limit]


def ensure_clone(repo: str, cache_dir: Path, *, url: str | None = None) -> GitRepo:
    """Bare clone at ``cache_dir/repo.git``; reused (and updated) when already present."""
    _check_repo(repo)
    target = cache_dir / "repo.git"
    if (target / "HEAD").is_file() and (target / "objects").is_dir():
        git = GitRepo(target)
        git.fetch_default_branch()
        return git
    source = url if url is not None else f"https://github.com/{repo}.git"
    cache_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["git", "clone", "--bare", "--quiet", "--", source, str(target)],
        capture_output=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if proc.returncode != 0:
        msg = proc.stderr.decode(errors="replace").strip()
        raise GitError(f"git clone failed: {msg}")
    return GitRepo(target)


def fetch_pr_heads(git: GitRepo, numbers: Sequence[int], batch: int = 100) -> set[int]:
    """Fetch ``refs/pull/<n>/head`` for each number; return the numbers that stay missing."""
    wanted = [n for n in dict.fromkeys(numbers) if not git.ref_exists(f"refs/pull/{n}/head")]
    missing: set[int] = set()
    for start in range(0, len(wanted), batch):
        chunk = wanted[start : start + batch]
        by_spec = {f"+refs/pull/{n}/head:refs/pull/{n}/head": n for n in chunk}
        missing.update(by_spec[spec] for spec in git.fetch_refs(list(by_spec)))
    return missing
