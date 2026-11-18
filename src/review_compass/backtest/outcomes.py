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
_REVERT_COMMIT_RE = re.compile(r"this reverts commit\s+([0-9a-f]{7,40})(?!\w)", re.IGNORECASE)


_BACKPORT_TITLE_RE = re.compile(r"\b(backport(ed|s)?|cherry[- ]?pick(ed|s)?)\b", re.IGNORECASE)
_BACKPORT_BODY_RE = re.compile(r"\bbackport of\b", re.IGNORECASE)
MAX_LOOSE_REFS = 5
_SHA_KEY = 7


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


def _loose_candidate(q: HistoricPR) -> set[int]:
    """Numbers q refers to as a possible fix follow-up; empty when q is filtered out."""
    title = _cap(q.title)
    if q.author_is_bot or not _FIX_RE.search(title):
        return set()
    if _BACKPORT_TITLE_RE.search(title) or _BACKPORT_BODY_RE.search(_cap(q.body)):
        return set()
    refs = _numbers(_REF_RE, q.title, q.body)
    return refs if len(refs) <= MAX_LOOSE_REFS else set()


def _created_in_window(p: HistoricPR, q: HistoricPR) -> bool:
    return q.number != p.number and _in_window(p.merged_at, q.created_at)


def label_outcomes(
    prs: Sequence[HistoricPR], git: GitRepo, default_ref: str
) -> dict[int, Outcome]:
    if not prs:
        return {}

    # Index candidate follow-up PRs once; nothing below scans all PRs per PR.
    by_title: dict[str, list[HistoricPR]] = defaultdict(list)
    revert_refs: dict[int, list[HistoricPR]] = defaultdict(list)
    fix_refs: dict[int, list[HistoricPR]] = defaultdict(list)
    explicit: set[int] = set()  # PRs whose body names what they revert; their title is ignored
    for q in prs:
        refs = _numbers(_REVERTS_RE, q.body)
        if refs:
            explicit.add(q.number)
        for n in refs:
            revert_refs[n].append(q)
        by_title[_cap(q.title).strip()].append(q)
        for n in _loose_candidate(q):
            fix_refs[n].append(q)

    # Shas are indexed by their first 7 characters and verified with startswith.
    by_sha_key: dict[str, list[HistoricPR]] = defaultdict(list)
    for p in prs:
        for sha in (p.merge_commit_sha, p.head_sha):
            if sha and len(sha) >= _SHA_KEY:
                by_sha_key[sha[:_SHA_KEY].lower()].append(p)

    # (time, source PR number or None for a commit) of every revert naming a PR's sha.
    revert_events: dict[int, list[tuple[datetime, int | None]]] = defaultdict(list)

    def record_reverts(text: str, when: datetime, source: int | None) -> None:
        for m in _REVERT_COMMIT_RE.finditer(_cap(text)):
            named = m.group(1).lower()
            for p in by_sha_key.get(named[:_SHA_KEY], ()):
                shas = (p.merge_commit_sha, p.head_sha)
                if any(s and s.lower().startswith(named) for s in shas):
                    revert_events[p.number].append((when, source))

    # One git call for the whole span.
    start = min(p.merged_at for p in prs)
    end = max(p.merged_at for p in prs) + _WINDOW + timedelta(seconds=1)
    for _sha, when, message in git.log_between_timed(default_ref, start, end):
        record_reverts(message, when, None)
    for q in prs:
        record_reverts(q.body, q.created_at, q.number)

    outcomes: dict[int, Outcome] = {}
    for p in prs:
        title = _cap(p.title).strip()
        by_wanted_title = [
            q
            for wanted in (f'Revert "{title}"', f'Revert "{title} (#{p.number})"')
            for q in by_title.get(wanted, ())
            if q.number not in explicit
        ]
        reverted = (
            any(_created_in_window(p, q) for q in by_wanted_title)
            or any(_created_in_window(p, q) for q in revert_refs.get(p.number, ()))
            or any(
                _in_window(p.merged_at, t) and src != p.number
                for t, src in revert_events.get(p.number, ())
            )
        )
        fix_followup = reverted or any(
            _created_in_window(p, q) for q in fix_refs.get(p.number, ())
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
