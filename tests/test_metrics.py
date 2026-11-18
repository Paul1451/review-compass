import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import RepoBuilder

from review_compass.backtest import metrics
from review_compass.backtest.metrics import (
    RepoReport,
    Row,
    auc,
    compute_report,
    run_backtest,
    spearman,
    wilson_interval,
)
from review_compass.backtest.outcomes import Outcome
from review_compass.models import Bucket
from review_compass.policy import load_policy

NOW = datetime(2026, 3, 1, tzinfo=UTC)


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


def test_wilson() -> None:
    assert wilson_interval(0, 0) == (0.0, 0.0)
    lo, hi = wilson_interval(0, 10)
    assert lo == 0.0 and hi == pytest.approx(0.2775, abs=1e-3)
    lo, hi = wilson_interval(5, 10)
    assert (lo, hi) == (pytest.approx(0.2366, abs=1e-3), pytest.approx(0.7634, abs=1e-3))
    assert wilson_interval(10, 10)[1] == 1.0


def test_auc() -> None:
    assert auc([1, 2, 3, 4], [False, False, True, True]) == 1.0
    assert auc([4, 3, 2, 1], [False, False, True, True]) == 0.0
    assert auc([1, 1, 1, 1], [False, True, False, True]) == 0.5
    assert auc([1, 2], [True, True]) is None
    assert auc([1, 2], [False, False]) is None


def test_auc_matches_pairwise_definition() -> None:
    scores = [3, 1, 4, 1, 5, 9, 2, 6, 5, 3]
    labels = [True, False, True, False, False, True, False, True, False, True]
    pos = [s for s, lab in zip(scores, labels, strict=True) if lab]
    neg = [s for s, lab in zip(scores, labels, strict=True) if not lab]
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    assert auc(scores, labels) == pytest.approx(wins / (len(pos) * len(neg)))


def test_spearman() -> None:
    assert spearman([1, 2, 3, 4], [10, 20, 30, 400]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 1, 1], [1, 2, 3]) is None and spearman([1, 2], [1, 2]) is None
    # ties use average ranks: ranks x=[1.5,1.5,3,4], y=[1,2,3,4]
    assert spearman([1, 1, 2, 3], [1, 2, 3, 4]) == pytest.approx(0.9486833, abs=1e-6)


def test_compute_report_counts() -> None:
    rows = [row(10, "low")] * 6 + [row(40, "medium")] * 3 + [row(70, "high", reverted=True)]
    r = compute_report("o/r", rows, 2, load_policy(None), NOW)
    assert [(b.bucket, b.n) for b in r.buckets] == [("low", 6), ("medium", 3), ("high", 1)]
    assert r.low_share == 0.6 and (r.reverted_total, r.reverted_in_low) == (1, 0)
    assert r.auc_reverted == 1.0 and r.skipped_no_head == 2 and r.agent is None
    assert r.policy_fingerprint == load_policy(None).fingerprint()
    high = r.buckets[2]
    assert (high.reverted, high.reverted_rate, high.fix_followup) == (1, 1.0, 1)
    assert r.generated_at == NOW


def test_empty_rows_do_not_crash() -> None:
    r = compute_report("o/r", [], 0, load_policy(None), NOW)
    assert r.n == 0 and r.auc_reverted is None and all(b.n == 0 for b in r.buckets)
    assert r.low_share == 0.0 and r.buckets[0].reverted_ci == (0.0, 0.0)
    assert r.buckets[0].median_hours_to_merge == 0.0


def test_medians_and_signal_fire_rate() -> None:
    rows = [
        row(10, "low", threads=1, hours=1.0, signals={"size": 3}),
        row(12, "low", threads=4, hours=2.0, signals={"size": 2, "agent": 5}),
        row(14, "low", threads=2, hours=9.0),
        row(16, "low", threads=9, hours=5.0),
    ]
    r = compute_report("o/r", rows, 0, load_policy(None), NOW)
    low = r.buckets[0]
    assert low.median_review_threads == 3.0  # mean of 2 and 4
    assert low.median_hours_to_merge == 3.5  # mean of 2.0 and 5.0
    assert r.signal_fire_rate["size"] == 0.5 and r.signal_fire_rate["agent"] == 0.25
    assert r.signal_fire_rate["hotspot"] == 0.0


def test_agent_report_needs_thirty_rows_and_does_not_recurse() -> None:
    few = [row(10, "low", agent=True)] * 29 + [row(10, "low")] * 5
    assert compute_report("o/r", few, 0, load_policy(None), NOW).agent is None
    enough = [row(10, "low", agent=True)] * 30 + [row(70, "high")] * 5
    r = compute_report("o/r", enough, 0, load_policy(None), NOW)
    assert r.agent is not None and r.agent.n == 30 and r.agent.agent is None


# -- end to end ------------------------------------------------------------------------


def _node(n: int, base: str, head: str, **over: Any) -> dict[str, Any]:
    node: dict[str, Any] = {
        "number": n,
        "title": f"Add feature {n}",
        "body": "",
        "author": {"login": "alice", "__typename": "User"},
        "headRefName": f"feat-{n}",
        "baseRefOid": base,
        "headRefOid": head,
        "mergeCommit": None,
        "createdAt": "2026-01-02T00:00:00Z",
        "mergedAt": "2026-01-03T00:00:00Z",
        "reviewThreads": {"totalCount": n},
        "reviews": {"totalCount": 0},
    }
    node.update(over)
    return node


class FakeGH:
    def __init__(self, nodes: list[dict[str, Any]]) -> None:
        self.nodes = nodes
        self.calls = 0

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        info = {"hasNextPage": False, "endCursor": None}
        return {"repository": {"pullRequests": {"nodes": self.nodes, "pageInfo": info}}}


def _origin(make_repo: Callable[[], RepoBuilder]) -> tuple[RepoBuilder, list[dict[str, Any]]]:
    origin = make_repo()
    base = origin.commit({"README.md": "hi\n", "src/a.py": "x = 1\n"})
    nodes = []
    for n in (1, 2, 3):
        origin.checkout("main")
        origin.branch(f"feat-{n}")
        head = origin.commit({f"src/f{n}.py": "y = 2\n"})
        origin._git("update-ref", f"refs/pull/{n}/head", head)
        nodes.append(_node(n, base, head))
    origin.checkout("main")
    return origin, nodes


@pytest.fixture
def no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    import review_compass.brief as brief

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("the backtest must not call an LLM")

    monkeypatch.setattr(brief, "build_brief", boom)
    monkeypatch.setattr(brief, "AnthropicBriefClient", boom)


def test_run_backtest_end_to_end_offline(
    make_repo: Callable[[], RepoBuilder], tmp_path: Path, no_llm: None
) -> None:
    origin, nodes = _origin(make_repo)
    # A fourth PR merged inside the 14-day window is not evaluable yet.
    recent = _node(4, nodes[0]["baseRefOid"], nodes[0]["headRefOid"])
    recent["mergedAt"] = "2026-02-25T00:00:00Z"
    nodes.append(recent)
    out, cache = tmp_path / "out", tmp_path / "cache"
    gh = FakeGH(nodes)
    report = run_backtest(
        "o/r", 10, out, cache, load_policy(None), gh, NOW, clone_url=str(origin.path)
    )
    assert report.n == 3 and report.skipped_no_head == 0
    lines = (out / "o__r" / "rows.jsonl").read_text().splitlines()
    assert len(lines) == 3
    assert {json.loads(line)["number"] for line in lines} == {1, 2, 3}
    parsed = RepoReport.model_validate_json((out / "o__r" / "report.json").read_text())
    assert parsed == report
    assert (cache / "o__r" / "blobs.json").is_file()
    assert (cache / "o__r" / "repo.git" / "HEAD").is_file()


def test_missing_head_and_git_errors_are_skipped(
    make_repo: Callable[[], RepoBuilder], tmp_path: Path
) -> None:
    origin, nodes = _origin(make_repo)
    nodes[0]["headRefOid"] = "0" * 40  # head object that does not exist: GitError
    subprocess.run(["git", "update-ref", "-d", "refs/pull/2/head"], cwd=origin.path, check=True)
    report = run_backtest(
        "o/r", 10, tmp_path / "out", tmp_path / "c", load_policy(None), FakeGH(nodes), NOW,
        clone_url=str(origin.path),
    )  # fmt: skip
    assert report.n == 1 and report.skipped_no_head == 2


def test_resume_skips_scored_prs_and_restarts_on_policy_change(
    make_repo: Callable[[], RepoBuilder],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin, nodes = _origin(make_repo)
    out, cache = tmp_path / "out", tmp_path / "cache"
    policy = load_policy(None)

    def run(p: Any = policy) -> RepoReport:
        gh = FakeGH(nodes)
        return run_backtest("o/r", 10, out, cache, p, gh, NOW, clone_url=str(origin.path))

    first = run()
    scored = out / "o__r" / "scored.jsonl"
    header = json.loads(scored.read_text().splitlines()[0])
    assert header["policy_fingerprint"] == policy.fingerprint() and "tool_version" in header

    calls: list[int] = []
    real = metrics.extract_features

    def spy(git: Any, meta: Any, *args: Any) -> Any:
        calls.append(meta.number)
        return real(git, meta, *args)

    monkeypatch.setattr(metrics, "extract_features", spy)
    assert run() == first.model_copy(update={}) and calls == []

    other = load_policy("thresholds: {medium: 31, high: 61}\n")
    assert run(other).n == 3 and sorted(calls) == [1, 2, 3]
    new_header = json.loads(scored.read_text().splitlines()[0])
    assert new_header["policy_fingerprint"] == other.fingerprint()

    scored.write_text("not json\n")
    calls.clear()
    assert run(other).n == 3 and sorted(calls) == [1, 2, 3]
