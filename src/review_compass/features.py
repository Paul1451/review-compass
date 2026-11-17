"""Feature extraction: git history and PR metadata in, ``PRFeatures`` out.

Every history lookup uses the merge base, never the head, so the same
function can run live and in a backtest without looking into the future.
"""

import re
from collections.abc import Sequence
from pathlib import PurePosixPath

from review_compass.classify import classify
from review_compass.gitrepo import GitRepo
from review_compass.imports import SUPPORTED_EXTENSIONS, ImportIndex
from review_compass.models import FileFeatures, FileKind, PRFeatures, PRMeta
from review_compass.policy import Policy

AGENT_NAMES = (
    "claude",
    "copilot",
    "cursor",
    "codex",
    "devin",
    "aider",
    "openhands",
    "jules",
    "gemini",
)
_CO_AUTHOR_RE = re.compile(r"^\s*co-authored-by:\s*(.*?)\s*<([^>]*)>", re.IGNORECASE | re.MULTILINE)
_BODY_MARKER = "Generated with [Claude Code]"
_AGENT_RES = {a: re.compile(rf"(?<![a-z0-9]){a}(?![a-z0-9])", re.IGNORECASE) for a in AGENT_NAMES}
_AGENT_DOMAINS = frozenset(
    {"anthropic.com", "openai.com", "cursor.com", "aider.chat", "google.com", "devin.ai"}
    | {"cognition.ai"}
)
_NOREPLY_DOMAIN = "users.noreply.github.com"
_NOREPLY_PREFIX_RE = re.compile(r"^\d+\+")


def _agent_in(text: str) -> str | None:
    return next((a for a, pattern in _AGENT_RES.items() if pattern.search(text)), None)


def _agent_email(email: str) -> bool:
    local, _, domain = email.strip().lower().rpartition("@")
    if domain in _AGENT_DOMAINS:
        return True
    if domain == _NOREPLY_DOMAIN:
        bare = _NOREPLY_PREFIX_RE.sub("", local)
        if bare in AGENT_NAMES or bare.endswith("[bot]"):
            return True
    return "[bot]" in local


def detect_agent(meta: PRMeta, commit_messages: Sequence[str]) -> str | None:
    for message in commit_messages:
        for name, email in _CO_AUTHOR_RE.findall(message):
            if _agent_in(name) and _agent_email(email):
                return f"Co-Authored-By: {name}"
    if _BODY_MARKER in meta.body:
        return "PR body: Generated with Claude Code"
    if meta.author_is_bot and _agent_in(meta.author):
        return f"bot author: {meta.author}"
    ref = meta.head_ref.lower()
    for agent in AGENT_NAMES:
        if ref.startswith(f"{agent}/"):
            return f"branch prefix: {agent}/"
    return None


def extract_features(
    repo: GitRepo, meta: PRMeta, policy: Policy, index: ImportIndex | None = None
) -> PRFeatures:
    base = repo.merge_base(meta.base_sha, meta.head_sha)
    changes = repo.changed_files(base, meta.head_sha)
    tree = repo.ls_tree(base)
    if index is None:
        index = ImportIndex(repo)

    # Path as it exists at base: renamed files are looked up under their old name.
    base_path = {c.path: c.old_path or c.path for c in changes}
    existed = {c.path: base_path[c.path] in tree for c in changes}
    diff_paths = {c.path for c in changes} | {c.old_path for c in changes if c.old_path}

    queried = list(dict.fromkeys(base_path.values()))
    churn = repo.churn(base, queried)
    emails = repo.author_emails(base, meta.head_sha)
    touched = repo.touched_before(base, emails, queried)

    # Supported-ness follows the path as it exists at base, so a source file renamed
    # to a non-source name still counts. A PR without any such file has no dependents data.
    dependents = index.dependents(base, [p for p in queried if p in tree]) or {}

    files: list[FileFeatures] = []
    union: set[str] = set()
    source_supported = False
    for c in changes:
        query = base_path[c.path]
        kind = classify(c.path)
        if c.old_path is not None and classify(c.old_path) is FileKind.SOURCE:
            kind = FileKind.SOURCE
        deps: int | None = None
        if query.endswith(SUPPORTED_EXTENSIONS):
            found = dependents.get(query, set()) - diff_paths
            union |= found
            deps = len(found)
            source_supported = source_supported or kind is FileKind.SOURCE
        files.append(
            FileFeatures(
                change=c,
                kind=kind,
                sensitive=any(policy.is_sensitive(p) for p in (c.path, c.old_path) if p),
                guard=any(policy.is_guard(p) for p in (c.path, c.old_path) if p),
                dependents=deps,
                churn_90d=churn[query],
                existed_at_base=existed[c.path],
                author_touched_before=touched[query],
            )
        )

    kinds = {f.kind for f in files}
    source = [f for f in files if f.kind is FileKind.SOURCE]
    known_source = [f for f in source if f.existed_at_base]
    unfamiliar = sum(1 for f in known_source if not f.author_touched_before)
    agent = detect_agent(meta, repo.commit_messages(base, meta.head_sha))
    return PRFeatures(
        files=files,
        effective_lines=sum(
            f.change.additions + f.change.deletions
            for f in files
            if f.kind in (FileKind.SOURCE, FileKind.CONFIG)
        ),
        source_changed=any(f.change.additions + f.change.deletions > 0 for f in source),
        tests_changed=FileKind.TEST in kinds,
        dependents_total=len(union) if source_supported else None,
        max_churn=max((f.churn_90d for f in source), default=0),
        unfamiliar_share=unfamiliar / len(known_source) if known_source else 0.0,
        directories=len(
            {
                PurePosixPath(f.change.path).parent
                for f in files
                if f.kind in (FileKind.SOURCE, FileKind.CONFIG)
            }
        ),
        agent_authored=agent is not None,
        agent_evidence=agent,
        guard_touched=any(f.guard for f in files),
        docs_only=bool(files) and kinds <= {FileKind.DOC, FileKind.GENERATED, FileKind.LOCKFILE},
    )
