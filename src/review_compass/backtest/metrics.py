"""Backtest metrics: replay the deterministic score over merged PRs and compare with outcomes.

Nothing here reads the clock (``now`` is passed in) and nothing calls an LLM.
"""

import json
import math
import sys
from collections.abc import Sequence
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

from pydantic import BaseModel

from review_compass.backtest.history import (
    GraphQLClient,
    HistoricPR,
    _check_repo,
    ensure_clone,
    fetch_merged_prs,
    fetch_pr_heads,
)
from review_compass.backtest.outcomes import Outcome, evaluable, label_outcomes
from review_compass.features import extract_features
from review_compass.gitrepo import GitError, GitRepo
from review_compass.imports import ImportIndex
from review_compass.models import Bucket
from review_compass.policy import Policy, Weights
from review_compass.scoring import score

BUCKETS: tuple[Bucket, ...] = ("low", "medium", "high")
MIN_AGENT_ROWS = 30
PROGRESS_EVERY = 100
INDEX_SAVE_EVERY = 200
SCORING_RULES_VERSION = "1"
"""Bump when classify, features, imports or scoring change what a pull request scores.

It is part of the scored.jsonl resume header: a file with another or no version is discarded.
"""


class Row(BaseModel):
    repo: str
    number: int
    score: int
    bucket: Bucket
    agent_authored: bool
    signals: dict[str, int]
    outcome: Outcome


class BucketStats(BaseModel):
    bucket: Bucket
    n: int
    share: float
    reverted: int
    reverted_rate: float
    reverted_ci: tuple[float, float]
    fix_followup: int
    fix_followup_rate: float
    fix_followup_ci: tuple[float, float]
    median_review_threads: float
    median_hours_to_merge: float
    changes_requested: int = 0  # PRs with at least one changes-requested review
    changes_requested_share: float = 0.0


class RepoReport(BaseModel):
    repo: str
    n: int
    skipped_no_head: int
    buckets: list[BucketStats]  # low, medium, high
    auc_reverted: float | None
    auc_fix_followup: float | None
    spearman_review_threads: float | None
    low_share: float
    reverted_total: int
    reverted_in_low: int
    high_low_revert_ratio: float | None = None  # None when the low rate is 0
    signal_fire_rate: dict[str, float]
    agent: "RepoReport | None"
    policy_fingerprint: str
    tool_version: str
    generated_at: datetime


# -- statistics ------------------------------------------------------------------------


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials, clamped to [0, 1]."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    # The bounds at k == 0 and k == n are exactly 0 and 1; avoid float residue there.
    lo = 0.0 if k == 0 else max(0.0, centre - half)
    hi = 1.0 if k == n else min(1.0, centre + half)
    return (lo, hi)


def _average_ranks(values: Sequence[float]) -> list[float]:
    """1-based ranks; tied values share the mean of the ranks they span."""
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        mean_rank = (i + j) / 2 + 1
        for idx in order[i : j + 1]:
            ranks[idx] = mean_rank
        i = j + 1
    return ranks


def auc(scores: Sequence[float], labels: Sequence[bool]) -> float | None:
    """P(random positive scores higher than random negative), ties 0.5 (Mann-Whitney U)."""
    n_pos = sum(1 for label in labels if label)
    n_neg = len(labels) - n_pos
    if n_pos == 0 or n_neg == 0:
        return None
    ranks = _average_ranks(scores)
    rank_sum = sum(r for r, label in zip(ranks, labels, strict=True) if label)
    return (rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    """Pearson correlation of average ranks; None for n < 3 or a constant side."""
    n = len(x)
    if n < 3:
        return None
    rx, ry = _average_ranks(x), _average_ranks(y)
    mx, my = sum(rx) / n, sum(ry) / n
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    return sxy / math.sqrt(sxx * syy)


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def _rate(k: int, n: int) -> float:
    return k / n if n else 0.0


# -- report ----------------------------------------------------------------------------


def _bucket_stats(bucket: Bucket, rows: Sequence[Row], total: int) -> BucketStats:
    members = [r for r in rows if r.bucket == bucket]
    n = len(members)
    reverted = sum(1 for r in members if r.outcome.reverted)
    followup = sum(1 for r in members if r.outcome.fix_followup)
    requested = sum(1 for r in members if r.outcome.changes_requested > 0)
    return BucketStats(
        bucket=bucket,
        n=n,
        share=_rate(n, total),
        reverted=reverted,
        reverted_rate=_rate(reverted, n),
        reverted_ci=wilson_interval(reverted, n),
        fix_followup=followup,
        fix_followup_rate=_rate(followup, n),
        fix_followup_ci=wilson_interval(followup, n),
        median_review_threads=_median([r.outcome.review_threads for r in members]),
        median_hours_to_merge=_median([r.outcome.hours_to_merge for r in members]),
        changes_requested=requested,
        changes_requested_share=_rate(requested, n),
    )


def _compute(
    repo: str,
    rows: Sequence[Row],
    skipped_no_head: int,
    policy: Policy,
    now: datetime,
    with_agent: bool,
) -> RepoReport:
    total = len(rows)
    scores = [float(r.score) for r in rows]
    buckets = [_bucket_stats(b, rows, total) for b in BUCKETS]
    low = buckets[0]
    agent_rows = [r for r in rows if r.agent_authored]
    agent = (
        _compute(repo, agent_rows, 0, policy, now, False)
        if with_agent and len(agent_rows) >= MIN_AGENT_ROWS
        else None
    )
    return RepoReport(
        repo=repo,
        n=total,
        skipped_no_head=skipped_no_head,
        buckets=buckets,
        auc_reverted=auc(scores, [r.outcome.reverted for r in rows]),
        auc_fix_followup=auc(scores, [r.outcome.fix_followup for r in rows]),
        spearman_review_threads=spearman(scores, [float(r.outcome.review_threads) for r in rows]),
        low_share=low.share,
        reverted_total=sum(1 for r in rows if r.outcome.reverted),
        reverted_in_low=low.reverted,
        high_low_revert_ratio=(
            buckets[2].reverted_rate / low.reverted_rate if low.reverted_rate > 0 else None
        ),
        signal_fire_rate={
            name: _rate(sum(1 for r in rows if r.signals.get(name, 0) > 0), total)
            for name in Weights.model_fields
        },
        agent=agent,
        policy_fingerprint=policy.fingerprint(),
        tool_version=version("review-compass"),
        generated_at=now,
    )


def compute_report(
    repo: str, rows: Sequence[Row], skipped_no_head: int, policy: Policy, now: datetime
) -> RepoReport:
    return _compute(repo, rows, skipped_no_head, policy, now, True)


# -- replay ----------------------------------------------------------------------------


class _Scored(BaseModel):
    number: int
    score: int
    bucket: Bucket
    agent_authored: bool
    signals: dict[str, int]


def _load_scored(path: Path, header: dict[str, str]) -> dict[int, _Scored] | None:
    """Scored PRs of a resumable file; None when absent, unreadable or for another policy.

    A bad final line (a crash mid-write) is dropped and the file is rewritten without it; a
    bad line anywhere else means the file cannot be trusted.
    """
    try:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        if not lines or json.loads(lines[0]) != header:
            return None
        scored: list[_Scored] = []
        for position, line in enumerate(lines[1:], start=1):
            try:
                scored.append(_Scored.model_validate_json(line))
            except ValueError:
                if position != len(lines) - 1:
                    return None
        if len(scored) != len(lines) - 1 or not text.endswith("\n"):
            body = "".join(s.model_dump_json() + "\n" for s in scored)
            path.write_text(json.dumps(header) + "\n" + body, encoding="utf-8")
    except (OSError, ValueError):
        return None
    return {s.number: s for s in scored}


def _score_one(
    git: GitRepo, pr: HistoricPR, policy: Policy, index: ImportIndex
) -> _Scored | None:
    try:
        features = extract_features(git, pr.to_meta(), policy, index)
    except (GitError, OSError):
        return None
    result = score(features, policy)
    return _Scored(
        number=pr.number,
        score=result.score,
        bucket=result.bucket,
        agent_authored=features.agent_authored,
        signals={c.signal: c.points for c in result.contributions},
    )


def run_backtest(
    repo: str,
    limit: int,
    out_dir: Path,
    cache_dir: Path,
    policy: Policy,
    gh: GraphQLClient,
    now: datetime,
    *,
    clone_url: str | None = None,
) -> RepoReport:
    _check_repo(repo)
    slug = repo.replace("/", "__")
    repo_cache = cache_dir / slug
    repo_out = out_dir / slug
    repo_out.mkdir(parents=True, exist_ok=True)

    prs = fetch_merged_prs(gh, repo, limit, repo_cache)
    # label_outcomes reads the clone's history, so the clone comes first; it still runs over
    # ALL fetched PRs because reverts and follow-ups live among the newest ones.
    git = ensure_clone(repo, repo_cache, url=clone_url)
    outcomes = label_outcomes(prs, git, "HEAD")
    todo = evaluable(prs, now)

    header = {
        "policy_fingerprint": policy.fingerprint(),
        "tool_version": version("review-compass"),
        "scoring_rules_version": SCORING_RULES_VERSION,
    }
    scored_path = repo_out / "scored.jsonl"
    scored = _load_scored(scored_path, header)
    fresh = scored is None
    done: dict[int, _Scored] = {} if scored is None else scored
    pending = [p for p in todo if p.number not in done]

    missing = fetch_pr_heads(git, [p.number for p in pending])
    index = ImportIndex(git, repo_cache / "blobs.json")
    handled = 0
    newly_scored = 0
    with scored_path.open("w" if fresh else "a", encoding="utf-8") as fh:
        if fresh:
            fh.write(json.dumps(header) + "\n")
            fh.flush()
        for pr in pending:
            handled += 1
            if handled % PROGRESS_EVERY == 0:
                print(f"review-compass: {repo}: {handled}/{len(pending)} scored", file=sys.stderr)
            if pr.number in missing:
                continue
            item = _score_one(git, pr, policy, index)
            if item is None:
                continue
            done[pr.number] = item
            fh.write(item.model_dump_json() + "\n")
            fh.flush()
            newly_scored += 1
            if newly_scored % INDEX_SAVE_EVERY == 0:
                index.save()
    index.save()

    rows = [
        Row(
            repo=repo,
            number=pr.number,
            score=done[pr.number].score,
            bucket=done[pr.number].bucket,
            agent_authored=done[pr.number].agent_authored,
            signals=done[pr.number].signals,
            outcome=outcomes[pr.number],
        )
        for pr in todo
        if pr.number in done
    ]
    report = compute_report(repo, rows, len(todo) - len(rows), policy, now)
    (repo_out / "rows.jsonl").write_text(
        "".join(r.model_dump_json() + "\n" for r in rows), encoding="utf-8"
    )
    (repo_out / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report
