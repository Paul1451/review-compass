"""Derive the pooled and sensitivity numbers quoted in LABEL_CHECK.md and the README.

Standard library only. Run from anywhere: ``python backtest/derive_numbers.py``.
It reads the three ``rows.jsonl`` files that sit next to this script.
"""

import json
from collections.abc import Sequence
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOS = ("pydantic__pydantic", "home-assistant__core", "vercel__next.js")
BUCKETS = ("low", "medium", "high")

# Firm misses found by the label check (see LABEL_CHECK.md): merged PRs that were reverted
# but not labelled `reverted` by the rules.
MISSED_REVERTS: dict[str, tuple[int, ...]] = {
    "pydantic__pydantic": (8583, 9746, 10886, 13582),
    "home-assistant__core": (189191,),
    "vercel__next.js": (100651, 99689, 100597, 100126),
}

Row = dict[str, object]


def load(repo: str) -> list[Row]:
    path = HERE / repo / "rows.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _outcome(row: Row) -> dict[str, object]:
    outcome = row["outcome"]
    assert isinstance(outcome, dict)
    return outcome


def _score(row: Row) -> int:
    value = row["score"]
    assert isinstance(value, int)
    return value


def auc(scores: Sequence[float], labels: Sequence[bool]) -> float:
    """P(positive scores higher than negative), ties count 0.5."""
    positives = [s for s, label in zip(scores, labels, strict=True) if label]
    negatives = [s for s, label in zip(scores, labels, strict=True) if not label]
    wins = 0.0
    for p in positives:
        for n in negatives:
            wins += 1.0 if p > n else 0.5 if p == n else 0.0
    return wins / (len(positives) * len(negatives))


def pct(k: int, n: int) -> str:
    scaled = int(k / n * 1000 + 0.5 + 1e-9)
    return f"{scaled // 10}.{scaled % 10}%"


def pooled_table(all_rows: Sequence[Row]) -> list[str]:
    lines = [
        "| Bucket | PRs | Reverted | Reverted rate | Fix follow-up | Fix follow-up rate |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for bucket in BUCKETS:
        members = [r for r in all_rows if r["bucket"] == bucket]
        n = len(members)
        rev = sum(1 for r in members if _outcome(r)["reverted"])
        fix = sum(1 for r in members if _outcome(r)["fix_followup"])
        lines.append(f"| {bucket} | {n} | {rev} | {pct(rev, n)} | {fix} | {pct(fix, n)} |")
    return lines


def sensitivity_table() -> list[str]:
    lines = [
        "| Repo | Reverted as reported | AUC as reported | Reverted with firm misses "
        "| AUC with firm misses |",
        "|---|---:|---:|---:|---:|",
    ]
    for repo in REPOS:
        rows = load(repo)
        scores = [float(_score(r)) for r in rows]
        reported = [bool(_outcome(r)["reverted"]) for r in rows]
        missed = set(MISSED_REVERTS[repo])
        numbers = {r["number"] for r in rows}
        assert missed <= numbers, f"{repo}: missed PR not among the rows"
        with_misses = [lab or r["number"] in missed for r, lab in zip(rows, reported, strict=True)]
        lines.append(
            f"| {repo.replace('__', '/')} | {sum(reported)} | {auc(scores, reported):.3f} "
            f"| {sum(with_misses)} | {auc(scores, with_misses):.3f} |"
        )
    return lines


def side_numbers() -> list[str]:
    lines = ["| Repo | Rows | Agent-authored rows | Score 0 rows | Reverted among score 0 |"]
    lines.append("|---|---:|---:|---:|---:|")
    for repo in REPOS:
        rows = load(repo)
        agents = sum(1 for r in rows if r["agent_authored"])
        zero = [r for r in rows if _score(r) == 0]
        zero_rev = sum(1 for r in zero if _outcome(r)["reverted"])
        lines.append(
            f"| {repo.replace('__', '/')} | {len(rows)} | {agents} ({pct(agents, len(rows))}) "
            f"| {len(zero)} ({pct(len(zero), len(rows))}) | {zero_rev} |"
        )
    return lines


def main() -> None:
    all_rows = [row for repo in REPOS for row in load(repo)]
    print(f"Pooled counts ({len(all_rows)} evaluated PRs)\n")
    print("\n".join(pooled_table(all_rows)))
    print("\nSensitivity: missed reverts counted as reverted\n")
    print("\n".join(sensitivity_table()))
    print("\nAgent share and score 0\n")
    print("\n".join(side_numbers()))


if __name__ == "__main__":
    main()
