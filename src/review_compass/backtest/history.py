"""Merged-PR history for the backtest: GraphQL pages, a bare clone and PR head refs.

Caching: every GraphQL page is stored as ``cache_dir/prs/page-<index>.json`` together with
the cursor it was requested with. The cache is a snapshot, not a live view: new PRs merged
after the first run do not appear. Delete ``cache_dir/prs`` to refresh it. There is
deliberately no expiry logic.
"""

import json
import os
import re
import shutil
import subprocess
import sys
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


_SHAPE_ERRORS = (KeyError, TypeError, ValueError, AttributeError)


def _parse_page(conn: Any) -> tuple[list[HistoricPR], bool, str | None]:
    """Validate and parse one pullRequests connection; raises on any shape problem."""
    nodes = conn["nodes"]
    info = conn["pageInfo"]
    has_next, end_cursor = info["hasNextPage"], info["endCursor"]
    if not isinstance(nodes, list) or not isinstance(has_next, bool):
        raise TypeError("bad page")
    if end_cursor is not None and not isinstance(end_cursor, str):
        raise TypeError("bad cursor")
    return [_parse_node(n) for n in nodes if n is not None], has_next, end_cursor


def _advances(has_next: bool, end_cursor: str | None, cursor: str | None) -> bool:
    return not has_next or (end_cursor is not None and end_cursor != cursor)


def _read_cached(
    path: Path, repo: str, cursor: str | None
) -> tuple[list[HistoricPR], bool, str | None] | None:
    try:
        stored = json.loads(path.read_text())
        if stored.get("repo") != repo or stored.get("cursor") != cursor:
            return None
        parsed = _parse_page(stored["data"])
        return parsed if _advances(parsed[1], parsed[2], cursor) else None
    except (OSError, *_SHAPE_ERRORS):
        return None


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
    seen: set[int] = set()
    dropped = 0
    cursor: str | None = None
    index = 0
    while len(prs) < limit:
        path = cache_dir / "prs" / f"page-{index}.json"
        parsed = _read_cached(path, repo, cursor)
        if parsed is None:
            data = gh.graphql(QUERY, {"owner": owner, "name": name, "cursor": cursor})
            try:
                conn = data["repository"]["pullRequests"]
                parsed = _parse_page(conn)
            except _SHAPE_ERRORS as exc:
                raise GitHubError("unexpected pull request page shape", 200) from exc
            if not _advances(parsed[1], parsed[2], cursor):
                raise GitHubError("pagination did not advance", 200)
            _write_atomic(path, {"repo": repo, "cursor": cursor, "data": conn})
        page_prs, has_next, end_cursor = parsed
        for pr in page_prs:
            if pr.number in seen:
                dropped += 1
            else:
                seen.add(pr.number)
                prs.append(pr)
        if not has_next:
            break
        cursor = end_cursor
        index += 1
    if dropped:
        print(
            f"review-compass: dropped {dropped} duplicate pull request(s) while paging {repo}",
            file=sys.stderr,
        )
    return prs[:limit]


def ensure_clone(repo: str, cache_dir: Path, *, url: str | None = None) -> GitRepo:
    """Bare clone at ``cache_dir/repo.git``; reused (and updated) when already present."""
    _check_repo(repo)
    source = url if url is not None else f"https://github.com/{repo}.git"
    target = cache_dir / "repo.git"
    if (target / "HEAD").is_file() and (target / "objects").is_dir():
        git = GitRepo(target)
        existing = git.origin_url()
        if existing != source:
            raise GitError(f"cached clone {target} has origin {existing!r}, expected {source!r}")
        git.fetch_default_branch()
        return git
    cache_dir.mkdir(parents=True, exist_ok=True)
    partial = cache_dir / "repo.git.partial"
    shutil.rmtree(partial, ignore_errors=True)
    proc = subprocess.run(
        ["git", "clone", "--bare", "--quiet", "--", source, str(partial)],
        capture_output=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if proc.returncode != 0:
        msg = proc.stderr.decode(errors="replace").strip()
        raise GitError(f"git clone failed: {msg}")
    shutil.rmtree(target, ignore_errors=True)
    os.replace(partial, target)
    return GitRepo(target)


def fetch_pr_heads(git: GitRepo, numbers: Sequence[int], batch: int = 100) -> set[int]:
    """Fetch ``refs/pull/<n>/head`` for each number; return the numbers still without a head.

    A number is reported as "without head" for any fetch failure, including transient network
    errors, not only for refs that do not exist on the remote.
    """
    wanted = [n for n in dict.fromkeys(numbers) if not git.ref_exists(f"refs/pull/{n}/head")]
    missing: set[int] = set()
    for start in range(0, len(wanted), batch):
        chunk = wanted[start : start + batch]
        by_spec = {f"+refs/pull/{n}/head:refs/pull/{n}/head": n for n in chunk}
        missing.update(by_spec[spec] for spec in git.fetch_refs(list(by_spec)))
    return missing
