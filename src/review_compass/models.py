"""Shared data models for Review Compass."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class FileKind(StrEnum):
    SOURCE = "source"
    TEST = "test"
    DOC = "doc"
    CONFIG = "config"
    GENERATED = "generated"
    LOCKFILE = "lockfile"


Bucket = Literal["low", "medium", "high"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class FileChange(_Frozen):
    path: str
    old_path: str | None
    status: Literal["added", "modified", "deleted", "renamed"]
    additions: int
    deletions: int
    binary: bool
    hunks: list[tuple[int, int]]  # new-file (start, end)


class PRMeta(_Frozen):
    number: int
    title: str
    body: str
    author: str
    author_is_bot: bool
    head_ref: str
    base_sha: str
    head_sha: str
    draft: bool
    is_fork: bool


class FileFeatures(_Frozen):
    change: FileChange
    kind: FileKind
    sensitive: bool
    guard: bool
    dependents: int | None
    churn_90d: int
    existed_at_base: bool
    author_touched_before: bool


class PRFeatures(_Frozen):
    files: list[FileFeatures]
    effective_lines: int
    source_changed: bool
    tests_changed: bool
    dependents_total: int | None
    max_churn: int
    unfamiliar_share: float
    directories: int
    agent_authored: bool
    agent_evidence: str | None
    guard_touched: bool
    docs_only: bool


class Contribution(_Frozen):
    signal: str
    points: int
    reason: str


class FileRank(_Frozen):
    path: str
    score: int
    reasons: list[str]
    skim: bool
    span: tuple[int, int] | None


class RiskResult(_Frozen):
    score: int
    bucket: Bucket
    contributions: list[Contribution]
    files: list[FileRank]
    guard_touched: bool
    notes: list[str]


class Issue(_Frozen):
    number: int
    title: str
    body: str


class IntentCheck(_Frozen):
    status: Literal["matches", "extends", "unclear", "no_issue", "not_run"]
    note: str


class ReadingNote(_Frozen):
    path: str
    why: str


class Brief(_Frozen):
    intent: IntentCheck
    reading_notes: list[ReadingNote]
