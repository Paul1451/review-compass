"""Deterministic risk scoring: pure function from features and policy to a RiskResult."""

import math

from review_compass.models import (
    Bucket,
    Contribution,
    FileFeatures,
    FileKind,
    FileRank,
    PRFeatures,
    RiskResult,
)
from review_compass.policy import Policy, Weights

_SKIM_KINDS = {FileKind.DOC, FileKind.GENERATED, FileKind.LOCKFILE}


def _points(weight: int, fraction: float) -> int:
    # Epsilon absorbs float noise so exact .5 products round half up.
    return int(weight * fraction + 0.5 + 1e-9)


def _dependents_fraction(n: int) -> float:
    return min(1.0, math.floor(math.log2(1 + n)) / 3)


def _size_fraction(lines: int) -> float:
    return min(1.0, math.log2(1 + lines / 10) / 5)


def _first_hotspot(features: PRFeatures) -> str | None:
    best: FileFeatures | None = None
    for f in features.files:
        if f.kind is not FileKind.SOURCE:
            continue
        if best is None or f.churn_90d > best.churn_90d:
            best = f
    return best.change.path if best is not None else None


def _contributions(
    features: PRFeatures, w: Weights, files: list[FileRank]
) -> list[Contribution]:
    sensitive = [f for f in features.files if f.sensitive]
    sensitive_paths = {f.change.path for f in sensitive}
    # Name the sensitive file a reviewer meets first (reading order), not diff order.
    first_sensitive = next((r.path for r in files if r.path in sensitive_paths), None)
    n_dep = features.dependents_total or 0
    entries: list[tuple[str, int, str]] = []

    reason = ""
    if sensitive and first_sensitive is not None:
        reason = f"touches sensitive path: {first_sensitive}"
        if len(sensitive) > 1:
            reason += f" (+{len(sensitive) - 1} more)"
    entries.append(("sensitive_path", _points(w.sensitive_path, 1 if sensitive else 0), reason))

    no_tests = features.source_changed and not features.tests_changed
    entries.append(
        ("no_tests", _points(w.no_tests, 1 if no_tests else 0), "source changed, no test changes")
    )
    entries.append(
        (
            "dependents",
            _points(w.dependents, _dependents_fraction(n_dep)),
            f"{n_dep} files depend on changed modules",
        )
    )
    entries.append(
        (
            "size",
            _points(w.size, _size_fraction(features.effective_lines)),
            f"{features.effective_lines} lines changed",
        )
    )
    hot_path = _first_hotspot(features)
    hot_name = f" {hot_path}" if hot_path is not None else ""
    entries.append(
        (
            "hotspot",
            _points(w.hotspot, min(1.0, features.max_churn / 9)),
            f"hotspot:{hot_name} changed {features.max_churn}x in 90 days",
        )
    )
    pct = int(features.unfamiliar_share * 100 + 0.5)
    entries.append(
        (
            "unfamiliar",
            _points(w.unfamiliar, features.unfamiliar_share),
            f"author has not changed {pct}% of these files before",
        )
    )
    entries.append(
        (
            "spread",
            _points(w.spread, min(1.0, max(0, features.directories - 1) / 5)),
            f"spans {features.directories} directories",
        )
    )
    entries.append(
        (
            "agent",
            _points(w.agent, 1 if features.agent_authored else 0),
            f"authored by agent ({features.agent_evidence})",
        )
    )

    kept = [Contribution(signal=s, points=p, reason=r) for s, p, r in entries if p > 0]
    return sorted(kept, key=lambda c: -c.points)  # stable: ties keep table order


def _rank_file(f: FileFeatures, w: Weights) -> FileRank:
    change = f.change
    lines = change.additions + change.deletions
    total = 0
    reasons: list[str] = []

    if f.sensitive:
        total += _points(w.sensitive_path, 1)
        reasons.append("sensitive path")
    if f.dependents:
        p = _points(w.dependents, _dependents_fraction(f.dependents))
        if p > 0:
            total += p
            reasons.append(f"{f.dependents} dependents")
    p = _points(w.hotspot, min(1.0, f.churn_90d / 9))
    if p > 0:
        total += p
        reasons.append(f"changed {f.churn_90d}x in 90 days")
    if f.existed_at_base and not f.author_touched_before:
        total += _points(w.unfamiliar, 1)
        reasons.append("new to author")
    p = _points(w.size, _size_fraction(lines))
    if p > 0:
        total += p
        reasons.append(f"{lines} lines")

    skim = f.kind in _SKIM_KINDS or (change.status == "renamed" and lines == 0)
    span = max(change.hunks, key=lambda h: h[1] - h[0]) if change.hunks else None
    return FileRank(path=change.path, score=total, reasons=reasons, skim=skim, span=span)


def _rank_files(features: PRFeatures, w: Weights) -> list[FileRank]:
    ranked = [(f.kind, _rank_file(f, w)) for f in features.files]

    def by_score(r: FileRank) -> tuple[int, str]:
        return (-r.score, r.path)

    code = sorted((r for k, r in ranked if not r.skim and k is not FileKind.TEST), key=by_score)
    tests = sorted((r for k, r in ranked if not r.skim and k is FileKind.TEST), key=by_score)
    skimmed = sorted((r for _, r in ranked if r.skim), key=lambda r: r.path)
    return code + tests + skimmed


def _bucket(total: int, policy: Policy) -> Bucket:
    if total >= policy.thresholds.high:
        return "high"
    if total >= policy.thresholds.medium:
        return "medium"
    return "low"


def score(features: PRFeatures, policy: Policy) -> RiskResult:
    files = _rank_files(features, policy.weights)
    if features.docs_only:
        return RiskResult(
            score=0,
            bucket="low",
            contributions=[],
            files=files,
            guard_touched=features.guard_touched,
            notes=["only documentation, generated files or lockfiles"],
        )
    contributions = _contributions(features, policy.weights, files)
    total = min(100, sum(c.points for c in contributions))
    notes: list[str] = []
    if features.dependents_total is None and features.source_changed:
        notes.append("dependents: only measured for Python, JavaScript and TypeScript files")
    return RiskResult(
        score=total,
        bucket=_bucket(total, policy),
        contributions=contributions,
        files=files,
        guard_touched=features.guard_touched,
        notes=notes,
    )
