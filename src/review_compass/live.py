"""Live run: one pull-request event, end to end."""

import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from review_compass.brief import BriefClient, build_brief
from review_compass.classify import classify
from review_compass.features import extract_features
from review_compass.github import GitHubClient, GitHubError
from review_compass.gitrepo import GitError, GitRepo
from review_compass.imports import SUPPORTED_EXTENSIONS
from review_compass.models import Brief, FileKind, Issue, PRFeatures, PRMeta, RiskResult
from review_compass.policy import POLICY_FILE, Policy, PolicyError, load_policy
from review_compass.render import _safe_text, render_comment
from review_compass.scoring import score

FETCH_DEPTH_HINT = "If this is GitHub Actions, set fetch-depth: 0 on actions/checkout."
BRIEF_DIFF_FILES = 5
DISMISS_LOW_RISK_MESSAGE = "Review Compass: no longer low risk."
DISMISS_UNFINISHED_MESSAGE = (
    "Review Compass: could not complete this run; earlier approval withdrawn."
)
# Auto-approval is never given when a changed file is one of these code-like documents.
CODE_LIKE_EXTENSIONS = frozenset(SUPPORTED_EXTENSIONS) | {
    *(".sh", ".bash", ".ps1", ".rb", ".go", ".rs", ".java", ".kt", ".php"),
    *(".c", ".cc", ".cpp", ".h"),
}
_CODEOWNERS_PATHS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")
_MISSING_COMMIT_RE = re.compile(
    r"bad object|bad revision|unknown revision|not a valid (?:object name|commit)"
    r"|invalid revision|no merge base|unable to read tree",
    re.IGNORECASE,
)
HEAD_CHANGED_NOTE = "\n\n> Note: pull request head changed during the run; approval skipped."


class LiveResult(BaseModel):
    exit_code: int
    summary: str
    result: RiskResult | None = None


class NotPullRequestEvent(ValueError):
    pass


def _mapping(value: Any) -> Mapping[str, Any]:
    """A missing (None) section is empty; any other non-mapping makes the event malformed."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("expected a mapping")
    return value


def meta_from_event(event: Mapping[str, Any]) -> PRMeta:
    if not isinstance(event, Mapping):
        raise ValueError("payload is not a mapping")
    if "pull_request" not in event:
        raise NotPullRequestEvent("Not a pull_request event")
    pr = event["pull_request"]
    if not isinstance(pr, Mapping):
        raise ValueError("pull_request is not a mapping")
    user = _mapping(pr.get("user"))
    head = _mapping(pr.get("head"))
    base = _mapping(pr.get("base"))
    head_repo = _mapping(head.get("repo")).get("full_name")
    base_repo = _mapping(base.get("repo")).get("full_name")
    return PRMeta(
        number=int(pr["number"]),
        title=pr.get("title") or "",
        body=pr.get("body") or "",
        author=user.get("login") or "",
        author_is_bot=user.get("type") == "Bot",
        head_ref=head.get("ref") or "",
        base_sha=str(base["sha"]),
        head_sha=str(head["sha"]),
        draft=bool(pr.get("draft")),
        # A deleted fork has no head repo: treat it as a fork.
        is_fork=head_repo is None or head_repo != base_repo,
    )


def analyze(
    repo: GitRepo,
    meta: PRMeta,
    policy: Policy,
    issues: list[Issue],
    brief_client: BriefClient | None,
) -> tuple[PRFeatures, RiskResult, Brief]:
    features = extract_features(repo, meta, policy)
    result = score(features, policy)
    paths = [f.path for f in result.files if not f.skim][:BRIEF_DIFF_FILES]
    diff = repo.diff_text(repo.merge_base(meta.base_sha, meta.head_sha), meta.head_sha, paths)
    brief = build_brief(meta, issues, diff, result, policy, brief_client)
    return features, result, brief


def _error_result(summary: str) -> LiveResult:
    return LiveResult(exit_code=2, summary=summary, result=None)


def _git_error_summary(error: GitError) -> str:
    text = str(error)
    summary = _safe_text(text)
    if _MISSING_COMMIT_RE.search(text) and "fetch-depth: 0" not in text:
        summary += f"\n{FETCH_DEPTH_HINT}"
    return summary


def _blocks_auto_approval(features: PRFeatures) -> bool:
    """Changes whose kind or location the score cannot be trusted to judge never auto-approve."""
    if features.guard_touched or any(f.sensitive for f in features.files):
        return True
    for f in features.files:
        paths = [f.change.path] + ([f.change.old_path] if f.change.old_path else [])
        for path in paths:
            kind = f.kind if path == f.change.path else classify(path)
            if kind in (FileKind.LOCKFILE, FileKind.GENERATED):
                return True
            if kind is FileKind.DOC and path.lower().endswith(tuple(CODE_LIKE_EXTENSIONS)):
                return True
            if path.startswith(".github/") or path in _CODEOWNERS_PATHS:
                return True
    return False


def _withdraw_approvals(gh: GitHubClient, meta: PRMeta, message: str) -> Exception | None:
    """Best effort: dismiss own approvals. Never raises; the error is returned and shown."""
    try:
        gh.dismiss_own_approvals(meta.number, message)
    except Exception as error:
        print(
            f"review-compass: could not dismiss earlier approvals ({type(error).__name__})",
            file=sys.stderr,
        )
        return error
    return None


def run_pr(
    event: Mapping[str, Any],
    repo_dir: Path,
    gh: GitHubClient | None,
    brief_client: BriefClient | None,
) -> LiveResult:
    try:
        meta = meta_from_event(event)
    except NotPullRequestEvent as error:
        return _error_result(str(error))
    except (KeyError, TypeError, ValueError, AttributeError):
        return _error_result("Malformed pull_request event")

    # Fail closed: every exit that does not end in a fresh approval withdraws earlier ones.
    # Fork pull requests are never approved by this tool and the token cannot write there.
    can_dismiss = gh is not None and not meta.is_fork
    try:
        outcome, approved, message = _run_checked(meta, repo_dir, gh, brief_client)
    except Exception:
        if can_dismiss and gh is not None:
            _withdraw_approvals(gh, meta, DISMISS_UNFINISHED_MESSAGE)
        raise
    if approved or not can_dismiss or gh is None:
        return outcome
    failure = _withdraw_approvals(gh, meta, message)
    if failure is None:
        return outcome
    detail = (
        f"HTTP {failure.status}"
        if isinstance(failure, GitHubError) and failure.status
        else type(failure).__name__
    )
    warning = (
        f"\n\n> Warning: could not withdraw earlier approvals ({detail}). "
        "Check workflow permissions."
    )
    return LiveResult(
        exit_code=outcome.exit_code or 2,
        summary=outcome.summary + warning,
        result=outcome.result,
    )


def _run_checked(
    meta: PRMeta,
    repo_dir: Path,
    gh: GitHubClient | None,
    brief_client: BriefClient | None,
) -> tuple[LiveResult, bool, str]:
    """One run. Returns (outcome, approved, dismissal message when not approved)."""
    unfinished = DISMISS_UNFINISHED_MESSAGE
    repo = GitRepo(repo_dir)
    try:
        # Policy always comes from the base SHA, never from the head.
        try:
            policy = load_policy(repo.show(meta.base_sha, POLICY_FILE))
        except PolicyError as error:
            summary = f"Invalid .review-compass.yml on base branch: {_safe_text(str(error))}"
            return _error_result(summary), False, unfinished
        if meta.draft and policy.skip_drafts:
            outcome = LiveResult(exit_code=0, summary="Skipped: draft pull request", result=None)
            return outcome, False, unfinished

        issues: list[Issue] = []
        if gh is not None:
            try:
                issues = gh.linked_issues(meta.body)
            except GitHubError:
                issues = []
        features, result, brief = analyze(repo, meta, policy, issues, brief_client)
    except GitError as error:
        return _error_result(_git_error_summary(error)), False, unfinished

    summary = render_comment(result, brief)

    if gh is None:
        return LiveResult(exit_code=0, summary=summary, result=result), False, unfinished
    if meta.is_fork:
        summary += "\n\n_Fork pull request: comment and label skipped._"
        return LiveResult(exit_code=0, summary=summary, result=result), False, unfinished

    eligible = (
        policy.auto_approve
        and result.bucket == "low"
        and not _blocks_auto_approval(features)
        and not meta.draft
    )
    first_error: GitHubError | None = None
    try:
        gh.upsert_comment(meta.number, summary)
    except GitHubError as error:
        first_error = error
    try:
        gh.set_bucket_label(meta.number, result.bucket)
    except GitHubError as error:
        first_error = first_error or error

    # Never approve unless every earlier write succeeded.
    approved = False
    try:
        if eligible and first_error is None:
            # A newer push may have superseded this run: the run for the new head decides.
            try:
                current_head: str | None = gh.head_sha(meta.number)
            except GitHubError as error:
                current_head = None
                first_error = error
            if current_head == meta.head_sha:
                gh.approve(
                    meta.number,
                    meta.head_sha,
                    f"Review Compass: low risk ({result.score}/100), auto-approved by policy.",
                )
                approved = True
            else:
                summary += HEAD_CHANGED_NOTE
    except GitHubError as error:
        first_error = first_error or error

    if first_error is not None:
        detail = f"HTTP {first_error.status}" if first_error.status else str(first_error)
        summary += (
            f"\n\n> Warning: could not write to the pull request ({detail}). "
            "Check workflow permissions."
        )
    message = DISMISS_LOW_RISK_MESSAGE if not eligible else unfinished
    return LiveResult(exit_code=0, summary=summary, result=result), approved, message
