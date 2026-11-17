import pytest

from review_compass.policy import PolicyError, load_policy


def test_defaults_when_no_file() -> None:
    p = load_policy(None)
    assert (p.thresholds.medium, p.thresholds.high) == (35, 60)
    assert p.auto_approve is False and p.skip_drafts is True
    assert p.weights.model_dump() == {
        "sensitive_path": 25,
        "no_tests": 18,
        "dependents": 15,
        "size": 15,
        "hotspot": 9,
        "unfamiliar": 8,
        "spread": 5,
        "agent": 5,
    }
    assert p.llm.model == "claude-haiku-5-5"


def test_user_paths_extend_builtins() -> None:
    p = load_policy("sensitive_paths: ['legacy/**']")
    assert p.is_sensitive("legacy/x.py") and p.is_sensitive("src/auth/session.py")
    assert not p.is_sensitive("src/util/strings.py")


def test_guard_paths_cannot_be_removed() -> None:
    p = load_policy("sensitive_paths: []")
    for path in (".review-compass.yml", ".github/workflows/ci.yml"):
        assert p.is_guard(path) and p.is_sensitive(path)
    assert not p.is_guard("src/app.py")


@pytest.mark.parametrize(
    "text",
    [
        "thresholds: {medium: 60, high: 35}",
        "thresholds: {medium: 0, high: 60}",
        "weights: {size: -1}",
        "unknown_key: 1",
        "llm: {modell: x}",
        "sensitive_paths: 'x'",
        ": : :",
    ],
)
def test_invalid_policy_raises(text: str) -> None:
    with pytest.raises(PolicyError):
        load_policy(text)


def test_empty_file_is_defaults() -> None:
    assert load_policy("") == load_policy(None)


def test_fingerprint_changes_with_weights() -> None:
    assert load_policy(None).fingerprint() != load_policy("weights: {size: 14}").fingerprint()


def test_user_negation_cannot_cancel_builtin_or_guard() -> None:
    p = load_policy('sensitive_paths: ["!.github/workflows/**", "!**/auth/**"]')
    assert p.is_sensitive(".github/workflows/ci.yml")
    assert p.is_sensitive("src/auth/s.py")
    assert p.is_guard(".github/workflows/ci.yml")
