import subprocess
from datetime import UTC, datetime

import pytest

from review_compass.gitrepo import GitError, GitRepo


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


def test_changed_files_paths_with_space_and_non_ascii(make_repo) -> None:
    b = make_repo()
    base = b.commit({"src/a b.py": "1\n2\n", "src/ünï.py": "x\n"})
    head = b.commit({"src/a b.py": "1\n2\n3\n", "src/ünï.py": "x\ny\n"})
    by = {c.path: c for c in b.repo.changed_files(base, head)}
    for path in ("src/a b.py", "src/ünï.py"):
        assert (by[path].additions, by[path].deletions) == (1, 0)
    assert by["src/a b.py"].hunks == [(3, 3)]
    assert by["src/ünï.py"].hunks == [(2, 2)]


def test_show_unresolvable_rev_raises(make_repo) -> None:
    b = make_repo()
    sha = b.commit({"a.py": "1\n"})
    assert b.repo.show(sha, "a.py") == "1\n"
    assert b.repo.show(sha, "nope.py") is None
    with pytest.raises(GitError):
        b.repo.show("0" * 40, "a.py")


def test_hunks_ignore_plus_plus_plus_content_and_diff_prefix_config(make_repo) -> None:
    b = make_repo()
    b._git("config", "diff.noprefix", "true")
    base = b.commit({"a.py": "1\n2\n", "b.py": "x\n"})
    head = b.commit({"a.py": "1\n2\n++ not a header\n", "b.py": "x\ny\n"})
    by = {c.path: c for c in b.repo.changed_files(base, head)}
    assert by["a.py"].hunks == [(3, 3)] and by["a.py"].additions == 1
    assert by["b.py"].hunks == [(2, 2)] and by["b.py"].additions == 1
    assert set(by) == {"a.py", "b.py"}


def test_ref_exists(make_repo) -> None:
    b = make_repo()
    sha = b.commit({"a.py": "1\n"})
    b._git("update-ref", "refs/pull/7/head", sha)
    assert b.repo.ref_exists("refs/pull/7/head")
    assert not b.repo.ref_exists("refs/pull/8/head")


def test_fetch_default_branch_picks_up_new_commits(make_repo, tmp_path) -> None:
    origin = make_repo()
    origin.commit({"a.py": "1\n"})
    clone = tmp_path / "clone.git"
    subprocess.run(["git", "clone", "-q", "--bare", "--", str(origin.path), str(clone)], check=True)
    new = origin.commit({"a.py": "2\n"})
    GitRepo(clone).fetch_default_branch()
    assert GitRepo(clone).ref_exists(new)


def test_log_between_timed_returns_commit_times(make_repo) -> None:
    b = make_repo()
    b.commit({"a": "1"}, message="old", date="2024-01-01T10:00:00+00:00")
    mid = b.commit({"a": "2"}, message="mid\n\nbody", date="2024-01-05T12:00:00+00:00")
    b.commit({"a": "3"}, message="new", date="2024-02-01T10:00:00+00:00")
    since = datetime(2024, 1, 2, tzinfo=UTC)
    until = datetime(2024, 1, 10, tzinfo=UTC)
    assert b.repo.log_between_timed("HEAD", since, until) == [
        (mid, datetime(2024, 1, 5, 12, tzinfo=UTC), "mid\n\nbody")
    ]
    assert b.repo.log_between("HEAD", since, until) == [(mid, "mid\n\nbody")]


def test_file_named_like_merge_base_sha_does_not_break_git_calls(make_repo) -> None:
    b = make_repo()
    base = b.commit({"README.md": "a\n"})
    b.branch("feature")
    head = b.commit({f"{base}": "decoy\n", "src/app.py": "x = 1\n"})
    repo = b.repo
    assert repo.merge_base(base, head) == base
    paths = {c.path for c in repo.changed_files(base, head)}
    assert paths == {base, "src/app.py"}
    assert repo.commit_time(base).year >= 2026
    assert repo.commit_messages(base, head) == ["c"]
    assert repo.author_emails(base, head) == {"dev@example.com"}
    assert repo.diff_text(base, head, ["src/app.py"]).startswith("diff --git")
    assert repo.show(base, "README.md") == "a\n"
    assert repo.ls_tree(base) == repo.ls_tree(base)
    assert repo.churn(head, ["src/app.py"]) == {"src/app.py": 1}
    assert repo.touched_before(head, {"dev@example.com"}, ["src/app.py"]) == {"src/app.py": True}
    since = datetime(2000, 1, 1, tzinfo=UTC)
    until = datetime(2100, 1, 1, tzinfo=UTC)
    assert len(repo.log_between(base, since, until)) == 1
    assert repo.ref_exists(base)


def test_directory_named_like_base_branch_does_not_break_git_calls(make_repo) -> None:
    b = make_repo()
    base = b.commit({"main/readme.txt": "a\n"})
    b.branch("feature")
    head = b.commit({"src/app.py": "x = 1\n"})
    repo = b.repo
    assert repo.merge_base("main", "HEAD") == base
    assert [c.path for c in repo.changed_files("main", "HEAD")] == ["src/app.py"]
    assert repo.commit_time("main").year >= 2026
    assert repo.commit_messages("main", "HEAD") == ["c"]
    assert repo.author_emails("main", "HEAD") == {"dev@example.com"}
    assert repo.diff_text("main", "HEAD", ["src/app.py"]).startswith("diff --git")
    assert head


def test_churn_and_touched_before_with_many_paths(make_repo) -> None:
    b = make_repo()
    files: dict[str, str | bytes | None] = {f"d/f{i}.py": "x\n" for i in range(700)}
    b.commit(files, author="Ann <ann@example.com>")
    rev = b.commit({"d/f0.py": "y\n", "d/f699.py": "y\n"}, author="Bob <bob@example.com>")
    paths = list(files)
    churn = b.repo.churn(rev, paths)
    assert len(churn) == 700
    assert churn["d/f0.py"] == 2 and churn["d/f699.py"] == 2 and churn["d/f5.py"] == 1
    touched = b.repo.touched_before(rev, {"bob@example.com"}, paths)
    assert touched["d/f0.py"] and touched["d/f699.py"] and not touched["d/f5.py"]



def test_path_batching_gives_identical_results(make_repo, monkeypatch) -> None:
    from review_compass import gitrepo

    b = make_repo()
    files: dict[str, str | bytes | None] = {f"d/f{i}.py": "x\n" for i in range(12)}
    b.commit(files, author="Ann <ann@example.com>")
    rev = b.commit({"d/f0.py": "y\n", "d/f11.py": "y\n"}, author="Bob <bob@example.com>")
    paths = list(files)
    whole = (
        b.repo.churn(rev, paths),
        b.repo.touched_before(rev, {"bob@example.com"}, paths),
    )
    monkeypatch.setattr(gitrepo, "PATH_CHUNK", 5)
    assert (
        b.repo.churn(rev, paths),
        b.repo.touched_before(rev, {"bob@example.com"}, paths),
    ) == whole
