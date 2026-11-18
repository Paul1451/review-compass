from pathlib import Path

import pytest

from review_compass.models import (
    Brief,
    Contribution,
    FileRank,
    IntentCheck,
    ReadingNote,
    RiskResult,
)
from review_compass.render import MARKER, render_comment

GOLDEN = Path(__file__).parent / "golden"


def _rank(path: str, *, skim: bool = False, span: tuple[int, int] | None = None) -> FileRank:
    return FileRank(path=path, score=10, reasons=[], skim=skim, span=span)


def _high() -> RiskResult:
    return RiskResult(
        score=72,
        bucket="high",
        contributions=[
            Contribution(
                signal="sensitive", points=25, reason="touches sensitive path: auth/session.py"
            ),
            Contribution(signal="no_tests", points=18, reason="source changed, no test changes"),
            Contribution(
                signal="dependents", points=15, reason="12 files depend on changed modules"
            ),
            Contribution(
                signal="hotspot", points=9, reason="hotspot: auth/session.py changed 9x in 90 days"
            ),
            Contribution(signal="spread", points=5, reason="spans 6 directories"),
        ],
        files=[
            _rank("auth/session.py", span=(88, 140)),
            _rank("api/login.py", span=(12, 30)),
            *[_rank(f"d{i}.md", skim=True) for i in range(1, 10)],
        ],
        guard_touched=False,
        notes=[],
    )


def _plain_brief() -> Brief:
    return Brief(intent=IntentCheck(status="not_run", note="no API key"), reading_notes=[])


def test_high_with_llm_matches_golden() -> None:
    brief = Brief(
        intent=IntentCheck(
            status="extends",
            note="Issue #31 asks for a logout fix. This PR also changes token lifetime.",
        ),
        reading_notes=[
            ReadingNote(path="auth/session.py", why="session invalidation rewritten"),
            ReadingNote(path="api/login.py", why="caller of changed function"),
            ReadingNote(path="not/listed.py", why="ignored"),
        ],
    )
    assert render_comment(_high(), brief) == (GOLDEN / "comment_high.md").read_text()


def test_no_llm_matches_golden() -> None:
    result = RiskResult(
        score=40,
        bucket="medium",
        contributions=[
            Contribution(signal="size", points=30, reason="large change"),
            Contribution(signal="spread", points=10, reason="many directories"),
        ],
        files=[_rank("core/engine.py"), _rank("core/util.py")],
        guard_touched=False,
        notes=[],
    )
    assert render_comment(result, _plain_brief()) == (GOLDEN / "comment_no_llm.md").read_text()


def test_starts_with_marker_and_ends_with_single_newline() -> None:
    out = render_comment(_high(), _plain_brief())
    assert out.startswith(MARKER + "\n")
    assert out.endswith("\n") and not out.endswith("\n\n")
    assert all(line == line.rstrip() for line in out.splitlines())


def test_docs_only_result() -> None:
    result = RiskResult(
        score=0,
        bucket="low",
        contributions=[],
        files=[_rank("README.md", skim=True)],
        guard_touched=False,
        notes=[],
    )
    out = render_comment(
        result, Brief(intent=IntentCheck(status="no_issue", note=""), reading_notes=[])
    )
    assert "## Review Compass: LOW (0/100)" in out
    assert "- nothing notable" in out
    assert "no linked issue" in out
    assert "**Read first**" not in out
    assert "**Skim (1 file)**\n`README.md`" in out
    assert "<sub>" not in out


def test_seven_non_skim_files_show_five_and_overflow() -> None:
    result = RiskResult(
        score=50,
        bucket="medium",
        contributions=[],
        files=[_rank(f"src/f{i}.py") for i in range(7)],
        guard_touched=False,
        notes=[],
    )
    out = render_comment(result, _plain_brief())
    assert "5. `src/f4.py`" in out
    assert "6." not in out
    assert "…and 2 more changed files" in out


def test_untrusted_path_cannot_break_out_of_code_span() -> None:
    result = RiskResult(
        score=1,
        bucket="low",
        contributions=[],
        files=[_rank("a`b\n**x**\x07.py"), _rank("s`\nk.md", skim=True)],
        guard_touched=False,
        notes=[],
    )
    out = render_comment(result, _plain_brief())
    assert "1. `ab**x**.py`" in out
    assert "`sk.md`" in out


ZWSP = "​"

HOSTILE = [
    ("ping @someuser now", f"ping @{ZWSP}someuser now"),
    ("[a](http://x)", f"\\[a\\](http:{ZWSP}//x)"),
    ("![i](http://x)", None),
    ("<!-- hide", "&lt;\\!-- hide"),
    ("</sub><img src=x>", "&lt;/sub&gt;&lt;img src=x&gt;"),
    ("see #12", "see \\#12"),
    ("visit www.evil.example", f"visit www{ZWSP}.evil.example"),
    ("a_b*c", "a\\_b\\*c"),
    ("cost $x$ and $$y$$", "cost \\$x\\$ and \\$\\$y\\$\\$"),
    ("a\u061cb", "ab"),
]


def _hostile_result(reason: str, note: str) -> RiskResult:
    return RiskResult(
        score=1,
        bucket="low",
        contributions=[Contribution(signal="s", points=1, reason=reason)],
        files=[],
        guard_touched=False,
        notes=[note],
    )


@pytest.mark.parametrize(("raw", "expected"), HOSTILE)
def test_hostile_reason_and_note_are_escaped(raw: str, expected: str | None) -> None:
    out = render_comment(_hostile_result(raw, raw), _plain_brief())
    reason_line = next(ln for ln in out.splitlines() if ln.startswith("- ") and "(+1)" in ln)
    notes_line = next(ln for ln in out.splitlines() if ln.startswith("<sub>"))
    if expected is None:
        assert reason_line.startswith("- \\!\\[i\\]")
        assert notes_line.startswith("<sub>Notes: \\!\\[i\\]")
    else:
        assert reason_line == f"- {expected} (+1)"
        assert notes_line == f"<sub>Notes: {expected}</sub>"


def test_hostile_note_cannot_close_sub_wrapper() -> None:
    out = render_comment(_hostile_result("x", "</sub> boom </SUB>"), _plain_brief())
    notes_line = next(ln for ln in out.splitlines() if ln.startswith("<sub>"))
    assert notes_line.count("</sub>") == 1
    assert notes_line.endswith("</sub>")


def test_intent_note_and_reading_why_are_escaped() -> None:
    brief = Brief(
        intent=IntentCheck(status="extends", note="fixes #3 for @bob"),
        reading_notes=[ReadingNote(path="a.py", why="see [x](http://y)")],
    )
    result = RiskResult(
        score=1,
        bucket="low",
        contributions=[],
        files=[_rank("a.py")],
        guard_touched=False,
        notes=[],
    )
    out = render_comment(result, brief)
    assert f"fixes \\#3 for @{ZWSP}bob" in out
    assert f"1. `a.py` — see \\[x\\](http:{ZWSP}//y)" in out


def test_bidi_and_zero_width_chars_are_removed_from_paths() -> None:
    result = RiskResult(
        score=1,
        bucket="low",
        contributions=[],
        files=[_rank("a‮b​c﻿.py"), _rank("`‮`")],
        guard_touched=False,
        notes=[],
    )
    out = render_comment(result, _plain_brief())
    assert "1. `abc.py`" in out
    assert "2. `(unnamed)`" in out
    assert "‮" not in out
