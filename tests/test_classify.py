import pytest

from review_compass.classify import classify
from review_compass.models import FileKind


@pytest.mark.parametrize(
    "path,kind",
    [
        ("uv.lock", FileKind.LOCKFILE),
        ("web/package-lock.json", FileKind.LOCKFILE),
        ("web/dist/app.js", FileKind.GENERATED),
        ("static/app.min.js", FileKind.GENERATED),
        ("api/user_pb2.py", FileKind.GENERATED),
        ("tests/test_auth.py", FileKind.TEST),
        ("src/auth_test.go", FileKind.TEST),
        ("web/src/Button.test.tsx", FileKind.TEST),
        ("tests/data/sample.json", FileKind.TEST),
        ("README.md", FileKind.DOC),
        ("docs/conf.py", FileKind.DOC),
        ("notes.txt", FileKind.DOC),
        ("requirements-dev.txt", FileKind.CONFIG),
        ("pyproject.toml", FileKind.CONFIG),
        (".github/workflows/ci.yml", FileKind.CONFIG),
        ("Dockerfile.prod", FileKind.CONFIG),
        ("src/auth/session.py", FileKind.SOURCE),
        ("web/src/Button.tsx", FileKind.SOURCE),
        ("Makefile", FileKind.SOURCE),
        (".gitignore", FileKind.CONFIG),
        ("web/.eslintrc.json", FileKind.CONFIG),
        (".editorconfig", FileKind.CONFIG),
        ("logo.png", FileKind.GENERATED),
        ("docs/Logo.PNG", FileKind.GENERATED),
        ("assets/font.woff2", FileKind.GENERATED),
        ("tests/fixtures/img.png", FileKind.GENERATED),
        ("icon.svg", FileKind.SOURCE),
    ],
)
def test_classify(path: str, kind: FileKind) -> None:
    assert classify(path) is kind
