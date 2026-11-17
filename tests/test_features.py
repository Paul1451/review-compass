import pytest

from review_compass.features import detect_agent, extract_features
from review_compass.models import FileKind, PRMeta
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
        (
            {},
            ["Co-authored-by: Copilot <175728472+Copilot@users.noreply.github.com>"],
            "Co-Authored-By: Copilot",
        ),
        (
            {},
            ["Co-authored-by: Cursor Agent <cursoragent@cursor.com>"],
            "Co-Authored-By: Cursor Agent",
        ),
        (
            {"author": "devin-ai-integration[bot]", "author_is_bot": True},
            [],
            "bot author: devin-ai-integration[bot]",
        ),
        ({}, ["Co-authored-by: Claude Dupont <claude.dupont@gmail.com>"], None),
        ({}, ["Co-authored-by: Devin Smith <1234+devsmith@users.noreply.github.com>"], None),
        ({}, ["Co-authored-by: Julesz <julesz@example.com>"], None),
        ({"author": "codexter[bot]", "author_is_bot": True}, [], None),
    ],
)
def test_detect_agent(meta_kw, msgs, expected) -> None:
    assert detect_agent(_meta("a", "b", **meta_kw), msgs) == expected


def test_rename_out_of_guard_and_sensitive_paths(make_repo) -> None:
    b = make_repo()
    base = b.commit(
        {
            ".github/workflows/ci.yml": "on: push\njobs: {}\nname: ci\nx: 1\ny: 2\n",
            "src/auth/session.py": "a\nb\nc\nd\ne\n",
        }
    )
    head = b.commit(
        {
            ".github/workflows/ci.yml": None,
            "docs/ci.yml": "on: push\njobs: {}\nname: ci\nx: 1\ny: 2\n",
            "src/auth/session.py": None,
            "src/core/session.py": "a\nb\nc\nd\ne\n",
        }
    )
    f = extract_features(b.repo, _meta(base, head), load_policy(None))
    by = {x.change.path: x for x in f.files}
    assert f.guard_touched is True
    assert by["docs/ci.yml"].guard and by["docs/ci.yml"].sensitive
    assert by["src/core/session.py"].sensitive


def test_dependents_do_not_depend_on_unrelated_files(make_repo) -> None:
    b = make_repo()
    base = b.commit({"README.md": "a\n"})
    head = b.commit({"src/new.py": "x = 1\n"})
    f = extract_features(b.repo, _meta(base, head), load_policy(None))
    assert f.files[0].dependents == 0 and f.dependents_total == 0

    b2 = make_repo()
    base2 = b2.commit({"main.go": "package main\n"})
    head2 = b2.commit({"main.go": "package main\n// x\n"})
    f2 = extract_features(b2.repo, _meta(base2, head2), load_policy(None))
    assert f2.files[0].dependents is None and f2.dependents_total is None

    b3 = make_repo()
    base3 = b3.commit({"src/a.py": "x = 1\n", "README.md": "a\n"})
    head3 = b3.commit({"src/a.py": "x = 2\n", "README.md": "b\n"})
    by = {
        x.change.path: x
        for x in extract_features(b3.repo, _meta(base3, head3), load_policy(None)).files
    }
    assert by["README.md"].dependents is None and by["src/a.py"].dependents == 0


def test_renamed_file_history_and_dependent_exclusion(make_repo) -> None:
    b = make_repo()
    ann = "Ann <ann@example.com>"
    body = "a\nb\nc\nd\ne\nf\n"
    b.commit(
        {"src/lib.py": body, "src/user.py": "import lib\n", "src/other.py": "import lib\n"},
        author=ann,
    )
    b.commit({"src/lib.py": body + "g\n"}, author=ann)
    base = b.commit({"src/lib.py": body}, author=ann)
    b.branch("feature/x")
    b.checkout("feature/x")
    head = b.commit(
        {"src/lib.py": None, "src/lib2.py": body + "z\n", "src/other.py": "import lib2\n"},
        author="Ann <ann@example.com>",
    )
    f = extract_features(b.repo, _meta(base, head), load_policy(None))
    by = {x.change.path: x for x in f.files}
    lib = by["src/lib2.py"]
    assert lib.change.old_path == "src/lib.py"
    assert lib.churn_90d == 3 and lib.existed_at_base is True
    assert lib.author_touched_before is True
    assert lib.dependents == 1


def test_rename_of_source_into_docs_keeps_source_kind(make_repo) -> None:
    b = make_repo()
    body = "a\nb\nc\nd\ne\nf\n"
    base = b.commit({"src/core.py": body, "src/api.py": "import core\n"})
    head = b.commit({"src/core.py": None, "docs/core.md": body})
    f = extract_features(b.repo, _meta(base, head), load_policy(None))
    core = f.files[0]
    assert core.kind is FileKind.SOURCE and core.dependents == 1
    assert not f.docs_only
