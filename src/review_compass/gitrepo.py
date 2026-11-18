"""Git access: the single place where git subprocess calls live.

PR code is only ever read through git, never executed. Every call uses an
argument list (no shell) and ``--`` before path arguments.
"""

import os
import re
import subprocess
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from review_compass.models import FileChange

Status = Literal["added", "modified", "deleted", "renamed"]
_SIMPLE_STATUS: dict[str, Status] = {"A": "added", "D": "deleted"}
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_NO_MERGE_BASE = (
    "no merge base between {a} and {b}; "
    "if this is GitHub Actions, set fetch-depth: 0 on actions/checkout"
)


class GitError(RuntimeError):
    pass


class GitRepo:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _run(self, *args: str, input_: bytes | None = None) -> bytes:
        proc = subprocess.run(
            ["git", "--literal-pathspecs", "-c", "core.quotepath=false", *args],
            cwd=self.path,
            capture_output=True,
            input=input_,
            env={**os.environ, "LC_ALL": "C"},  # stable messages for error classification
        )
        if proc.returncode != 0:
            msg = proc.stderr.decode(errors="replace").strip()
            raise GitError(f"git {args[0]} failed: {msg}")
        return proc.stdout

    def _text(self, *args: str) -> str:
        return self._run(*args).decode(errors="replace")

    def merge_base(self, a: str, b: str) -> str:
        try:
            out = self._text("merge-base", "--end-of-options", a, b).strip()
        except GitError:
            out = ""
        if not out:
            raise GitError(_NO_MERGE_BASE.format(a=a, b=b))
        return out

    def changed_files(self, base: str, head: str) -> list[FileChange]:
        status_parts = self._text(
            "diff", "-M", "--name-status", "-z", "--end-of-options", base, head, "--"
        ).split("\0")
        statuses: dict[str, tuple[Status, str | None]] = {}
        i = 0
        while i + 1 < len(status_parts):
            code = status_parts[i]
            if code.startswith(("R", "C")):
                old, new = status_parts[i + 1], status_parts[i + 2]
                statuses[new] = ("renamed", old)
                i += 3
            else:
                statuses[status_parts[i + 1]] = (_SIMPLE_STATUS.get(code[0], "modified"), None)
                i += 2

        counts: dict[str, tuple[int, int, bool]] = {}
        num_parts = self._text(
            "diff", "-M", "--numstat", "-z", "--end-of-options", base, head, "--"
        ).split("\0")
        i = 0
        while i < len(num_parts) and num_parts[i]:
            add_s, del_s, path = num_parts[i].split("\t", 2)
            i += 1
            if path == "":  # rename/copy: old and new path follow as separate fields
                path = num_parts[i + 1]
                i += 2
            binary = add_s == "-" and del_s == "-"
            counts[path] = (0, 0, True) if binary else (int(add_s), int(del_s), False)

        hunks = self._hunks(base, head)
        result: list[FileChange] = []
        for path, (status, old_path) in statuses.items():
            adds, dels, binary = counts.get(path, (0, 0, False))
            result.append(
                FileChange(
                    path=path,
                    old_path=old_path,
                    status=status,
                    additions=adds,
                    deletions=dels,
                    binary=binary,
                    hunks=[] if binary else hunks.get(path, []),
                )
            )
        return result

    def _hunks(self, base: str, head: str) -> dict[str, list[tuple[int, int]]]:
        out: dict[str, list[tuple[int, int]]] = {}
        current: list[tuple[int, int]] | None = None
        in_header = False
        diff = self._text(
            "diff",
            "-M",
            "-U0",
            "--no-color",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            "--end-of-options",
            base,
            head,
            "--",
        )
        for line in diff.splitlines():
            if line.startswith("diff --git "):
                in_header, current = True, None
            elif in_header and line.startswith("+++ "):
                # git appends a TAB to the name when the path contains whitespace
                target = line[4:].removesuffix("\t")
                current = out.setdefault(target[2:], []) if target.startswith("b/") else None
            elif line.startswith("@@"):
                in_header = False
                m = _HUNK_RE.match(line)
                if m and current is not None:
                    start = int(m.group(1))
                    length = int(m.group(2)) if m.group(2) is not None else 1
                    if length > 0:
                        current.append((start, start + length - 1))
        return out

    def show(self, rev: str, path: str) -> str | None:
        """File content at rev; None only if rev is a commit lacking the path."""
        try:
            self._run("rev-parse", "--verify", "--quiet", "--end-of-options", f"{rev}^{{commit}}")
        except GitError as error:  # --quiet leaves stderr empty
            raise GitError(f"git rev-parse failed: bad revision {rev}") from error
        if not self._run("ls-tree", "-z", "--end-of-options", rev, path):
            return None
        return self._text("show", "--end-of-options", f"{rev}:{path}", "--")

    def ls_tree(self, rev: str) -> dict[str, str]:
        tree: dict[str, str] = {}
        for entry in self._text("ls-tree", "-r", "-z", "--end-of-options", rev).split("\0"):
            if not entry:
                continue
            meta, path = entry.split("\t", 1)
            _mode, kind, sha = meta.split(" ")
            if kind == "blob":
                tree[path] = sha
        return tree

    def read_blobs(self, shas: Iterable[str]) -> dict[str, bytes]:
        wanted = list(dict.fromkeys(shas))
        if not wanted:
            return {}
        data = self._run("cat-file", "--batch", input_="".join(f"{s}\n" for s in wanted).encode())
        blobs: dict[str, bytes] = {}
        pos = 0
        while pos < len(data):
            eol = data.index(b"\n", pos)
            header = data[pos:eol].decode().split(" ")
            pos = eol + 1
            if len(header) == 3 and header[1] == "blob":
                size = int(header[2])
                blobs[header[0]] = data[pos : pos + size]
                pos += size + 1  # trailing newline after content
            elif len(header) == 3:  # non-blob object: skip content
                pos += int(header[2]) + 1
        return blobs

    def commit_time(self, rev: str) -> datetime:
        stamp = int(self._text("show", "-s", "--format=%ct", "--end-of-options", rev, "--").strip())
        return datetime.fromtimestamp(stamp, tz=UTC)

    def commit_messages(self, base: str, head: str) -> list[str]:
        out = self._text("log", "--format=%B%x00", "--end-of-options", f"{base}..{head}", "--")
        return [m.strip() for m in out.split("\0") if m.strip()]

    def author_emails(self, base: str, head: str) -> set[str]:
        out = self._text("log", "--format=%ae", "--end-of-options", f"{base}..{head}", "--")
        return {line for line in out.splitlines() if line}

    def churn(self, rev: str, paths: Sequence[str], days: int = 90) -> dict[str, int]:
        counts = {p: 0 for p in paths}
        if not paths:
            return counts
        since = int(self.commit_time(rev).timestamp()) - days * 86400
        out = self._text(
            "log", "--format=%x01", "--name-only", f"--since=@{since}",
            "--end-of-options", rev, "--", *paths,
        )  # fmt: skip
        for chunk in out.split("\x01"):
            for p in {line for line in chunk.splitlines() if line}:
                if p in counts:
                    counts[p] += 1
        return counts

    def touched_before(self, rev: str, emails: set[str], paths: Sequence[str]) -> dict[str, bool]:
        touched = {p: False for p in paths}
        if not paths:
            return touched
        wanted = {e.lower() for e in emails}
        out = self._text(
            "log", "--format=%x01%ae", "--name-only", "--end-of-options", rev, "--", *paths
        )
        for chunk in out.split("\x01"):
            lines = [line for line in chunk.splitlines() if line]
            if not lines or lines[0].lower() not in wanted:
                continue
            for p in lines[1:]:
                if p in touched:
                    touched[p] = True
        return touched

    def diff_text(self, base: str, head: str, paths: Sequence[str]) -> str:
        if not paths:
            return ""
        return self._text(
            "diff", "-M", "--no-color", "--end-of-options", base, head, "--", *paths
        )

    def log_between(self, rev: str, since: datetime, until: datetime) -> list[tuple[str, str]]:
        return [(sha, msg) for sha, _, msg in self.log_between_timed(rev, since, until)]

    def log_between_timed(
        self, rev: str, since: datetime, until: datetime
    ) -> list[tuple[str, datetime, str]]:
        """Commits reachable from ``rev`` with committer date in range: (sha, time, message)."""
        out = self._text(
            "log",
            "--format=%H%x01%ct%x01%B%x00",
            f"--since=@{int(since.timestamp())}",
            f"--until=@{int(until.timestamp())}",
            "--end-of-options",
            rev,
            "--",
        )
        commits: list[tuple[str, datetime, str]] = []
        for record in out.split("\0"):
            record = record.lstrip("\n")
            if not record:
                continue
            sha, _, rest = record.partition("\x01")
            stamp, _, message = rest.partition("\x01")
            commits.append((sha, datetime.fromtimestamp(int(stamp), tz=UTC), message.strip()))
        return commits

    def fetch_refs(self, refspecs: Sequence[str]) -> set[str]:
        if not refspecs:
            return set()
        try:
            self._run("fetch", "origin", "--", *refspecs)
            return set()
        except GitError:
            pass
        failed: set[str] = set()
        for spec in refspecs:
            try:
                self._run("fetch", "origin", "--", spec)
            except GitError:
                failed.add(spec)
        return failed

    def ref_exists(self, ref: str) -> bool:
        try:
            self._run("rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}")
        except GitError:
            return False
        return True

    def fetch_default_branch(self) -> None:
        """Bring the checked-out (default) branch of a bare clone up to date with origin."""
        branch = self._text("symbolic-ref", "--short", "HEAD").strip()
        spec = f"+refs/heads/{branch}:refs/heads/{branch}"
        self._run("fetch", "--prune", "origin", "--", spec)

    def origin_url(self) -> str | None:
        try:
            return self._text("config", "--get", "remote.origin.url").strip() or None
        except GitError:
            return None
