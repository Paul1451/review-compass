from datetime import UTC, datetime, timedelta
from typing import Any

from review_compass.backtest.history import HistoricPR
from review_compass.backtest.outcomes import Outcome, evaluable, label_outcomes
from review_compass.gitrepo import GitRepo

T0 = datetime(2024, 3, 1, 16, 30, tzinfo=UTC)
SHA_A = "a" * 40
SHA_H = "b" * 40


def pr(n: int, **over: Any) -> HistoricPR:
    base: dict[str, Any] = {
        "number": n,
        "title": f"PR {n}",
        "body": "",
        "author": "alice",
        "author_is_bot": False,
        "head_ref": f"feat-{n}",
        "base_sha": "0" * 40,
        "head_sha": f"{n:040x}",
        "merge_commit_sha": f"{n + 1000:040x}",
        "created_at": T0 - timedelta(hours=6, minutes=30),
        "merged_at": T0,
        "review_threads": 0,
        "changes_requested": 0,
    }
    base.update(over)
    return HistoricPR(**base)


class _NoGit(GitRepo):
    def log_between_timed(self, rev: str, since: datetime, until: datetime) -> list[Any]:
        return []


def nogit() -> GitRepo:
    return _NoGit(None)  # type: ignore[arg-type]


def later(days: float = 1) -> dict[str, Any]:
    t = T0 + timedelta(days=days)
    return {"created_at": t, "merged_at": t + timedelta(hours=1)}


def test_revert_by_title() -> None:
    out = label_outcomes([pr(12), pr(20, title='Revert "PR 12"', **later())], nogit(), "main")
    assert out[12].reverted and out[12].fix_followup
    assert not out[20].reverted


def test_revert_title_matches_after_strip_and_only_original() -> None:
    p12 = pr(12, title="  X ")
    rev = pr(20, title='Revert "X"', **later())
    revrev = pr(21, title='Revert "Revert "X""', **later(3))
    out = label_outcomes([p12, rev, revrev], nogit(), "main")
    assert out[20].reverted  # the revert PR is reverted by the revert of the revert
    assert out[12].reverted  # directly reverted by 20
    assert not out[21].reverted


def test_revert_of_revert_does_not_mark_original() -> None:
    revrev = pr(21, title='Revert "Revert "X""', **later(2))
    out = label_outcomes([pr(12, title="X"), revrev], nogit(), "main")
    assert not out[12].reverted


def test_revert_by_body_reference() -> None:
    for body in ("Reverts #12", "reverts owner/repo#12", "This REVERTS  org/r.e-po#12."):
        out = label_outcomes([pr(12), pr(20, body=body, **later())], nogit(), "main")
        assert out[12].reverted and out[12].fix_followup, body


def test_body_reference_boundaries() -> None:
    q = pr(20, body="Reverts #123 and reverts #12x", **later())
    out = label_outcomes([pr(12), pr(123), q], nogit(), "main")
    assert not out[12].reverted
    assert out[123].reverted


def test_revert_by_commit_message(make_repo) -> None:
    b = make_repo()
    merged = b.commit({"a": "1"}, message="feat", date="2024-03-01T16:30:00+00:00")
    head = "c" * 40
    b.commit(
        {"a": "2"},
        message=f"Revert x\n\nThis reverts commit {merged[:7].upper()}.",
        date="2024-03-03T10:00:00+00:00",
    )
    b.commit(
        {"a": "3"},
        message=f"Revert y\n\nThis reverts commit {head}.\nThis reverts commit {'d' * 40}.",
        date="2024-03-04T10:00:00+00:00",
    )
    short = pr(1, merge_commit_sha=merged)
    full = pr(2, merge_commit_sha=None, head_sha=head)
    unrelated = pr(3, merge_commit_sha="e" * 40, head_sha="f" * 40)
    out = label_outcomes([short, full, unrelated], b.repo, "HEAD")
    assert out[1].reverted and out[1].fix_followup
    assert out[2].reverted and out[2].fix_followup
    assert not out[3].reverted


def test_commit_revert_window_boundaries(make_repo) -> None:
    b = make_repo()
    sha_in = "1" * 40
    sha_at = "2" * 40
    sha_out = "3" * 40
    b.commit({"a": "1"}, message=f"This reverts commit {sha_at}", date="2024-03-01T16:30:00+00:00")
    b.commit({"a": "2"}, message=f"This reverts commit {sha_in}", date="2024-03-15T16:30:00+00:00")
    b.commit({"a": "3"}, message=f"This reverts commit {sha_out}", date="2024-03-15T16:30:01+00:00")
    prs = [
        pr(1, merge_commit_sha=sha_in),
        pr(2, merge_commit_sha=sha_at),
        pr(3, merge_commit_sha=sha_out),
    ]
    out = label_outcomes(prs, b.repo, "HEAD")
    assert out[1].reverted  # exactly merged_at + 14 days counts
    assert not out[2].reverted  # exactly merged_at does not count
    assert not out[3].reverted


def test_revert_outside_window_ignored() -> None:
    late = pr(20, title='Revert "PR 12"', **later(15))
    early = pr(21, title='Revert "PR 12"', created_at=T0 - timedelta(days=1), merged_at=T0)
    out = label_outcomes([pr(12), late, early], nogit(), "main")
    assert out[12].reverted is False
    assert out[12].fix_followup is False


def test_window_edges_for_prs() -> None:
    end = T0 + timedelta(days=14)
    at_end = pr(20, title='Revert "PR 12"', created_at=end, merged_at=end + timedelta(days=1))
    at_start = pr(21, title='Revert "PR 13"', created_at=T0, merged_at=T0 + timedelta(days=1))
    out = label_outcomes([pr(12), pr(13), at_end, at_start], nogit(), "main")
    assert out[12].reverted
    assert not out[13].reverted


def test_fix_followup_needs_reference_and_keyword() -> None:
    cases = [
        ("Fix crash introduced in #12", "", True),
        ("Fix crash", "", False),
        ("Refactor #12 follow-up", "", False),
        ("Hotfix for startup", "Caused by #12", True),
        ("Regression", "see #12", True),
        ("Refix #12", "", False),
        ("Fix owner/repo#12", "", False),
    ]
    for title, body, expected in cases:
        out = label_outcomes([pr(12), pr(20, title=title, body=body, **later())], nogit(), "main")
        assert out[12].fix_followup is expected, (title, body)
        assert out[12].reverted is False


def test_revert_word_alone_is_not_a_fix() -> None:
    q = pr(20, title="Revert something about #12", **later())
    out = label_outcomes([pr(12), q], nogit(), "main")
    assert out[12].fix_followup is False


def test_number_boundary() -> None:
    q = pr(20, title="Fix #123", **later())
    out = label_outcomes([pr(12), pr(123), q], nogit(), "main")
    assert out[12].fix_followup is False
    assert out[123].fix_followup is True


def test_pr_is_never_its_own_followup() -> None:
    own = pr(12, title='Revert "PR 12"', body="Fixes #12 and reverts #12")
    out = label_outcomes([own], nogit(), "main")
    assert out[12].reverted is False and out[12].fix_followup is False


def test_hours_to_merge_and_review_counts() -> None:
    p = pr(1, review_threads=3, changes_requested=2)
    out = label_outcomes([p], nogit(), "main")
    assert out[1] == Outcome(
        reverted=False,
        fix_followup=False,
        review_threads=3,
        changes_requested=2,
        hours_to_merge=6.5,
    )


def test_oversized_text_is_capped() -> None:
    body = "x" * 25_000 + " Reverts #12"
    out = label_outcomes([pr(12), pr(20, body=body, **later())], nogit(), "main")
    assert out[12].reverted is False


def test_empty_input_makes_no_git_call() -> None:
    assert label_outcomes([], GitRepo(None), "main") == {}  # type: ignore[arg-type]


def test_evaluable_excludes_recent() -> None:
    now = datetime(2024, 6, 15, 12, tzinfo=UTC)
    d13 = pr(1, merged_at=now - timedelta(days=13))
    d14 = pr(2, merged_at=now - timedelta(days=14))
    d20 = pr(3, merged_at=now - timedelta(days=20))
    assert evaluable([d13, d14, d20], now) == [d14, d20]


# --- fix round 1 ---


def test_squash_merge_revert_title_with_own_number() -> None:
    p12 = pr(12, title="Foo")
    out = label_outcomes([p12, pr(20, title='Revert "Foo (#12)"', **later())], nogit(), "main")
    assert out[12].reverted and out[12].fix_followup
    out = label_outcomes([p12, pr(20, title='Revert "Foo (#13)"', **later())], nogit(), "main")
    assert out[12].reverted is False


def test_revert_commit_sha_in_pr_body() -> None:
    p12 = pr(12)
    sha = str(p12.merge_commit_sha)[:10]
    body = f"This reverts commit {sha}."
    out = label_outcomes([p12, pr(20, body=body, **later())], nogit(), "main")
    assert out[12].reverted and out[12].fix_followup
    out = label_outcomes([p12, pr(20, body=body, **later(15))], nogit(), "main")
    assert out[12].reverted is False
    own = pr(12, body=f"This reverts commit {sha}", **later(1))
    assert label_outcomes([own], nogit(), "main")[12].reverted is False


def test_loose_rule_ignores_bots_backports_and_reference_dumps() -> None:
    cases: list[dict[str, Any]] = [
        {"title": "Fix foo #12", "author_is_bot": True},
        {"title": "[1.x] Fix foo (backport)", "body": "see #12"},
        {"title": "Fix cherry-picked bar", "body": "#12"},
        {"title": "Fix foo", "body": "Backport of #12"},
        {"title": "Fix foo", "body": "#12 #1 #2 #3 #4 #5"},
    ]
    for over in cases:
        out = label_outcomes([pr(12), pr(20, **over, **later())], nogit(), "main")
        assert out[12].fix_followup is False, over
    ok = pr(20, title="Fix foo", body="#12 #1", **later())
    assert label_outcomes([pr(12), ok], nogit(), "main")[12].fix_followup is True
    five = pr(20, title="Fix foo", body="#12 #1 #2 #3 #4 #4", **later())
    assert label_outcomes([pr(12), five], nogit(), "main")[12].fix_followup is True


def test_strict_revert_rules_ignore_bot_and_backport_filter() -> None:
    q = pr(20, title='Revert "PR 12"', author_is_bot=True, body="backport of x", **later())
    assert label_outcomes([pr(12), q], nogit(), "main")[12].reverted is True


def test_revert_title_with_explicit_reference_marks_only_referenced() -> None:
    a = pr(12, title="Fix typo")
    b = pr(14, title="Fix typo")
    rev = pr(20, title='Revert "Fix typo"', body="Reverts #14", **later())
    out = label_outcomes([a, b, rev], nogit(), "main")
    assert out[14].reverted and not out[12].reverted
    rev = pr(20, title='Revert "Fix typo"', body="", **later())
    out = label_outcomes([a, b, rev], nogit(), "main")
    assert out[14].reverted and out[12].reverted


def test_revert_commit_sha_must_end_at_word_boundary(make_repo) -> None:
    b = make_repo()
    sha = b.commit({"a": "1"}, message="feat", date="2024-03-01T16:30:00+00:00")
    message = f"This reverts commit {sha[:7]}g"
    b.commit({"a": "2"}, message=message, date="2024-03-03T10:00:00+00:00")
    out = label_outcomes([pr(1, merge_commit_sha=sha)], b.repo, "HEAD")
    assert out[1].reverted is False


class _CountingGit(GitRepo):
    def __init__(self) -> None:
        super().__init__(None)  # type: ignore[arg-type]
        self.calls: list[tuple[str, datetime, datetime]] = []

    def log_between_timed(self, rev: str, since: datetime, until: datetime) -> list[Any]:
        self.calls.append((rev, since, until))
        return []


def test_single_history_call_covers_whole_span() -> None:
    prs = [pr(n, merged_at=T0 + timedelta(days=n), created_at=T0 + timedelta(days=n - 1))
           for n in range(1, 51)]
    git = _CountingGit()
    label_outcomes(prs, git, "main")
    assert len(git.calls) == 1
    _, since, until = git.calls[0]
    assert since <= min(p.merged_at for p in prs)
    assert until >= max(p.merged_at for p in prs) + timedelta(days=14)


def test_commit_revert_far_outside_and_inside_window(make_repo) -> None:
    b = make_repo()
    s_in, s_out = "1" * 40, "2" * 40
    b.commit({"a": "1"}, message=f"This reverts commit {s_in}", date="2024-03-14T16:30:00+00:00")
    b.commit({"a": "2"}, message=f"This reverts commit {s_out}", date="2024-03-21T16:30:00+00:00")
    late = pr(3, merged_at=T0 + timedelta(days=30), created_at=T0 + timedelta(days=29))
    out = label_outcomes([pr(1, merge_commit_sha=s_in), pr(2, merge_commit_sha=s_out), late],
                         b.repo, "HEAD")
    assert out[1].reverted and not out[2].reverted
