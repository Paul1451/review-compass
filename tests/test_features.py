import pytest

from review_compass.features import detect_agent, extract_features
from review_compass.models import PRMeta
from review_compass.policy import load_policy


def _meta(base: str, head: str, **kw) -> PRMeta:
    d = dict(
        number=1,
        title="t",
        body="",
        author="dev",
        author_is_bot=False,
        head_ref="feature/x",
        base_sha=base,
        head_sha=head,
        draft=False,
        is_fork=False,
    )
    return PRMeta(**{**d, **kw})


def test_extract_basic_signals(make_repo) -> None:
    b = make_repo()
    b.commit(
        {
            "src/auth/session.py": "def f():\n    return 1\n",
            "src/api.py": "from auth.session import f\n",
            "src/util.py": "U = 1\n",
        },
        author="Ann <ann@example.com>",
    )
    base = b.commit(
        {"src/auth/session.py": "def f():\n    return 2\n"}, author="Ann <ann@example.com>"
    )
    b.branch("feature/x")
    b.checkout("feature/x")
    head = b.commit(
        {
            "src/auth/session.py": "def f():\n    return 3\n",
            "src/util.py": "U = 2\n",
            "README.md": "hi\n",
        },
        author="Bob <bob@example.com>",
    )
    f = extract_features(b.repo, _meta(base, head), load_policy(None))
    by = {x.change.path: x for x in f.files}
    assert by["src/auth/session.py"].sensitive and by["src/auth/session.py"].churn_90d == 2
    assert by["src/auth/session.py"].dependents == 1 and f.dependents_total == 1
    assert f.source_changed and not f.tests_changed and not f.docs_only
    assert f.effective_lines == 4 and f.max_churn == 2 and f.directories == 2
    assert f.unfamiliar_share == 1.0 and not f.guard_touched and not f.agent_authored


def test_docs_only_and_guard(make_repo) -> None:
    b = make_repo()
    base = b.commit({"README.md": "a\n", "src/a.py": "x=1\n"})
    docs = b.commit({"README.md": "b\n"})
    assert extract_features(b.repo, _meta(base, docs), load_policy(None)).docs_only
    guard = b.commit({".github/workflows/ci.yml": "on: push\n"})
    assert extract_features(b.repo, _meta(docs, guard), load_policy(None)).guard_touched


def test_empty_diff_and_first_contribution(make_repo) -> None:
    b = make_repo()
    base = b.commit({"a.py": "x=1\n"})
    f = extract_features(b.repo, _meta(base, base), load_policy(None))
    assert f.files == [] and f.effective_lines == 0
    assert not f.docs_only and f.unfamiliar_share == 0.0


def test_rename_and_binary_do_not_crash(make_repo) -> None:
    b = make_repo()
    base = b.commit({"src/old.py": "a\nb\nc\nd\ne\n"})
    head = b.commit(
        {"src/old.py": None, "src/new.py": "a\nb\nc\nd\ne\n", "logo.png": b"\x89PNG\x00"}
    )
    f = extract_features(b.repo, _meta(base, head), load_policy(None))
    assert f.effective_lines == 0
    assert {x.change.path for x in f.files} == {"src/new.py", "logo.png"}


@pytest.mark.parametrize(
    "meta_kw,msgs,expected",
    [
        ({}, ["fix\n\nCo-Authored-By: Claude <noreply@anthropic.com>"], "Co-Authored-By: Claude"),
        (
            {"body": "x\n🤖 Generated with [Claude Code](https://claude.com/claude-code)"},
            [],
            "PR body: Generated with Claude Code",
        ),
        (
            {"author": "copilot-swe-agent[bot]", "author_is_bot": True},
            [],
            "bot author: copilot-swe-agent[bot]",
        ),
        ({"head_ref": "cursor/fix-login"}, [], "branch prefix: cursor/"),
        (
            {"author": "dependabot[bot]", "author_is_bot": True, "head_ref": "dependabot/pip/x"},
            ["bump x"],
            None,
        ),
        ({}, ["Co-authored-by: Jane Doe <jane@example.com>"], None),
    ],
)
def test_detect_agent(meta_kw, msgs, expected) -> None:
    assert detect_agent(_meta("a", "b", **meta_kw), msgs) == expected
