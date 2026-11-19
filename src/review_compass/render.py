"""Render a RiskResult and a Brief as the single PR comment."""

import re

from review_compass.models import Brief, FileRank, RiskResult

MARKER = "<!-- review-compass -->"

_MAX_READ_FIRST = 5
_MAX_SKIM_PATHS = 8
# C0/C1 controls, line/paragraph separators, bidi controls, zero-width characters, BOM.
_INVISIBLE = re.compile(
    "[\u0000-\u001f\u007f-\u009f\u2028\u2029\u202a-\u202e\u2066-\u2069\u061c\u200b-\u200f\ufeff]"
)
_WHITESPACE = re.compile(r"\s+")
_MD_SPECIAL = re.compile(r"[\\`*_\[\]#!~|$]")
_WWW = re.compile(r"www\.", re.IGNORECASE)
_ZWSP = "\u200b"


def _clean_path(path: str) -> str:
    """Strip backticks, newlines and invisible characters from an untrusted path."""
    cleaned = _INVISIBLE.sub("", path.replace("`", ""))
    return cleaned or "(unnamed)"


def _safe_text(text: str) -> str:
    """Neutralise Markdown, HTML, mentions and autolinks in untrusted free text."""
    out = _INVISIBLE.sub("", _WHITESPACE.sub(" ", text).strip())
    out = out.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    out = _MD_SPECIAL.sub(lambda m: "\\" + m.group(0), out)
    out = out.replace("@", "@" + _ZWSP)
    out = out.replace("://", ":" + _ZWSP + "//")
    return _WWW.sub(lambda m: m.group(0)[:3] + _ZWSP + ".", out)


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
        return f"not run ({_safe_text(intent.note)})"
    if intent.status == "no_issue":
        return "no linked issue"
    return _safe_text(intent.note)


def render_comment(result: RiskResult, brief: Brief) -> str:
    lines = [
        MARKER,
        f"## Review Compass: {result.bucket.upper()} ({result.score}/100)",
        "",
        "**Why**",
    ]
    if result.contributions:
        lines.extend(f"- {_safe_text(c.reason)} ({c.points:+d})" for c in result.contributions)
    else:
        lines.append("- nothing notable")

    lines += ["", "**Intent check**", _intent_text(brief)]

    read_first = [f for f in result.files if not f.skim]
    skim = [f for f in result.files if f.skim]

    if read_first:
        shown = read_first[:_MAX_READ_FIRST]
        notes: dict[str, str] = {}
        for note in brief.reading_notes:
            why = _safe_text(note.why)
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
        joined = "; ".join(_safe_text(n) for n in result.notes)
        lines += ["", f"<sub>Notes: {joined}</sub>"]

    return "\n".join(lines) + "\n"


def _plain(text: str) -> str:
    """Make untrusted text safe for a terminal: collapse whitespace, drop control characters."""
    return _INVISIBLE.sub("", _WHITESPACE.sub(" ", text).strip())


def _plain_path(path: str) -> str:
    return _plain(path) or "(unnamed)"


def _intent_plain(brief: Brief) -> str:
    intent = brief.intent
    if intent.status == "not_run":
        return f"not run ({_plain(intent.note)})"
    if intent.status == "no_issue":
        return "no linked issue"
    return _plain(intent.note)


def render_text(result: RiskResult, brief: Brief) -> str:
    """Plain-text rendering of the comment for reading in a terminal."""
    lines = [f"Review Compass: {result.bucket.upper()} ({result.score}/100)", "", "Why"]
    if result.contributions:
        lines.extend(f"  {c.points:+d}  {_plain(c.reason)}" for c in result.contributions)
    else:
        lines.append("  nothing notable")

    lines += ["", "Intent check", f"  {_intent_plain(brief)}"]

    read_first = [f for f in result.files if not f.skim]
    skim = [f for f in result.files if f.skim]

    if read_first:
        shown = read_first[:_MAX_READ_FIRST]
        notes: dict[str, str] = {}
        for note in brief.reading_notes:
            why = _plain(note.why)
            if why:
                notes.setdefault(note.path, why)
        lines += ["", "Read first"]
        for i, rank in enumerate(shown, start=1):
            label = _plain_path(rank.path)
            if rank.span is not None:
                label += f":{rank.span[0]}-{rank.span[1]}"
            line = f"  {i}. {label}"
            if rank.path in notes:
                line += f" — {notes[rank.path]}"
            lines.append(line)
        extra = len(read_first) - len(shown)
        if extra > 0:
            lines.append(f"  ...and {extra} more changed files")

    if skim:
        noun = "file" if len(skim) == 1 else "files"
        listed = ", ".join(_plain_path(f.path) for f in skim[:_MAX_SKIM_PATHS])
        more = len(skim) - _MAX_SKIM_PATHS
        if more > 0:
            listed += f", ...and {more} more"
        lines += ["", f"Skim ({len(skim)} {noun})", f"  {listed}"]

    if result.notes:
        lines += ["", "Notes: " + "; ".join(_plain(n) for n in result.notes)]

    return "\n".join(lines) + "\n"
