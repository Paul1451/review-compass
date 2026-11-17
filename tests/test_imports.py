import json
import warnings

from review_compass.imports import ImportIndex


def test_python_absolute_relative_and_src_layout(make_repo) -> None:
    b = make_repo()
    rev = b.commit(
        {
            "src/app/core.py": "X = 1\n",
            "src/app/api.py": "from app.core import X\n",
            "src/app/sub/deep.py": "from ..core import X\n",
            "src/app/other.py": "import app.core\n",
            "src/app/unrelated.py": "import os\n",
        }
    )
    deps = ImportIndex(b.repo).dependents(rev, ["src/app/core.py"])
    assert deps == {
        "src/app/core.py": {"src/app/api.py", "src/app/sub/deep.py", "src/app/other.py"}
    }


def test_js_ts_relative_and_index(make_repo) -> None:
    b = make_repo()
    rev = b.commit(
        {
            "web/lib/util.ts": "export const u = 1\n",
            "web/lib/index.ts": "export * from './util'\n",
            "web/app.tsx": "import { u } from './lib/util'\nimport x from './lib'\n",
            "web/old.js": "const u = require('./lib/util.ts')\n",
            "web/ext.ts": "import React from 'react'\n",
        }
    )
    deps = ImportIndex(b.repo).dependents(rev, ["web/lib/util.ts", "web/lib/index.ts"])
    assert deps == {
        "web/lib/util.ts": {"web/lib/index.ts", "web/app.tsx", "web/old.js"},
        "web/lib/index.ts": {"web/app.tsx"},
    }


def test_unsupported_language_returns_none(make_repo) -> None:
    b = make_repo()
    rev = b.commit({"main.go": "package main\n", "README.md": "x\n"})
    assert ImportIndex(b.repo).dependents(rev, ["main.go", "README.md"]) is None


def test_unparseable_files_are_skipped(make_repo) -> None:  # Review Focus 3
    b = make_repo()
    rev = b.commit(
        {
            "a.py": "X = 1\n",
            "broken.py": "def (:\n",
            "bin.py": b"\x00\xff\x00",
            "ok.py": "import a\n",
        }
    )
    assert ImportIndex(b.repo).dependents(rev, ["a.py"]) == {"a.py": {"ok.py"}}


def test_blob_cache_survives_save_and_reload(make_repo, tmp_path) -> None:
    b = make_repo()
    rev = b.commit({"a.py": "X=1\n", "b.py": "import a\n"})
    cache = tmp_path / "blobs.json"
    idx = ImportIndex(b.repo, cache)
    idx.dependents(rev, ["a.py"])
    idx.save()
    assert cache.exists()
    assert ImportIndex(b.repo, cache).dependents(rev, ["a.py"]) == {"a.py": {"b.py"}}


def test_cached_blobs_are_not_read_again(make_repo, monkeypatch) -> None:
    b = make_repo()
    rev = b.commit({"a.py": "X=1\n", "b.py": "import a\n"})
    idx = ImportIndex(b.repo)
    idx.dependents(rev, ["a.py"])

    def boom(shas):  # type: ignore[no-untyped-def]
        raise AssertionError("blobs should come from the cache")

    monkeypatch.setattr(b.repo, "read_blobs", boom)
    assert idx.dependents(rev, ["a.py"]) == {"a.py": {"b.py"}}


def test_corrupt_cache_file_is_treated_as_empty(make_repo, tmp_path) -> None:
    b = make_repo()
    rev = b.commit({"a.py": "X=1\n", "b.py": "import a\n"})
    cache = tmp_path / "blobs.json"
    for content in ("{not json", json.dumps(["wrong", "shape"])):
        cache.write_text(content)
        assert ImportIndex(b.repo, cache).dependents(rev, ["a.py"]) == {"a.py": {"b.py"}}
    missing = tmp_path / "nope" / "blobs.json"
    assert ImportIndex(b.repo, missing).dependents(rev, ["a.py"]) == {"a.py": {"b.py"}}


def test_syntax_warnings_from_source_are_suppressed(make_repo) -> None:
    b = make_repo()
    rev = b.commit({"a.py": "X=1\n", "w.py": 'import a\nS = "\\d"\n'})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert ImportIndex(b.repo).dependents(rev, ["a.py"]) == {"a.py": {"w.py"}}


def test_self_import_and_package_relative(make_repo) -> None:
    b = make_repo()
    rev = b.commit(
        {
            "pkg/__init__.py": "",
            "pkg/a.py": "import pkg.a\nfrom . import b\n",
            "pkg/b.py": "X = 1\n",
        }
    )
    deps = ImportIndex(b.repo).dependents(rev, ["pkg/a.py", "pkg/b.py"])
    assert deps == {"pkg/a.py": set(), "pkg/b.py": {"pkg/a.py"}}


def test_missing_blob_is_not_cached(make_repo, monkeypatch, tmp_path) -> None:
    b = make_repo()
    rev = b.commit({"a.py": "X=1\n", "b.py": "import a\n"})
    missing_sha = b.repo.ls_tree(rev)["b.py"]
    real_read_blobs = b.repo.read_blobs

    def read_without_b(shas):  # type: ignore[no-untyped-def]
        return {s: d for s, d in real_read_blobs(shas).items() if s != missing_sha}

    cache = tmp_path / "blobs.json"
    idx = ImportIndex(b.repo, cache)
    monkeypatch.setattr(b.repo, "read_blobs", read_without_b)
    assert idx.dependents(rev, ["a.py"]) == {"a.py": set()}
    idx.save()
    assert not any(missing_sha in key for key in json.loads(cache.read_text()))

    monkeypatch.setattr(b.repo, "read_blobs", real_read_blobs)
    assert idx.dependents(rev, ["a.py"]) == {"a.py": {"b.py"}}
