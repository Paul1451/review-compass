"""Render a RiskResult and a Brief as the single PR comment."""

import re

from review_compass.models import Brief, FileRank, RiskResult

MARKER = "<!-- review-compass -->"

_MAX_READ_FIRST = 5
_MAX_SKIM_PATHS = 8
_UNSAFE = re.compile(r"[`\x00-\x1f\x7f-\x9f  ]")
_WHITESPACE = re.compile(r"\s+")


def _clean_path(path: str) -> str:
    """Strip backticks, newlines and control characters from an untrusted path."""
    return _UNSAFE.sub("", path)


def _one_line(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _read_first_line(index: int, rank: FileRank, why: str | None) -> str:
    label = _clean_path(rank.path)
    if rank.span is not None:
        label += f":{rank.span[0]}-{rank.span[1]}"
    line = f"{index}. `{label}`"
    if why:
        line += f" — {why}"
    return line


def _intent_text(brief: Brief) -> str:
    intent = brief.intent
    if intent.status == "not_run":
        return f"not run ({_one_line(intent.note)})"
    if intent.status == "no_issue":
        return "no linked issue"
    return _one_line(intent.note)


def render_comment(result: RiskResult, brief: Brief) -> str:
    lines = [
        MARKER,
        f"## Review Compass: {result.bucket.upper()} ({result.score}/100)",
        "",
        "**Why**",
    ]
    if result.contributions:
        lines.extend(f"- {_one_line(c.reason)} ({c.points:+d})" for c in result.contributions)
    else:
        lines.append("- nothing notable")

    lines += ["", "**Intent check**", _intent_text(brief)]

    read_first = [f for f in result.files if not f.skim]
    skim = [f for f in result.files if f.skim]

    if read_first:
        shown = read_first[:_MAX_READ_FIRST]
        notes: dict[str, str] = {}
        for note in brief.reading_notes:
            why = _one_line(note.why)
            if why:
                notes.setdefault(note.path, why)
        lines += ["", "**Read first**"]
        for i, rank in enumerate(shown, start=1):
            lines.append(_read_first_line(i, rank, notes.get(rank.path)))
        extra = len(read_first) - len(shown)
        if extra > 0:
            lines.append(f"…and {extra} more changed files")

    if skim:
        noun = "file" if len(skim) == 1 else "files"
        listed = ", ".join(f"`{_clean_path(f.path)}`" for f in skim[:_MAX_SKIM_PATHS])
        more = len(skim) - _MAX_SKIM_PATHS
        if more > 0:
            listed += f", …and {more} more"
        lines += ["", f"**Skim ({len(skim)} {noun})**", listed]

    if result.notes:
        joined = "; ".join(_one_line(n) for n in result.notes)
        lines += ["", f"<sub>Notes: {joined}</sub>"]

    return "\n".join(lines) + "\n"
