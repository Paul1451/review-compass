from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import row

from review_compass.backtest.metrics import RepoReport, compute_report
from review_compass.backtest.report import fmt, pct, write_reports
from review_compass.policy import load_policy

NOW = datetime(2026, 3, 1, tzinfo=UTC)


def _report(repo: str = "o/r") -> RepoReport:
    rows = [row(10, "low")] * 6 + [row(40, "medium")] * 3 + [row(70, "high", reverted=True)]
    return compute_report(repo, rows, 2, load_policy(None), NOW)


def test_pct_rounds_half_up_on_tenths() -> None:
    assert pct(0.6) == "60.0%" and pct(0.0) == "0.0%" and pct(1.0) == "100.0%"
    assert pct(0.12345) == "12.3%" and pct(0.1235) == "12.4%"


def test_report_markdown_contains_summary_line(tmp_path: Path) -> None:
    write_reports([_report()], tmp_path)
    text = (tmp_path / "REPORT.md").read_text()
    assert (
        "Skimming low-risk PRs would cover 60.0% of PRs and contain 0 of 1 reverted PRs." in text
    )
    assert "## o/r" in text and "| low | 6 | 60.0% |" in text
    assert "AUC, score vs reverted: 1.000" in text
    assert "Policy fingerprint: " + load_policy(None).fingerprint() in text
    assert "Generated: 2026-03-01" in text


def test_report_marks_missing_statistics(tmp_path: Path) -> None:
    write_reports([compute_report("o/r", [], 0, load_policy(None), NOW)], tmp_path)
    text = (tmp_path / "REPORT.md").read_text()
    assert "score vs reverted: n/a" in text and "0 of 0 reverted PRs" in text


def test_report_rejects_unsafe_repo_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        write_reports([_report("o/r\n# injected")], tmp_path)


def test_fmt_is_half_up() -> None:
    assert fmt(1.25, 1) == "1.3" and fmt(0.0005, 3) == "0.001"
    assert fmt(-0.9995, 3) == "-1.000" and fmt(60.0, 1) == "60.0"
    assert fmt(0.0, 3) == "0.000"


def test_report_uses_half_up_for_medians(tmp_path: Path) -> None:
    rows = [row(10, "low", hours=1.25)]
    write_reports([compute_report("o/r", rows, 0, load_policy(None), NOW)], tmp_path)
    assert "| 0.0 | 1.3 |" in (tmp_path / "REPORT.md").read_text()
