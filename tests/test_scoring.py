from typing import Any

import pytest

from review_compass.models import FileChange, FileFeatures, FileKind, PRFeatures, RiskResult
from review_compass.policy import load_policy
from review_compass.scoring import score


def feats(**overrides: Any) -> PRFeatures:
    base: dict[str, Any] = {
        "files": [],
        "effective_lines": 0,
        "source_changed": False,
        "tests_changed": False,
        "dependents_total": 0,
        "max_churn": 0,
        "unfamiliar_share": 0.0,
        "directories": 1,
        "agent_authored": False,
        "agent_evidence": None,
        "guard_touched": False,
        "docs_only": False,
    }
    base.update(overrides)
    return PRFeatures(**base)


def file_feat(
    path: str,
    *,
    kind: FileKind = FileKind.SOURCE,
    sensitive: bool = False,
    churn_90d: int = 0,
    lines: int = 0,
    hunks: list[tuple[int, int]] | None = None,
    status: Any = "modified",
    dependents: int | None = 0,
    existed_at_base: bool = True,
    author_touched_before: bool = True,
) -> FileFeatures:
    change = FileChange(
        path=path,
        old_path=None,
        status=status,
        additions=lines,
        deletions=0,
        binary=False,
        hunks=hunks or [],
    )
    return FileFeatures(
        change=change,
        kind=kind,
        sensitive=sensitive,
        guard=False,
        dependents=dependents,
        churn_90d=churn_90d,
        existed_at_base=existed_at_base,
        author_touched_before=author_touched_before,
    )


P = load_policy(None)


def pts(r: RiskResult, signal: str) -> int:
    return next((c.points for c in r.contributions if c.signal == signal), 0)


@pytest.mark.parametrize("lines,expected", [(0, 0), (10, 3), (70, 9), (310, 15), (5000, 15)])
def test_size(lines: int, expected: int) -> None:
    assert pts(score(feats(effective_lines=lines), P), "size") == expected


@pytest.mark.parametrize("n,expected", [(0, 0), (1, 5), (3, 10), (7, 15), (14, 15)])
def test_dependents(n: int, expected: int) -> None:
    assert pts(score(feats(dependents_total=n), P), "dependents") == expected


@pytest.mark.parametrize("churn,expected", [(0, 0), (3, 3), (9, 9), (20, 9)])
def test_hotspot(churn: int, expected: int) -> None:
    assert pts(score(feats(max_churn=churn), P), "hotspot") == expected


@pytest.mark.parametrize("share,expected", [(0.0, 0), (0.5, 4), (1.0, 8)])
def test_unfamiliar(share: float, expected: int) -> None:
    assert pts(score(feats(unfamiliar_share=share), P), "unfamiliar") == expected


@pytest.mark.parametrize("dirs,expected", [(1, 0), (3, 2), (6, 5), (10, 5)])
def test_spread(dirs: int, expected: int) -> None:
    assert pts(score(feats(directories=dirs), P), "spread") == expected


def test_combined_example() -> None:
    f = feats(
        files=[file_feat("auth/session.py", sensitive=True, churn_90d=9)],
        effective_lines=10,
        source_changed=True,
        tests_changed=False,
        dependents_total=14,
        max_churn=9,
        agent_authored=True,
        agent_evidence="Co-Authored-By: Claude",
    )
    r = score(f, P)
    assert (r.score, r.bucket) == (75, "high")
    assert [c.signal for c in r.contributions] == [
        "sensitive_path",
        "no_tests",
        "dependents",
        "hotspot",
        "agent",
        "size",
    ]
    assert r.contributions[0].reason == "touches sensitive path: auth/session.py"
    assert r.contributions[4].reason == "authored by agent (Co-Authored-By: Claude)"


SENS = [file_feat("auth/x.py", sensitive=True)]


@pytest.mark.parametrize(
    "kw,total,bucket",
    [
        (dict(files=SENS, max_churn=9), 34, "low"),  # 25 + 9
        (dict(files=SENS, max_churn=9, directories=2), 35, "medium"),  # 25 + 9 + 1
        (
            dict(
                files=SENS,
                max_churn=9,
                source_changed=True,
                agent_authored=True,
                agent_evidence="x",
                directories=3,
            ),
            59,
            "medium",
        ),  # 25 + 18 + 9 + 5 + 2
        (
            dict(
                files=SENS,
                max_churn=9,
                source_changed=True,
                agent_authored=True,
                agent_evidence="x",
                effective_lines=10,
            ),
            60,
            "high",
        ),  # 25 + 18 + 9 + 5 + 3
    ],
)
def test_bucket_edges(kw: dict[str, Any], total: int, bucket: str) -> None:
    r = score(feats(**kw), P)
    assert (r.score, r.bucket) == (total, bucket)


def test_docs_only_is_zero() -> None:
    r = score(feats(docs_only=True, effective_lines=0, agent_authored=True), P)
    assert (r.score, r.bucket, r.contributions, r.notes) == (0, "low", [], ["documentation only"])


def test_empty_pr_is_low() -> None:  # Review Focus 5
    r = score(feats(), P)
    assert (r.score, r.bucket) == (0, "low")


def test_clamped_to_100_and_custom_weights() -> None:
    heavy = load_policy("weights: {sensitive_path: 90, no_tests: 90}")
    f = feats(files=[file_feat("auth/x.py", sensitive=True)], source_changed=True)
    assert score(f, heavy).score == 100


def test_unmeasured_dependents_note() -> None:
    r = score(feats(dependents_total=None, source_changed=True), P)
    assert "dependents: not measured (unsupported language)" in r.notes


def test_file_ranking_and_skim() -> None:
    f = feats(
        files=[
            file_feat("README.md", kind=FileKind.DOC),
            file_feat("src/util.py", lines=10),
            file_feat(
                "auth/session.py",
                sensitive=True,
                churn_90d=9,
                lines=40,
                hunks=[(88, 140), (5, 6)],
            ),
            file_feat("src/moved.py", status="renamed", lines=0),
        ],
        source_changed=True,
        effective_lines=50,
    )
    files = score(f, P).files
    order = ["auth/session.py", "src/util.py", "README.md", "src/moved.py"]
    assert [x.path for x in files] == order
    assert [x.skim for x in files] == [False, False, True, True]
    assert files[0].span == (88, 140)


def test_is_pure() -> None:
    f = feats(effective_lines=70, max_churn=3)
    assert score(f, P) == score(f, P)


@pytest.mark.parametrize(
    "weight,share,expected", [(11, 15 / 22, 8), (13, 15 / 26, 8), (45, 7 / 10, 32)]
)
def test_half_up_is_float_safe(weight: int, share: float, expected: int) -> None:
    policy = load_policy(f"weights: {{unfamiliar: {weight}}}")
    assert pts(score(feats(unfamiliar_share=share), policy), "unfamiliar") == expected


def _reason(r: RiskResult, signal: str) -> str:
    return next(c.reason for c in r.contributions if c.signal == signal)


def test_sensitive_reason_more_suffix() -> None:
    files = [file_feat(p, sensitive=True) for p in ("auth/a.py", "auth/b.py", "auth/c.py")]
    r = score(feats(files=files), P)
    assert _reason(r, "sensitive_path") == "touches sensitive path: auth/a.py (+2 more)"


def test_hotspot_reason_names_top_source_file_first_on_ties() -> None:
    files = [
        file_feat("tests/t.py", kind=FileKind.TEST, churn_90d=50),
        file_feat("src/a.py", churn_90d=4),
        file_feat("src/b.py", churn_90d=7),
        file_feat("src/c.py", churn_90d=7),
    ]
    r = score(feats(files=files, max_churn=7), P)
    assert _reason(r, "hotspot") == "hotspot: src/b.py changed 7x in 90 days"


def test_hotspot_reason_without_source_file_has_no_placeholder() -> None:
    files = [file_feat("tests/t.py", kind=FileKind.TEST, churn_90d=5)]
    r = score(feats(files=files, max_churn=5), P)
    assert _reason(r, "hotspot") == "hotspot: changed 5x in 90 days"


def test_unfamiliar_reason_percentage() -> None:
    r = score(feats(unfamiliar_share=0.666), P)
    assert _reason(r, "unfamiliar") == "author has not changed 67% of these files before"


@pytest.mark.parametrize("touched", [True, False])
def test_guard_touched_passthrough(touched: bool) -> None:
    assert score(feats(guard_touched=touched), P).guard_touched is touched


def test_file_reasons_content_and_order() -> None:
    f = feats(
        files=[
            file_feat(
                "auth/x.py",
                sensitive=True,
                dependents=3,
                churn_90d=4,
                existed_at_base=True,
                author_touched_before=False,
                lines=10,
            )
        ]
    )
    (rank,) = score(f, P).files
    assert rank.reasons == [
        "sensitive path",
        "3 dependents",
        "changed 4x in 90 days",
        "new to author",
        "10 lines",
    ]
    assert rank.score == 25 + 10 + 4 + 8 + 3


def test_zero_weight_hotspot_drops_signal_and_file_points() -> None:
    policy = load_policy("weights: {hotspot: 0}")
    r = score(feats(files=[file_feat("src/a.py", churn_90d=9)], max_churn=9), policy)
    assert pts(r, "hotspot") == 0
    assert r.files[0].score == 0
    assert "changed 9x in 90 days" not in r.files[0].reasons


def test_no_unmeasured_note_when_dependents_zero() -> None:
    assert score(feats(dependents_total=0, source_changed=True), P).notes == []
