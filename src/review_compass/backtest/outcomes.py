"""Outcome labelling for the backtest: was a merged PR reverted, did it need a fix, how costly.

All matched text (titles, bodies, commit messages) is untrusted: every pattern is linear-time
and each text is capped at ``MAX_TEXT`` characters before matching.
"""

import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta

from pydantic import BaseModel

from review_compass.backtest.history import HistoricPR
from review_compass.gitrepo import GitRepo

WINDOW_DAYS = 14
MAX_TEXT = 20_000
_WINDOW = timedelta(days=WINDOW_DAYS)

# "Reverts #12", "Reverts owner/repo#12"; (?!\w) keeps #12 distinct from #123 and #12x.
_REVERTS_RE = re.compile(r"\breverts\s+(?:[\w.-]+/[\w.-]+)?#(\d+)(?!\w)", re.IGNORECASE)
# Plain "#12" reference; (?<!\w) rejects "abc#12" and "owner/repo#12".
_REF_RE = re.compile(r"(?<!\w)#(\d+)(?!\w)")
_FIX_RE = re.compile(r"\b(fix|fixes|fixed|hotfix|regression)\b", re.IGNORECASE)
_REVERT_COMMIT_RE = re.compile(r"this reverts commit\s+([0-9a-f]{7,40})(?![0-9a-f])", re.IGNORECASE)


class Outcome(BaseModel):
    reverted: bool
    fix_followup: bool
    review_threads: int
    changes_requested: int
    hours_to_merge: float


def _cap(text: str) -> str:
    return text[:MAX_TEXT]


def _numbers(pattern: re.Pattern[str], *texts: str) -> set[int]:
    found: set[int] = set()
    for text in texts:
        for m in pattern.finditer(_cap(text)):
            digits = m.group(1)
            if len(digits) <= 12:
                found.add(int(digits))
    return found


def _in_window(start: datetime, moment: datetime) -> bool:
    return start < moment <= start + _WINDOW


def label_outcomes(
    prs: Sequence[HistoricPR], git: GitRepo, default_ref: str
) -> dict[int, Outcome]:
    if not prs:
        return {}

    # Index candidate follow-up PRs once; nothing below scans all PRs per PR.
    by_title: dict[str, list[HistoricPR]] = defaultdict(list)
    revert_refs: dict[int, list[HistoricPR]] = defaultdict(list)
    fix_refs: dict[int, list[HistoricPR]] = defaultdict(list)
    for q in prs:
        by_title[_cap(q.title).strip()].append(q)
        for n in _numbers(_REVERTS_RE, q.body):
            revert_refs[n].append(q)
        if _FIX_RE.search(_cap(q.title)):
            for n in _numbers(_REF_RE, q.title, q.body):
                fix_refs[n].append(q)

    # One git call for the whole span; revert commits are indexed by the sha prefixes they name.
    prefixes: dict[str, list[HistoricPR]] = defaultdict(list)
    for p in prs:
        for sha in (p.merge_commit_sha, p.head_sha):
            if sha:
                for length in range(7, min(len(sha), 40) + 1):
                    prefixes[sha[:length].lower()].append(p)
    reverting_commits: dict[int, list[datetime]] = defaultdict(list)
    start = min(p.merged_at for p in prs)
    end = max(p.merged_at for p in prs) + _WINDOW + timedelta(seconds=1)
    for _sha, when, message in git.log_between_timed(default_ref, start, end):
        for m in _REVERT_COMMIT_RE.finditer(_cap(message)):
            for p in prefixes.get(m.group(1).lower(), ()):
                reverting_commits[p.number].append(when)

    outcomes: dict[int, Outcome] = {}
    for p in prs:
        def created_in_window(q: HistoricPR, p: HistoricPR = p) -> bool:
            return q.number != p.number and _in_window(p.merged_at, q.created_at)

        wanted_title = f'Revert "{_cap(p.title).strip()}"'
        reverted = (
            any(created_in_window(q) for q in by_title.get(wanted_title, ()))
            or any(created_in_window(q) for q in revert_refs.get(p.number, ()))
            or any(_in_window(p.merged_at, t) for t in reverting_commits.get(p.number, ()))
        )
        fix_followup = reverted or any(
            created_in_window(q) for q in fix_refs.get(p.number, ())
        )
        outcomes[p.number] = Outcome(
            reverted=reverted,
            fix_followup=fix_followup,
            review_threads=p.review_threads,
            changes_requested=p.changes_requested,
            hours_to_merge=(p.merged_at - p.created_at).total_seconds() / 3600,
        )
    return outcomes


def evaluable(prs: Sequence[HistoricPR], now: datetime) -> list[HistoricPR]:
    cutoff = now - _WINDOW
    return [p for p in prs if p.merged_at <= cutoff]
