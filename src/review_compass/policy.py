"""Policy file (.review-compass.yml): schema, built-in path lists, loading."""

import hashlib
import json
from functools import cache
from typing import Self

import pathspec
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

POLICY_FILE = ".review-compass.yml"
GUARD_PATHS = [".review-compass.yml", ".github/workflows/**"]
DEFAULT_SENSITIVE_PATHS = [
    "**/auth/**",
    "**/*auth*",
    "**/security/**",
    "**/crypto/**",
    "**/payments/**",
    "**/billing/**",
    "**/migrations/**",
    "**/*migration*",
    "**/Dockerfile*",
    "**/*.tf",
    "**/k8s/**",
    "**/helm/**",
    "**/pyproject.toml",
    "**/requirements*.txt",
    "**/setup.py",
    "**/setup.cfg",
    "**/package.json",
    "**/go.mod",
    "**/Cargo.toml",
]


class PolicyError(ValueError):
    """Raised when the policy file cannot be parsed or fails validation."""


class _PolicyModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Thresholds(_PolicyModel):
    medium: int = 35
    high: int = 60

    @model_validator(mode="after")
    def _check_order(self) -> Self:
        if not 0 < self.medium < self.high <= 100:
            raise ValueError(
                f"thresholds must satisfy 0 < medium < high <= 100, "
                f"got medium={self.medium}, high={self.high}"
            )
        return self


class Weights(_PolicyModel):
    sensitive_path: int = Field(default=25, ge=0)
    no_tests: int = Field(default=18, ge=0)
    dependents: int = Field(default=15, ge=0)
    size: int = Field(default=15, ge=0)
    hotspot: int = Field(default=9, ge=0)
    unfamiliar: int = Field(default=8, ge=0)
    spread: int = Field(default=5, ge=0)
    agent: int = Field(default=5, ge=0)


class LLMPolicy(_PolicyModel):
    enabled: bool = True
    model: str = "claude-haiku-5-5"
    max_input_tokens: int = Field(default=30000, ge=1000)


@cache
def _spec(patterns: tuple[str, ...]) -> pathspec.GitIgnoreSpec:
    return pathspec.GitIgnoreSpec.from_lines(patterns)


class Policy(_PolicyModel):
    sensitive_paths: list[str] = []  # user additions; built-ins always apply
    thresholds: Thresholds = Thresholds()
    weights: Weights = Weights()
    skip_drafts: bool = True
    auto_approve: bool = False
    llm: LLMPolicy = LLMPolicy()

    def is_guard(self, path: str) -> bool:
        return _spec(tuple(GUARD_PATHS)).match_file(path)

    def is_sensitive(self, path: str) -> bool:
        patterns = (*DEFAULT_SENSITIVE_PATHS, *GUARD_PATHS, *self.sensitive_paths)
        return _spec(patterns).match_file(path)

    def fingerprint(self) -> str:
        canonical = {
            "policy": self.model_dump(mode="json"),
            "default_sensitive_paths": DEFAULT_SENSITIVE_PATHS,
            "guard_paths": GUARD_PATHS,
        }
        blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:12]


def load_policy(text: str | None) -> Policy:
    """Parse policy YAML. None, empty or comment-only text yields the defaults."""
    if text is None:
        return Policy()
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PolicyError(f"{POLICY_FILE}: invalid YAML: {exc}") from exc
    if data is None:
        return Policy()
    if not isinstance(data, dict):
        raise PolicyError(f"{POLICY_FILE}: top level must be a mapping, got {type(data).__name__}")
    try:
        return Policy.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        )
        raise PolicyError(f"{POLICY_FILE}: {details}") from exc
