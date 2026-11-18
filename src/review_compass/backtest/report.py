"""REPORT.md rendering. Built only from report numbers and validated repository names."""

from collections.abc import Sequence
from pathlib import Path

from review_compass.backtest.history import _check_repo
from review_compass.backtest.metrics import BucketStats, RepoReport


def pct(x: float) -> str:
    """Fraction as a percentage with one decimal, half up on tenths."""
    return f"{int(x * 1000 + 0.5) / 10:.1f}%"


def _rate_ci(rate: float, ci: tuple[float, float]) -> str:
    return f"{pct(rate)} ({pct(ci[0])} to {pct(ci[1])})"


def _num(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.3f}"


def _row(b: BucketStats) -> str:
    return (
        f"| {b.bucket} | {b.n} | {pct(b.share)} | {_rate_ci(b.reverted_rate, b.reverted_ci)} "
        f"| {_rate_ci(b.fix_followup_rate, b.fix_followup_ci)} "
        f"| {b.median_review_threads:.1f} | {b.median_hours_to_merge:.1f} |"
    )


def _table(report: RepoReport) -> list[str]:
    return [
        "| Bucket | PRs | Share | Reverted (95% CI) | Fix follow-up (95% CI) "
        "| Median review threads | Median hours to merge |",
        "|---|---:|---:|---|---|---:|---:|",
        *(_row(b) for b in report.buckets),
        "",
        f"Skimming low-risk PRs would cover {pct(report.low_share)} of PRs and contain "
        f"{report.reverted_in_low} of {report.reverted_total} reverted PRs.",
        "",
    ]


def _section(report: RepoReport) -> list[str]:
    _check_repo(report.repo)
    lines = [f"## {report.repo}", ""]
    lines += [f"{report.n} evaluated PRs, {report.skipped_no_head} skipped (no head).", ""]
    lines += _table(report)
    lines += [
        f"- AUC, score vs reverted: {_num(report.auc_reverted)}",
        f"- AUC, score vs fix follow-up: {_num(report.auc_fix_followup)}",
        f"- Spearman, score vs review threads: {_num(report.spearman_review_threads)}",
        "",
        "Signal fire rates:",
        "",
        *(f"- {name}: {pct(rate)}" for name, rate in report.signal_fire_rate.items()),
        "",
    ]
    if report.agent is not None:
        lines += [f"### Agent-authored PRs ({report.agent.n})", "", *_table(report.agent)]
    lines += [
        f"Policy fingerprint: {report.policy_fingerprint}",
        f"Tool version: {report.tool_version}",
        f"Generated: {report.generated_at.date().isoformat()}",
        "",
    ]
    return lines


def render_report(reports: Sequence[RepoReport]) -> str:
    lines = ["# Review Compass backtest", ""]
    for report in reports:
        lines += _section(report)
    return "\n".join(lines)


def write_reports(reports: Sequence[RepoReport], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "REPORT.md").write_text(render_report(reports), encoding="utf-8")
