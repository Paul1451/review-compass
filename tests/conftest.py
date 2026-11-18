"""Shared fixtures: throwaway git repositories under pytest's tmp_path."""

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from review_compass.backtest.metrics import Row
from review_compass.backtest.outcomes import Outcome
from review_compass.gitrepo import GitRepo
from review_compass.models import Bucket


class RepoBuilder:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.repo = GitRepo(path)
        self._git("init", "-q", "--initial-branch=main")
        self._git("config", "user.name", "Test")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "commit.gpgsign", "false")

    def _git(self, *args: str, env: dict[str, str] | None = None) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=self.path,
            capture_output=True,
            env={**os.environ, **(env or {})},
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.decode(errors="replace"))
        return proc.stdout.decode(errors="replace").strip()

    def commit(
        self,
        files: dict[str, str | bytes | None],
        message: str = "c",
        author: str = "Dev <dev@example.com>",
        date: str | None = None,
    ) -> str:
        for rel, content in files.items():
            target = self.path / rel
            if content is None:
                self._git("rm", "-q", "--", rel)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content)
            self._git("add", "--", rel)
        name, _, rest = author.partition(" <")
        email = rest.rstrip(">")
        env = {
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
        }
        if date is not None:
            env["GIT_AUTHOR_DATE"] = date
            env["GIT_COMMITTER_DATE"] = date
        self._git("commit", "-q", "--allow-empty", "-m", message, env=env)
        return self._git("rev-parse", "HEAD")

    def branch(self, name: str) -> None:
        self._git("checkout", "-q", "-b", name)

    def checkout(self, name: str) -> None:
        self._git("checkout", "-q", name)


@pytest.fixture
def make_repo(tmp_path: Path) -> Callable[[], RepoBuilder]:
    counter = 0

    def factory() -> RepoBuilder:
        nonlocal counter
        counter += 1
        path = tmp_path / f"repo{counter}"
        path.mkdir()
        return RepoBuilder(path)

    return factory


_ISOLATED_ENV = (
    "ANTHROPIC_API_KEY",
    "GITHUB_TOKEN",
    "GITHUB_REPOSITORY",
    "GITHUB_STEP_SUMMARY",
    "GITHUB_EVENT_PATH",
    "GITHUB_ACTIONS",
    "REVIEW_COMPASS_BOT_LOGIN",
    "REVIEW_COMPASS_DEBUG",
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may see credentials or Actions variables from the developer's shell."""
    for name in _ISOLATED_ENV:
        monkeypatch.delenv(name, raising=False)


def row(
    score: int,
    bucket: Bucket,
    *,
    reverted: bool = False,
    fix_followup: bool = False,
    agent: bool = False,
    signals: dict[str, int] | None = None,
    threads: int = 0,
    hours: float = 1.0,
) -> Row:
    return Row(
        repo="o/r",
        number=1,
        score=score,
        bucket=bucket,
        agent_authored=agent,
        signals=signals or {},
        outcome=Outcome(
            reverted=reverted,
            fix_followup=fix_followup or reverted,
            review_threads=threads,
            changes_requested=0,
            hours_to_merge=hours,
        ),
    )
