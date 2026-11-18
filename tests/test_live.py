"""End-to-end tests of run_pr against throwaway repos and a fake GitHub client."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import RepoBuilder

from review_compass.github import GitHubError
from review_compass.live import meta_from_event, run_pr
from review_compass.render import MARKER

RepoFactory = Callable[[], RepoBuilder]

LINES = "".join(f"x{i} = {i}\n" for i in range(300))


class FakeGH:
    def __init__(
        self, fail: dict[str, int] | None = None, head: str | Exception | None = None
    ) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fail = fail or {}
        self.head = head
        self.issues_error: Exception | None = None

    def _record(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))
        if name in self.fail:
            raise GitHubError(f"{name} refused", self.fail[name])

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def args(self, name: str) -> list[tuple[Any, ...]]:
        return [a for n, a in self.calls if n == name]

    def linked_issues(self, pr_body: str) -> list[Any]:
        self.calls.append(("linked_issues", (pr_body,)))
        if self.issues_error is not None:
            raise self.issues_error
        return []

    def head_sha(self, pr: int) -> str:
        self.calls.append(("head_sha", (pr,)))
        if isinstance(self.head, Exception):
            raise self.head
        if self.head is None:
            raise AssertionError("FakeGH.head not configured")
        return self.head

    def upsert_comment(self, pr: int, body: str) -> None:
        self._record("upsert_comment", pr, body)

    def set_bucket_label(self, pr: int, bucket: str) -> None:
        self._record("set_bucket_label", pr, bucket)

    def approve(self, pr: int, head_sha: str, body: str) -> None:
        self._record("approve", pr, head_sha, body)

    def dismiss_own_approvals(self, pr: int, message: str) -> None:
        self._record("dismiss_own_approvals", pr, message)


class FakeBriefClient:
    def complete(
        self, *, model: str, system: str, user: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        return {"intent": {"status": "unclear", "note": "n"}, "reading_notes": []}


def event_for(base: str, head: str, **overrides: Any) -> dict[str, Any]:
    pr: dict[str, Any] = {
        "number": 7,
        "title": "Change things",
        "body": "Does a thing",
        "user": {"login": "dev", "type": "User"},
        "head": {"ref": "feature", "sha": head, "repo": {"full_name": "o/r"}},
        "base": {"sha": base, "repo": {"full_name": "o/r"}},
        "draft": False,
    }
    pr.update(overrides)
    return {"pull_request": pr}


def build(
    make_repo: RepoFactory,
    base_files: dict[str, str | bytes | None],
    head_files: dict[str, str | bytes | None],
) -> tuple[Path, str, str]:
    repo = make_repo()
    base = repo.commit(base_files, "base")
    repo.branch("feature")
    head = repo.commit(head_files, "head")
    return repo.path, base, head


def test_happy_path_posts_comment_and_label(make_repo: RepoFactory) -> None:
    path, base, head = build(make_repo, {"README.md": "a\n"}, {"src/app.py": LINES})
    gh = FakeGH()
    out = run_pr(event_for(base, head), path, gh, FakeBriefClient())  # type: ignore[arg-type]
    assert out.exit_code == 0
    assert out.result is not None
    comments = gh.args("upsert_comment")
    assert len(comments) == 1
    assert comments[0][1].startswith(MARKER)
    assert gh.args("set_bucket_label") == [(7, out.result.bucket)]
    assert gh.args("dismiss_own_approvals")
    assert not gh.args("approve")


def test_policy_read_from_base_not_head(make_repo: RepoFactory) -> None:
    policy = "auto_approve: true\nthresholds: {medium: 98, high: 99}\n"
    path, base, head = build(
        make_repo, {"README.md": "a\n"}, {".review-compass.yml": policy, "src/a.py": "x = 1\n"}
    )
    gh = FakeGH()
    out = run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert out.result is not None
    assert out.result.guard_touched
    assert not gh.args("approve")
    assert gh.args("dismiss_own_approvals")
    # default thresholds (35/60), not the head's 98/99
    assert out.result.bucket == ("low" if out.result.score < 35 else "medium")


def test_auto_approve_only_when_low_and_enabled(make_repo: RepoFactory) -> None:
    policy = "auto_approve: true\n"
    base_files: dict[str, str | bytes | None] = {
        ".review-compass.yml": policy,
        "README.md": "a\n",
        "src/auth/x.py": "x = 0\n",
    }
    path, base, head = build(make_repo, base_files, {"README.md": "b\n"})
    gh = FakeGH(head=head)
    out = run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert out.result is not None and out.result.bucket == "low"
    assert gh.args("approve") == [
        (7, head, f"Review Compass: low risk ({out.result.score}/100), auto-approved by policy.")
    ]
    assert not gh.args("dismiss_own_approvals")

    path, base, head = build(make_repo, base_files, {"src/auth/x.py": LINES})
    gh = FakeGH()
    out = run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert out.result is not None and out.result.bucket != "low"
    assert not gh.args("approve")
    assert gh.args("dismiss_own_approvals")


def test_auto_approve_blocked_by_guard_file(make_repo: RepoFactory) -> None:
    path, base, head = build(
        make_repo,
        {".review-compass.yml": "auto_approve: true\n", ".github/workflows/ci.yml": "a: 1\n"},
        {".github/workflows/ci.yml": "a: 2\n"},
    )
    gh = FakeGH()
    out = run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert out.result is not None and out.result.bucket == "low"
    assert out.result.guard_touched
    assert not gh.args("approve")
    assert gh.args("dismiss_own_approvals")


def test_auto_approve_blocked_by_sensitive_file(make_repo: RepoFactory) -> None:
    path, base, head = build(
        make_repo,
        {
            ".review-compass.yml": "auto_approve: true\n",
            "src/auth/session.py": "a = 1\nb = 2\n",
            "tests/test_session.py": "a = 1\nb = 2\n",
        },
        {
            "src/auth/session.py": "a = 3\nb = 4\n",
            "tests/test_session.py": "a = 3\nb = 4\n",
        },
    )
    gh = FakeGH()
    out = run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert out.result is not None and out.result.bucket == "low"
    assert not out.result.guard_touched
    assert not gh.args("approve")
    assert gh.args("dismiss_own_approvals")


def test_draft_is_skipped(make_repo: RepoFactory) -> None:
    path, base, head = build(make_repo, {"README.md": "a\n"}, {"src/a.py": "x = 1\n"})
    gh = FakeGH()
    out = run_pr(event_for(base, head, draft=True), path, gh, None)  # type: ignore[arg-type]
    assert out.exit_code == 0
    assert out.summary == "Skipped: draft pull request"
    assert gh.names() == ["dismiss_own_approvals"]


def test_fork_writes_summary_only(make_repo: RepoFactory) -> None:
    path, base, head = build(make_repo, {"README.md": "a\n"}, {"src/a.py": "x = 1\n"})
    ev = event_for(base, head)
    ev["pull_request"]["head"]["repo"] = {"full_name": "someone/r"}
    gh = FakeGH()
    out = run_pr(ev, path, gh, None)  # type: ignore[arg-type]
    assert out.exit_code == 0
    assert "Fork pull request" in out.summary
    assert set(gh.names()) <= {"linked_issues"}


def test_deleted_fork_counts_as_fork(make_repo: RepoFactory) -> None:
    path, base, head = build(make_repo, {"README.md": "a\n"}, {"src/a.py": "x = 1\n"})
    ev = event_for(base, head)
    ev["pull_request"]["head"]["repo"] = None
    assert meta_from_event(ev).is_fork
    gh = FakeGH()
    run_pr(ev, path, gh, None)  # type: ignore[arg-type]
    assert set(gh.names()) <= {"linked_issues"}


def test_invalid_base_policy_exits_2(make_repo: RepoFactory) -> None:
    bad: dict[str, str | bytes | None] = {".review-compass.yml": "thresholds: nope\n"}
    path, base, head = build(make_repo, bad, {"a.py": "1\n"})
    gh = FakeGH()
    out = run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert out.exit_code == 2
    assert out.summary.startswith("Invalid .review-compass.yml on base branch:")
    assert gh.names() == ["dismiss_own_approvals"]


def test_shallow_clone_exits_2_with_hint(make_repo: RepoFactory) -> None:
    path, _, head = build(make_repo, {"README.md": "a\n"}, {"src/a.py": "x = 1\n"})
    gh = FakeGH()
    out = run_pr(event_for("0" * 40, head), path, gh, None)  # type: ignore[arg-type]
    assert out.exit_code == 2
    assert "fetch-depth: 0" in out.summary
    assert gh.names() == ["dismiss_own_approvals"]


def test_write_refused_still_succeeds(make_repo: RepoFactory) -> None:
    path, base, head = build(make_repo, {"README.md": "a\n"}, {"src/a.py": LINES})
    gh = FakeGH(fail={"upsert_comment": 403})
    out = run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert out.exit_code == 0
    assert "## Review Compass:" in out.summary
    assert "HTTP 403" in out.summary


def test_comment_failure_still_dismisses_when_not_eligible(make_repo: RepoFactory) -> None:
    path, base, head = build(make_repo, {"README.md": "a\n"}, {"src/a.py": LINES})
    gh = FakeGH(fail={"upsert_comment": 403})
    out = run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert out.exit_code == 0
    assert gh.args("dismiss_own_approvals")
    assert "HTTP 403" in out.summary


def test_comment_failure_never_approves(make_repo: RepoFactory) -> None:
    files: dict[str, str | bytes | None] = {
        ".review-compass.yml": "auto_approve: true\n",
        "README.md": "a\n",
    }
    path, base, head = build(make_repo, files, {"README.md": "b\n"})
    gh = FakeGH(fail={"upsert_comment": 403})
    out = run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert out.result is not None and out.result.bucket == "low"
    assert not gh.args("approve")
    assert "HTTP 403" in out.summary


def test_not_a_pull_request_event(tmp_path: Path) -> None:
    out = run_pr({"action": "push"}, tmp_path, None, None)
    assert out.exit_code == 2
    assert out.summary == "Not a pull_request event"


def test_null_body(make_repo: RepoFactory) -> None:
    path, base, head = build(make_repo, {"README.md": "a\n"}, {"src/a.py": "x = 1\n"})
    out = run_pr(event_for(base, head, body=None), path, FakeGH(), None)  # type: ignore[arg-type]
    assert out.exit_code == 0
    assert meta_from_event(event_for(base, head, body=None)).body == ""


APPROVE_POLICY: dict[str, str | bytes | None] = {
    ".review-compass.yml": "auto_approve: true\n",
    "README.md": "a\n",
}


def test_draft_never_auto_approved_when_not_skipped(make_repo: RepoFactory) -> None:
    files: dict[str, str | bytes | None] = {
        ".review-compass.yml": "auto_approve: true\nskip_drafts: false\n",
        "README.md": "a\n",
    }
    path, base, head = build(make_repo, files, {"README.md": "b\n"})
    gh = FakeGH(head=head)
    out = run_pr(event_for(base, head, draft=True), path, gh, None)  # type: ignore[arg-type]
    assert out.result is not None and out.result.bucket == "low"
    assert not gh.args("approve")
    assert gh.args("dismiss_own_approvals")
    assert gh.args("upsert_comment") and gh.args("set_bucket_label")


def test_transport_error_in_comment_still_dismisses(make_repo: RepoFactory) -> None:
    path, base, head = build(make_repo, {"README.md": "a\n"}, {"src/a.py": LINES})
    gh = FakeGH(fail={"upsert_comment": 0})
    out = run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert out.exit_code == 0
    assert gh.args("dismiss_own_approvals")
    assert "HTTP 0" not in out.summary
    assert "upsert_comment refused" in out.summary


def test_linked_issues_failure_continues(make_repo: RepoFactory) -> None:
    path, base, head = build(make_repo, {"README.md": "a\n"}, {"src/a.py": LINES})
    gh = FakeGH()
    gh.issues_error = GitHubError("network error: GET /x", 0)
    out = run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert out.exit_code == 0
    assert gh.args("upsert_comment")


def test_head_changed_skips_approval_and_dismissal(make_repo: RepoFactory) -> None:
    path, base, head = build(make_repo, APPROVE_POLICY, {"README.md": "b\n"})
    gh = FakeGH(head="f" * 40)
    out = run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert out.exit_code == 0
    assert not gh.args("approve")
    assert gh.args("dismiss_own_approvals")
    assert "> Note: pull request head changed during the run; approval skipped." in out.summary


def test_head_unchanged_approves_current_head(make_repo: RepoFactory) -> None:
    path, base, head = build(make_repo, APPROVE_POLICY, {"README.md": "b\n"})
    gh = FakeGH(head=head)
    run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert [a[1] for a in gh.args("approve")] == [head]
    names = gh.names()
    assert names.index("head_sha") < names.index("approve")


def test_head_sha_failure_means_no_approval(make_repo: RepoFactory) -> None:
    path, base, head = build(make_repo, APPROVE_POLICY, {"README.md": "b\n"})
    gh = FakeGH(head=GitHubError("network error: GET /pulls/7", 0))
    out = run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert out.exit_code == 0
    assert not gh.args("approve")
    assert gh.args("dismiss_own_approvals")
    assert "approval skipped" in out.summary


def test_label_failure_blocks_approval_and_dismisses(make_repo: RepoFactory) -> None:
    path, base, head = build(make_repo, {"README.md": "a\n"}, {"src/a.py": LINES})
    gh = FakeGH(fail={"set_bucket_label": 403})
    out = run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert out.exit_code == 0
    assert not gh.args("approve")
    assert gh.args("dismiss_own_approvals")
    assert "HTTP 403" in out.summary


def test_label_failure_blocks_approval_when_eligible(make_repo: RepoFactory) -> None:
    path, base, head = build(make_repo, APPROVE_POLICY, {"README.md": "b\n"})
    gh = FakeGH(fail={"set_bucket_label": 403}, head=head)
    out = run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert not gh.args("approve")
    assert "HTTP 403" in out.summary


def test_approve_failure_warns(make_repo: RepoFactory) -> None:
    path, base, head = build(make_repo, APPROVE_POLICY, {"README.md": "b\n"})
    gh = FakeGH(fail={"approve": 422}, head=head)
    out = run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert out.exit_code == 0
    assert "HTTP 422" in out.summary


def test_dismiss_failure_warns(make_repo: RepoFactory, capsys: pytest.CaptureFixture[str]) -> None:
    path, base, head = build(make_repo, {"README.md": "a\n"}, {"src/a.py": LINES})
    gh = FakeGH(fail={"dismiss_own_approvals": 403})
    out = run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert out.exit_code == 2  # a surviving approval must fail the job
    assert "could not withdraw earlier approvals (HTTP 403)" in out.summary
    assert "dismiss earlier approvals (GitHubError)" in capsys.readouterr().err


def test_malformed_payloads_exit_2(tmp_path: Path) -> None:
    user_str = {"pull_request": {"number": 1, "user": "x", "head": {}, "base": {}}}
    sha_only = {
        "pull_request": {"number": 1, "user": "x", "head": {"sha": "a"}, "base": {"sha": "b"}}
    }
    cases: list[tuple[Any, str]] = [
        ([], "Malformed pull_request event"),
        ({"pull_request": []}, "Malformed pull_request event"),
        ({"pull_request": "x"}, "Malformed pull_request event"),
        ({"pull_request": None}, "Malformed pull_request event"),
        (user_str, "Malformed pull_request event"),
        (sha_only, "Malformed pull_request event"),
        ({"action": "push"}, "Not a pull_request event"),
    ]
    for event, message in cases:
        out = run_pr(event, tmp_path, None, None)
        assert out.exit_code == 2
        assert out.summary == message


# -- F1: fail closed on every exit that does not end in a fresh approval ----------------------


def test_git_error_exit_dismisses_and_hint_only_for_missing_commits(make_repo: RepoFactory) -> None:
    path, _, head = build(make_repo, {"README.md": "a\n"}, {"src/a.py": "x = 1\n"})
    gh = FakeGH()
    out = run_pr(event_for("0" * 40, head), path, gh, None)  # type: ignore[arg-type]
    assert out.exit_code == 2 and "fetch-depth: 0" in out.summary
    assert gh.args("dismiss_own_approvals")
    from review_compass.gitrepo import GitError
    from review_compass.live import _git_error_summary

    assert "fetch-depth" not in _git_error_summary(GitError("git diff failed: Permission denied"))


def test_file_named_like_merge_base_does_not_break_the_run(make_repo: RepoFactory) -> None:
    repo = make_repo()
    base = repo.commit(
        {".review-compass.yml": "auto_approve: true\n", "README.md": "a\n"}, "base"
    )
    repo.branch("feature")
    head = repo.commit({base: "decoy\n", "src/app.py": LINES}, "head")
    gh = FakeGH(head=head)
    out = run_pr(event_for(base, head), repo.path, gh, None)  # type: ignore[arg-type]
    assert out.exit_code == 0 and out.result is not None
    assert gh.args("approve")  # the run completed normally despite the decoy file
    assert not gh.args("dismiss_own_approvals")


def test_unexpected_exception_dismisses_then_reraises(make_repo: RepoFactory) -> None:
    path, base, head = build(make_repo, {"README.md": "a\n"}, {"src/a.py": "x = 1\n"})
    gh = FakeGH()

    class Boom:
        def complete(self, **_: Any) -> dict[str, Any]:
            raise RuntimeError("boom")

    gh.issues_error = RuntimeError("unexpected")
    with pytest.raises(RuntimeError, match="unexpected"):
        run_pr(event_for(base, head), path, gh, Boom())  # type: ignore[arg-type]
    assert gh.names()[-1] == "dismiss_own_approvals"


def test_failing_dismissal_does_not_mask_original_exception(
    make_repo: RepoFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path, base, head = build(make_repo, {"README.md": "a\n"}, {"src/a.py": "x = 1\n"})
    gh = FakeGH(fail={"dismiss_own_approvals": 403})
    gh.issues_error = RuntimeError("unexpected")
    with pytest.raises(RuntimeError, match="unexpected"):
        run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert "dismiss earlier approvals (GitHubError)" in capsys.readouterr().err


def test_invalid_policy_dismissal_failure_is_visible(
    make_repo: RepoFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    bad: dict[str, str | bytes | None] = {".review-compass.yml": "thresholds: nope\n"}
    path, base, head = build(make_repo, bad, {"a.py": "1\n"})
    gh = FakeGH(fail={"dismiss_own_approvals": 500})
    out = run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert out.exit_code == 2
    assert out.summary.startswith("Invalid .review-compass.yml on base branch:")
    assert "could not withdraw earlier approvals (HTTP 500)" in out.summary
    assert "(GitHubError)" in capsys.readouterr().err


def test_draft_dismissal_failure_is_visible(
    make_repo: RepoFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path, base, head = build(make_repo, {"README.md": "a\n"}, {"src/a.py": "x = 1\n"})
    gh = FakeGH(fail={"dismiss_own_approvals": 403})
    out = run_pr(event_for(base, head, draft=True), path, gh, None)  # type: ignore[arg-type]
    assert out.exit_code == 2 and out.summary.startswith("Skipped: draft pull request")
    assert "could not dismiss" in capsys.readouterr().err


def test_eligible_but_label_failure_dismisses(make_repo: RepoFactory) -> None:
    path, base, head = build(make_repo, APPROVE_POLICY, {"README.md": "b\n"})
    gh = FakeGH(fail={"set_bucket_label": 403}, head=head)
    run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert not gh.args("approve") and gh.args("dismiss_own_approvals")


def test_eligible_but_comment_failure_dismisses(make_repo: RepoFactory) -> None:
    path, base, head = build(make_repo, APPROVE_POLICY, {"README.md": "b\n"})
    gh = FakeGH(fail={"upsert_comment": 403}, head=head)
    run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert not gh.args("approve") and gh.args("dismiss_own_approvals")


def test_approve_failure_dismisses(make_repo: RepoFactory) -> None:
    path, base, head = build(make_repo, APPROVE_POLICY, {"README.md": "b\n"})
    gh = FakeGH(fail={"approve": 422}, head=head)
    run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert gh.args("dismiss_own_approvals")


def test_happy_path_does_not_dismiss(make_repo: RepoFactory) -> None:
    path, base, head = build(make_repo, APPROVE_POLICY, {"README.md": "b\n"})
    gh = FakeGH(head=head)
    run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert gh.args("approve") and not gh.args("dismiss_own_approvals")


def test_error_summaries_are_escaped(make_repo: RepoFactory) -> None:
    bad: dict[str, str | bytes | None] = {".review-compass.yml": "thresholds: [x](http://evil)\n"}
    path, base, head = build(make_repo, bad, {"a.py": "1\n"})
    out = run_pr(event_for(base, head), path, None, None)
    assert out.exit_code == 2
    assert "[x](http" not in out.summary and "http://evil" not in out.summary


# -- F2 / F16: files the score cannot judge never auto-approve --------------------------------


@pytest.mark.parametrize(
    "files",
    [
        {"package-lock.json": '{"a": 2}\n'},
        {"vendor/lib/api.py": "x = 2\n"},
        {"docs/conf.py": "x = 2\n"},
        {"docs/run.sh": "echo 2\n"},
        {"gradle/wrapper/gradle-wrapper.jar": b"\x00\x01"},
        {".github/actions/x/action.yml": "a: 2\n"},
        {".github/CODEOWNERS": "* @x\n"},
        {"CODEOWNERS": "* @x\n"},
        {"docs/CODEOWNERS": "* @x\n"},
    ],
)
def test_no_auto_approval_for_unjudgeable_files(
    make_repo: RepoFactory, files: dict[str, str | bytes | None]
) -> None:
    base_files: dict[str, str | bytes | None] = {
        ".review-compass.yml": "auto_approve: true\n",
        "package-lock.json": '{"a": 1}\n',
        "vendor/lib/api.py": "x = 1\n",
        "docs/conf.py": "x = 1\n",
        "docs/run.sh": "echo 1\n",
        "gradle/wrapper/gradle-wrapper.jar": b"\x00",
        ".github/actions/x/action.yml": "a: 1\n",
        ".github/CODEOWNERS": "* @y\n",
        "CODEOWNERS": "* @y\n",
        "docs/CODEOWNERS": "* @y\n",
    }
    path, base, head = build(make_repo, base_files, files)
    gh = FakeGH(head=head)
    out = run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert out.result is not None and out.result.bucket == "low"
    assert not gh.args("approve")
    assert gh.args("dismiss_own_approvals")


def test_rename_out_of_github_dir_blocks_auto_approval(make_repo: RepoFactory) -> None:
    body = "name: x\non: push\njobs: {}\n" * 3
    path, base, head = build(
        make_repo,
        {".review-compass.yml": "auto_approve: true\n", ".github/actions/a.yml": body},
        {".github/actions/a.yml": None, "docs/a.md": body},
    )
    gh = FakeGH(head=head)
    run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert not gh.args("approve")


def test_plain_docs_still_auto_approved(make_repo: RepoFactory) -> None:
    path, base, head = build(
        make_repo, {**APPROVE_POLICY, "docs/guide.md": "a\n"}, {"docs/guide.md": "b\n"}
    )
    gh = FakeGH(head=head)
    run_pr(event_for(base, head), path, gh, None)  # type: ignore[arg-type]
    assert gh.args("approve")
