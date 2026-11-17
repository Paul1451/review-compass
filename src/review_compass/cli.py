"""Command line entry point: `review-compass pr` and `review-compass score`."""

import argparse
import json
import os
import secrets
import subprocess
import sys
import traceback
from collections.abc import Sequence
from pathlib import Path

from review_compass.brief import AnthropicBriefClient, BriefClient
from review_compass.github import GitHubClient
from review_compass.gitrepo import GitError, GitRepo
from review_compass.live import analyze, run_pr
from review_compass.models import PRMeta
from review_compass.policy import POLICY_FILE, PolicyError, load_policy
from review_compass.render import render_comment


def _brief_client() -> BriefClient | None:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    return AnthropicBriefClient(key) if key else None


def _github_client() -> GitHubClient | None:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        return None
    bot_login = os.environ.get("REVIEW_COMPASS_BOT_LOGIN", "")
    if bot_login:
        return GitHubClient(token, repo, bot_login=bot_login)
    return GitHubClient(token, repo)


def _stdout_text(text: str) -> str:
    """In GitHub Actions, stop the runner from parsing `::` lines in the echoed summary."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return text
    token = secrets.token_hex(16)
    while token in text:
        token = secrets.token_hex(16)
    return f"::stop-commands::{token}\n{text}\n::{token}::"


def _cmd_pr(args: argparse.Namespace) -> int:
    event_path = args.event or os.environ.get("GITHUB_EVENT_PATH", "")
    if not event_path:
        print("review-compass: no event file (use --event or GITHUB_EVENT_PATH)", file=sys.stderr)
        return 2
    try:
        event = json.loads(Path(event_path).read_text())
    except (OSError, ValueError):
        print("review-compass: cannot read the event file", file=sys.stderr)
        return 2
    outcome = run_pr(event, Path(args.repo_dir), _github_client(), _brief_client())
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as handle:
            handle.write(outcome.summary + "\n")
    print(_stdout_text(outcome.summary))
    return outcome.exit_code


def _current_branch(repo_dir: Path) -> str:
    proc = subprocess.run(
        ["git", "symbolic-ref", "--short", "-q", "HEAD"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _cmd_score(args: argparse.Namespace) -> int:
    repo_dir = Path(args.repo_dir)
    repo = GitRepo(repo_dir)
    try:
        policy = load_policy(repo.show(args.base, POLICY_FILE))
        meta = PRMeta(
            number=0,
            title="",
            body="",
            author="local",
            author_is_bot=False,
            head_ref=_current_branch(repo_dir),
            base_sha=args.base,
            head_sha=args.head,
            draft=False,
            is_fork=False,
        )
        _, result, brief = analyze(repo, meta, policy, [], _brief_client())
    except PolicyError as error:
        print(f"Invalid {POLICY_FILE} at {args.base}: {error}", file=sys.stderr)
        return 2
    except GitError as error:
        print(error, file=sys.stderr)
        return 2
    print(result.model_dump_json(indent=2) if args.json else render_comment(result, brief))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="review-compass")
    sub = parser.add_subparsers(dest="command", required=True)
    pr = sub.add_parser("pr", help="handle one pull_request event (GitHub Action)")
    pr.add_argument("--event", default=None)
    pr.add_argument("--repo-dir", default=".")
    score = sub.add_parser("score", help="score a local revision range")
    score.add_argument("--base", required=True)
    score.add_argument("--head", required=True)
    score.add_argument("--repo-dir", default=".")
    score.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "pr":
            return _cmd_pr(args)
        return _cmd_score(args)
    except Exception as exc:
        # Type name only: never tokens, keys, prompts or tracebacks unless debugging.
        print(f"review-compass: internal error ({type(exc).__name__})", file=sys.stderr)
        if os.environ.get("REVIEW_COMPASS_DEBUG") == "1":
            traceback.print_exc()
        return 1
