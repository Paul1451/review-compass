"""Live run: one pull-request event, end to end."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from review_compass.brief import BriefClient, build_brief
from review_compass.features import extract_features
from review_compass.github import GitHubClient, GitHubError
from review_compass.gitrepo import GitError, GitRepo
from review_compass.models import Brief, Issue, PRFeatures, PRMeta, RiskResult
from review_compass.policy import POLICY_FILE, Policy, PolicyError, load_policy
from review_compass.render import render_comment
from review_compass.scoring import score

FETCH_DEPTH_HINT = "If this is GitHub Actions, set fetch-depth: 0 on actions/checkout."
BRIEF_DIFF_FILES = 5
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
    if "fetch-depth: 0" not in text:
        text += f"\n{FETCH_DEPTH_HINT}"
    return text


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

    repo = GitRepo(repo_dir)
    try:
        # Policy always comes from the base SHA, never from the head.
        try:
            policy = load_policy(repo.show(meta.base_sha, POLICY_FILE))
        except PolicyError as error:
            return _error_result(f"Invalid .review-compass.yml on base branch: {error}")
        if meta.draft and policy.skip_drafts:
            return LiveResult(exit_code=0, summary="Skipped: draft pull request", result=None)

        issues: list[Issue] = []
        if gh is not None:
            try:
                issues = gh.linked_issues(meta.body)
            except GitHubError:
                issues = []
        features, result, brief = analyze(repo, meta, policy, issues, brief_client)
    except GitError as error:
        return _error_result(_git_error_summary(error))

    summary = render_comment(result, brief)

    if gh is None:
        return LiveResult(exit_code=0, summary=summary, result=result)
    if meta.is_fork:
        summary += "\n\n_Fork pull request: comment and label skipped._"
        return LiveResult(exit_code=0, summary=summary, result=result)

    eligible = (
        policy.auto_approve
        and result.bucket == "low"
        and not result.guard_touched
        and not any(f.sensitive for f in features.files)
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

    # Never approve unless every earlier write succeeded; dismissing is always the safe half.
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
            else:
                summary += HEAD_CHANGED_NOTE
        elif not eligible:
            gh.dismiss_own_approvals(meta.number, "Review Compass: no longer low risk.")
    except GitHubError as error:
        first_error = first_error or error

    if first_error is not None:
        detail = f"HTTP {first_error.status}" if first_error.status else str(first_error)
        summary += (
            f"\n\n> Warning: could not write to the pull request ({detail}). "
            "Check workflow permissions."
        )
    return LiveResult(exit_code=0, summary=summary, result=result)
