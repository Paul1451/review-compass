"""CLI tests: score and pr entry points."""

import json
import re
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import RepoBuilder

from review_compass import cli
from review_compass.backtest.metrics import RepoReport
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
    monkeypatch.setenv("GITHUB_API_URL", "https://ghe.example.com/api/v3")
    client = cli._github_client()
    assert isinstance(client, _Recorder)
    assert client.kwargs == {"bot_login": "me[bot]", "base_url": "https://ghe.example.com/api/v3"}


def test_score_no_llm_never_builds_a_client(
    make_repo: Callable[[], RepoBuilder],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom() -> None:
        raise AssertionError("brief client must not be built")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(cli, "_brief_client", boom)
    repo = make_repo()
    base = repo.commit({"README.md": "a\n"})
    head = repo.commit({"src/a.py": "x = 1\n"})
    argv = ["score", "--base", base, "--head", head, "--repo-dir", str(repo.path), "--no-llm"]
    assert cli.main(argv) == 0
    assert "Review Compass" in capsys.readouterr().out


def test_brief_client_env_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "AnthropicBriefClient", _Recorder)
    assert cli._brief_client() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert cli._brief_client() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    client = cli._brief_client()
    assert isinstance(client, _Recorder) and client.args == ("k",)


def _run_pr_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    summary_text: str,
) -> tuple[list[str], Path]:
    result = LiveResult(exit_code=0, summary=summary_text)
    monkeypatch.setattr(cli, "run_pr", lambda *a, **k: result)
    event_file = tmp_path / "e.json"
    event_file.write_text("{}")
    summary_file = tmp_path / "s.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
    assert cli.main(["pr", "--event", str(event_file), "--repo-dir", str(tmp_path)]) == 0
    return capsys.readouterr().out.split("\n"), summary_file


HOSTILE = "line\n::error::x\n   ::add-mask::y\nend"


def test_actions_stdout_wrapped_in_stop_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    lines, summary_file = _run_pr_stdout(tmp_path, monkeypatch, capsys, HOSTILE)
    match = re.fullmatch(r"::stop-commands::([0-9a-f]{32})", lines[0])
    assert match is not None
    token = match.group(1)
    body = [line for line in lines if line]
    assert body[-1] == f"::{token}::"
    assert lines[1:5] == HOSTILE.split("\n")
    assert token not in HOSTILE
    assert summary_file.read_text() == HOSTILE + "\n"


def test_actions_stop_commands_token_differs_per_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    first, _ = _run_pr_stdout(tmp_path, monkeypatch, capsys, HOSTILE)
    second, _ = _run_pr_stdout(tmp_path, monkeypatch, capsys, HOSTILE)
    assert first[0] != second[0]


def test_stdout_verbatim_outside_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lines, _ = _run_pr_stdout(tmp_path, monkeypatch, capsys, HOSTILE)
    assert lines == HOSTILE.split("\n") + [""]
    assert not any(line.startswith("::stop-commands::") for line in lines)


# -- backtest and report ---------------------------------------------------------------


def test_backtest_without_token_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "_github_token", lambda: None)
    assert cli.main(["backtest", "o/r"]) == 2
    assert "No GitHub token: set GITHUB_TOKEN or log in with gh" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["o/r", "--limit", "0"], ["o/r", "--limit", "20001"], ["bad"]])
def test_backtest_rejects_bad_arguments(
    argv: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_github_token", lambda: "t")
    assert cli.main(["backtest", *argv]) == 2


def test_backtest_runs_every_repo_and_writes_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from review_compass.backtest.metrics import compute_report
    from review_compass.policy import load_policy

    seen: list[tuple[str, int]] = []

    def fake_run(repo: str, limit: int, out_dir: Path, *args: object, **kw: object):  # type: ignore[no-untyped-def]
        seen.append((repo, limit))
        return compute_report(repo, [], 0, load_policy(None), args[-1])  # type: ignore[arg-type]

    monkeypatch.setattr(cli, "_github_token", lambda: "t")
    monkeypatch.setattr(cli, "run_backtest", fake_run)
    out = tmp_path / "out"
    assert cli.main(["backtest", "a/b", "c/d", "--limit", "5", "--out", str(out)]) == 0
    assert seen == [("a/b", 5), ("c/d", 5)]
    text = (out / "REPORT.md").read_text()
    assert "## a/b" in text and "## c/d" in text


def test_backtest_unexpected_error_prints_type_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*args: object, **kw: object) -> None:
        raise RuntimeError("secret detail")

    monkeypatch.setattr(cli, "_github_token", lambda: "t")
    monkeypatch.setattr(cli, "run_backtest", boom)
    assert cli.main(["backtest", "o/r"]) == 1
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "secret detail" not in err


def test_github_token_prefers_env_then_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "from-env")
    assert cli._github_token() == "from-env"
    monkeypatch.delenv("GITHUB_TOKEN")
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="from-gh\n", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert cli._github_token() == "from-gh" and calls == [["gh", "auth", "token"]]

    def failing(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no")

    monkeypatch.setattr(cli.subprocess, "run", failing)
    assert cli._github_token() is None


def test_report_rebuilds_markdown_from_json(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from review_compass.backtest.metrics import compute_report
    from review_compass.policy import load_policy

    rep = compute_report("o/r", [], 0, load_policy(None), datetime(2026, 3, 1, tzinfo=UTC))
    (tmp_path / "o__r").mkdir()
    (tmp_path / "o__r" / "report.json").write_text(rep.model_dump_json())
    assert cli.main(["report", str(tmp_path)]) == 0
    assert "## o/r" in (tmp_path / "REPORT.md").read_text()
    assert cli.main(["report", str(tmp_path / "missing")]) == 2


def _fake_reports(monkeypatch: pytest.MonkeyPatch, failing: set[str]) -> None:
    from review_compass.backtest.metrics import compute_report
    from review_compass.policy import load_policy

    def fake_run(
        repo: str,
        limit: int,
        out_dir: Path,
        cache: Path,
        policy: object,
        gh: object,
        now: datetime,
        **kw: object,
    ) -> RepoReport:
        if repo in failing:
            raise RuntimeError("secret detail")
        report = compute_report(repo, [], 0, load_policy(None), now)
        slug = repo.replace("/", "__")
        (out_dir / slug).mkdir(parents=True, exist_ok=True)
        (out_dir / slug / "report.json").write_text(report.model_dump_json())
        return report

    monkeypatch.setattr(cli, "_github_token", lambda: "t")
    monkeypatch.setattr(cli, "run_backtest", fake_run)


def test_backtest_failure_keeps_other_repos_and_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_reports(monkeypatch, {"a/b"})
    out = tmp_path / "out"
    assert cli.main(["backtest", "a/b", "c/d", "--out", str(out)]) == 1
    err = capsys.readouterr().err
    assert "review-compass: backtest failed for a/b (RuntimeError)" in err
    assert "secret detail" not in err
    text = (out / "REPORT.md").read_text()
    assert "## c/d" in text and "## a/b" not in text


def test_report_is_rewritten_after_each_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_reports(monkeypatch, {"c/d"})
    out = tmp_path / "out"
    assert cli.main(["backtest", "a/b", "c/d", "--out", str(out)]) == 1
    assert "## a/b" in (out / "REPORT.md").read_text()


def test_report_command_skips_invalid_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from review_compass.backtest.metrics import compute_report
    from review_compass.policy import load_policy

    good = compute_report("o/r", [], 0, load_policy(None), datetime(2026, 3, 1, tzinfo=UTC))
    variants = {
        "good": good,
        "fp": good.model_copy(update={"policy_fingerprint": "# injected"}),
        "ver": good.model_copy(update={"tool_version": "1.0\n# x"}),
        "sig": good.model_copy(update={"signal_fire_rate": {"evil|signal": 0.5}}),
        "agent": good.model_copy(update={"agent": good.model_copy(update={"tool_version": "a b"})}),
    }
    for name, rep in variants.items():
        (tmp_path / name).mkdir()
        (tmp_path / name / "report.json").write_text(rep.model_dump_json())
    assert cli.main(["report", str(tmp_path)]) == 0
    err = capsys.readouterr().err
    for name in ("fp", "ver", "sig", "agent"):
        assert f"review-compass: ignoring invalid report {tmp_path / name / 'report.json'}" in err
    text = (tmp_path / "REPORT.md").read_text()
    assert text.count("## o/r") == 1
