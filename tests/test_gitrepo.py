import pytest

from review_compass.gitrepo import GitError


def test_changed_files_counts_and_hunks(make_repo) -> None:
    b = make_repo()
    base = b.commit({"a.py": "1\n2\n3\n", "old.py": "x\n", "gone.py": "y\n"})
    head = b.commit(
        {"a.py": "1\n2\nX\nY\n", "new.py": "n\n", "gone.py": None, "img.png": b"\x89PNG\x00\xff"}
    )
    by = {c.path: c for c in b.repo.changed_files(base, head)}
    assert (by["a.py"].additions, by["a.py"].deletions, by["a.py"].hunks) == (2, 1, [(3, 4)])
    assert by["new.py"].status == "added" and by["gone.py"].status == "deleted"
    assert by["img.png"].binary and by["img.png"].additions == 0


def test_pure_rename(make_repo) -> None:  # Review Focus 2
    b = make_repo()
    base = b.commit({"old.py": "a\nb\nc\nd\ne\n"})
    head = b.commit({"old.py": None, "renamed.py": "a\nb\nc\nd\ne\n"})
    (c,) = b.repo.changed_files(base, head)
    got = (c.status, c.old_path, c.path, c.additions, c.deletions)
    assert got == ("renamed", "old.py", "renamed.py", 0, 0)


def test_show_missing_and_non_utf8(make_repo) -> None:  # Review Focus 2
    b = make_repo()
    sha = b.commit({"latin.py": b"x = '\xe9'\n"})
    assert b.repo.show(sha, "nope.py") is None
    assert "x = " in (b.repo.show(sha, "latin.py") or "")


def test_churn_window_is_relative_to_commit(make_repo) -> None:
    b = make_repo()
    b.commit({"a.py": "1"}, date="2026-01-01T00:00:00Z")
    b.commit({"a.py": "2"}, date="2026-05-01T00:00:00Z")
    b.commit({"a.py": "3"}, date="2026-06-01T00:00:00Z")
    rev = b.commit({"b.py": "x"}, date="2026-06-15T00:00:00Z")
    assert b.repo.churn(rev, ["a.py", "b.py"]) == {"a.py": 2, "b.py": 1}


def test_touched_before(make_repo) -> None:
    b = make_repo()
    b.commit({"a.py": "1"}, author="Ann <ann@example.com>")
    rev = b.commit({"b.py": "1"}, author="Bob <bob@example.com>")
    got = b.repo.touched_before(rev, {"ann@example.com"}, ["a.py", "b.py"])
    assert got == {"a.py": True, "b.py": False}


def test_merge_base_missing_names_fetch_depth(make_repo) -> None:  # Review Focus 1
    b = make_repo()
    a = b.commit({"a": "1"})
    with pytest.raises(GitError, match="fetch-depth: 0"):
        b.repo.merge_base(a, "0" * 40)


def test_ls_tree_and_read_blobs(make_repo) -> None:
    b = make_repo()
    sha = b.commit({"pkg/a.py": "import os\n"})
    tree = b.repo.ls_tree(sha)
    assert b.repo.read_blobs([tree["pkg/a.py"]])[tree["pkg/a.py"]] == b"import os\n"
