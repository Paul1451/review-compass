"""CLI tests: score and pr entry points."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import RepoBuilder

from review_compass import cli
from review_compass.live import LiveResult


def test_cli_score_json(
    make_repo: Callable[[], RepoBuilder], capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo()
    base = repo.commit({"README.md": "a\n"})
    head = repo.commit({"src/a.py": "x = 1\n"})
    argv = ["score", "--base", base, "--head", head, "--repo-dir", str(repo.path), "--json"]
    code = cli.main(argv)
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert {"score", "bucket", "contributions"} <= data.keys()


def test_cli_pr_writes_step_summary(
    make_repo: Callable[[], RepoBuilder],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo()
    base = repo.commit({"README.md": "a\n"})
    head = repo.commit({"src/a.py": "x = 1\n"})
    event = {
        "pull_request": {
            "number": 1,
            "title": "t",
            "body": None,
            "user": {"login": "dev", "type": "User"},
            "head": {"ref": "f", "sha": head, "repo": {"full_name": "o/r"}},
            "base": {"sha": base, "repo": {"full_name": "o/r"}},
            "draft": False,
        }
    }
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(event))
    summary = tmp_path / "summary.md"
    for name in ("GITHUB_TOKEN", "GITHUB_REPOSITORY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    code = cli.main(["pr", "--event", str(event_file), "--repo-dir", str(repo.path)])
    assert code == 0
    assert "Review Compass:" in summary.read_text()
    assert "Review Compass:" in capsys.readouterr().out


def test_unexpected_error_prints_type_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom(*_: object, **__: object) -> LiveResult:
        raise RuntimeError("secret-token-123")

    event_file = tmp_path / "e.json"
    event_file.write_text("{}")
    monkeypatch.setattr(cli, "run_pr", boom)
    monkeypatch.delenv("REVIEW_COMPASS_DEBUG", raising=False)
    code = cli.main(["pr", "--event", str(event_file), "--repo-dir", str(tmp_path)])
    err = capsys.readouterr().err
    assert code == 1
    assert "internal error (RuntimeError)" in err
    assert "secret-token-123" not in err
    assert "Traceback" not in err


class _Recorder:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs


def test_github_client_env_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "GitHubClient", _Recorder)
    assert cli._github_client() is None
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    assert cli._github_client() is None
    monkeypatch.delenv("GITHUB_TOKEN")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    assert cli._github_client() is None
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    client = cli._github_client()
    assert isinstance(client, _Recorder)
    assert client.args == ("t", "o/r") and client.kwargs == {}
    monkeypatch.setenv("REVIEW_COMPASS_BOT_LOGIN", "me[bot]")
    client = cli._github_client()
    assert isinstance(client, _Recorder)
    assert client.kwargs == {"bot_login": "me[bot]"}


def test_brief_client_env_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "AnthropicBriefClient", _Recorder)
    assert cli._brief_client() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert cli._brief_client() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    client = cli._brief_client()
    assert isinstance(client, _Recorder) and client.args == ("k",)


def test_stdout_neutralises_workflow_commands(
    make_repo: Callable[[], RepoBuilder],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    summary_text = "line\n::error::x\nok ::notice::y"
    monkeypatch.setattr(
        cli, "run_pr", lambda *a, **k: LiveResult(exit_code=0, summary=summary_text)
    )
    event_file = tmp_path / "e.json"
    event_file.write_text("{}")
    summary_file = tmp_path / "s.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
    assert cli.main(["pr", "--event", str(event_file), "--repo-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert " ::error::x" in out.splitlines()
    assert "ok ::notice::y" in out.splitlines()
    assert "\n::error::x" in summary_file.read_text()
