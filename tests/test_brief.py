from types import SimpleNamespace
from typing import Any

from review_compass.brief import AnthropicBriefClient, build_brief, sanitize
from review_compass.models import FileRank, IntentCheck, Issue, PRMeta, ReadingNote, RiskResult
from review_compass.policy import load_policy

META = PRMeta(
    number=7,
    title="Fix logout",
    body="Closes #31",
    author="alice",
    author_is_bot=False,
    head_ref="fix/logout",
    base_sha="a" * 40,
    head_sha="b" * 40,
    draft=False,
    is_fork=False,
)
ISSUE = Issue(number=31, title="Logout broken", body="Logging out keeps the session alive.")
RESULT = RiskResult(
    score=50,
    bucket="medium",
    contributions=[],
    files=[FileRank(path="auth/session.py", score=40, reasons=[], skim=False, span=(1, 20))],
    guard_touched=False,
    notes=[],
)
DOCS_RESULT = RiskResult(
    score=0,
    bucket="low",
    contributions=[],
    files=[FileRank(path="README.md", score=0, reasons=[], skim=True, span=None)],
    guard_touched=False,
    notes=["documentation only"],
)
P = load_policy(None)


class FakeClient:
    def __init__(self, response: dict[str, Any] | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def complete(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_no_client() -> None:
    b = build_brief(META, [], "diff", RESULT, P, None)
    assert (b.intent.status, b.intent.note, b.reading_notes) == ("not_run", "no API key", [])


def test_disabled_and_docs_only_do_not_call_client() -> None:
    fc = FakeClient({})
    disabled = load_policy("llm: {enabled: false}")
    assert build_brief(META, [], "d", RESULT, disabled, fc).intent.note == "disabled in policy"
    assert build_brief(META, [], "d", DOCS_RESULT, P, fc).intent.note == "documentation only"
    assert fc.calls == []


def test_no_non_skim_files_does_not_call_client() -> None:
    fc = FakeClient({})
    skim_only = RESULT.model_copy(
        update={"files": [FileRank(path="a.md", score=1, reasons=[], skim=True, span=None)]}
    )
    assert build_brief(META, [], "d", skim_only, P, fc).intent.note == "nothing to review"
    assert fc.calls == []


def test_client_error_becomes_not_run() -> None:
    brief = build_brief(META, [ISSUE], "d", RESULT, P, FakeClient(RuntimeError("boom")))
    assert brief.intent == IntentCheck(status="not_run", note="error")


def test_invalid_payload_becomes_not_run() -> None:
    fc = FakeClient({"intent": {"status": "approve"}})
    assert build_brief(META, [ISSUE], "d", RESULT, P, fc).intent.note == "error"


def test_notes_filtered_to_known_paths_and_sanitized() -> None:
    payload = {
        "intent": {"status": "extends", "note": "Also changes   token lifetime <b>@alice</b> `x`"},
        "reading_notes": [
            {"path": "auth/session.py", "why": "rewritten"},
            {"path": "/etc/passwd", "why": "x"},
            {"path": "auth/session.py", "why": "dup"},
        ],
    }
    b = build_brief(META, [ISSUE], "d", RESULT, P, FakeClient(payload))
    assert b.intent.note == "Also changes token lifetime b(at)alice/b x"
    assert b.reading_notes == [ReadingNote(path="auth/session.py", why="rewritten")]


def test_no_issue_forces_status() -> None:
    payload = {"intent": {"status": "matches", "note": "ok"}, "reading_notes": []}
    assert build_brief(META, [], "d", RESULT, P, FakeClient(payload)).intent.status == "no_issue"


def test_diff_is_truncated_to_budget() -> None:
    fc = FakeClient({"intent": {"status": "unclear", "note": "n"}, "reading_notes": []})
    policy = load_policy("llm: {max_input_tokens: 1000}")
    build_brief(META, [ISSUE], "x" * 500_000, RESULT, policy, fc)
    assert len(fc.calls[0]["user"]) <= 4000 and fc.calls[0]["user"].endswith("[diff truncated]")
    assert fc.calls[0]["model"] == "claude-haiku-5-5"


def test_untrusted_parts_are_delimited_and_named_in_system_prompt() -> None:
    fc = FakeClient({"intent": {"status": "unclear", "note": "n"}, "reading_notes": []})
    build_brief(META, [ISSUE], "the diff", RESULT, P, fc)
    call = fc.calls[0]
    for tag in ("pr_title", "pr_body", "issue", "diff"):
        assert f"<{tag}" in call["user"] and f"<{tag}>" in call["system"]
    assert "untrusted" in call["system"] and "submit_brief" in call["system"]
    assert call["schema"]["properties"]["intent"]["properties"]["status"]["enum"] == [
        "matches",
        "extends",
        "unclear",
        "no_issue",
    ]


def test_body_and_issue_body_are_cut_to_4000() -> None:
    fc = FakeClient({"intent": {"status": "unclear", "note": "n"}, "reading_notes": []})
    meta = META.model_copy(update={"body": "B" * 9000})
    issue = ISSUE.model_copy(update={"body": "I" * 9000})
    build_brief(meta, [issue], "d", RESULT, P, fc)
    assert "B" * 4000 in fc.calls[0]["user"] and "B" * 4001 not in fc.calls[0]["user"]
    assert "I" * 4000 in fc.calls[0]["user"] and "I" * 4001 not in fc.calls[0]["user"]


def test_sanitize_limit() -> None:
    assert sanitize("a" * 300) == "a" * 200 + "…"


class FakeSDK:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text="ignored"),
                SimpleNamespace(type="tool_use", name="submit_brief", input={"k": "v"}),
            ]
        )


def test_anthropic_client_builds_forced_tool_request() -> None:
    sdk = FakeSDK()
    schema: dict[str, Any] = {"type": "object", "properties": {}}
    out = AnthropicBriefClient("unused", client=sdk).complete(
        model="m-1", system="SYS", user="USR", schema=schema
    )
    assert out == {"k": "v"}
    kw = sdk.kwargs
    assert kw["model"] == "m-1" and kw["max_tokens"] == 1024 and kw["system"] == "SYS"
    assert kw["messages"] == [{"role": "user", "content": "USR"}]
    assert kw["tools"][0]["name"] == "submit_brief" and kw["tools"][0]["input_schema"] == schema
    assert kw["tool_choice"] == {"type": "tool", "name": "submit_brief"}
