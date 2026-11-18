"""Classify changed files by kind (first matching rule wins)."""

from fnmatch import fnmatchcase
from pathlib import PurePosixPath

from review_compass.models import FileKind

_LOCKFILES = frozenset(
    {
        "uv.lock",
        "poetry.lock",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "Cargo.lock",
        "go.sum",
        "Gemfile.lock",
        "composer.lock",
    }
)
_GENERATED_DIRS = frozenset({"dist", "build", "vendor", "node_modules"})
_GENERATED_NAMES = ("*.min.js", "*.min.css", "*_pb2.py", "*.generated.*", "*.snap")
_BINARY_EXTENSIONS = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".pdf",
        ".woff", ".woff2", ".ttf", ".otf", ".eot",
        ".mp3", ".mp4", ".mov", ".zip", ".gz", ".tar", ".jar", ".bin",
    }
)  # fmt: skip
_TEST_DIRS = frozenset({"tests", "test", "__tests__", "spec"})
_TEST_NAMES = (
    "test_*.py",
    "*_test.py",
    "conftest.py",
    "*_test.go",
    "*.test.js",
    "*.test.jsx",
    "*.test.ts",
    "*.test.tsx",
    "*.spec.js",
    "*.spec.jsx",
    "*.spec.ts",
    "*.spec.tsx",
)
_DOC_NAMES = ("*.md", "*.rst", "LICENSE*", "CHANGELOG*")
_CONFIG_NAMES = (
    "*.yml",
    "*.yaml",
    "*.toml",
    "*.json",
    "*.ini",
    "*.cfg",
    ".env*",
    "Dockerfile*",
    "requirements*.txt",
    ".eslintrc*",
    ".prettierrc.*",
    ".babelrc*",
    ".stylelintrc*",
)
_CONFIG_BASENAMES = frozenset(
    {
        ".gitignore",
        ".gitattributes",
        ".gitmodules",
        ".editorconfig",
        ".dockerignore",
        ".npmrc",
        ".nvmrc",
        ".prettierrc",
        ".prettierignore",
        ".eslintignore",
        ".python-version",
        ".tool-versions",
        ".flake8",
        ".coveragerc",
        ".browserslistrc",
        ".gitkeep",
        ".pylintrc",
        ".yamllint",
        ".markdownlint.json",
        ".env.example",
        "CODEOWNERS",
    }
)


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatchcase(name, p) for p in patterns)


def classify(path: str) -> FileKind:
    parts = PurePosixPath(path).parts
    name = parts[-1]
    dirs = set(parts[:-1])

    if name in _LOCKFILES:
        return FileKind.LOCKFILE
    if (
        dirs & _GENERATED_DIRS
        or _matches(name, _GENERATED_NAMES)
        or PurePosixPath(name).suffix.lower() in _BINARY_EXTENSIONS
    ):
        return FileKind.GENERATED
    if dirs & _TEST_DIRS or _matches(name, _TEST_NAMES):
        return FileKind.TEST
    if (
        parts[0] == "docs"
        and len(parts) > 1
        or _matches(name, _DOC_NAMES)
        or (fnmatchcase(name, "*.txt") and not fnmatchcase(name, "requirements*.txt"))
    ):
        return FileKind.DOC
    if name in _CONFIG_BASENAMES or _matches(name, _CONFIG_NAMES):
        return FileKind.CONFIG
    return FileKind.SOURCE
