# Hand check of the outcome labels

The backtest labels each merged PR as `reverted` and/or `fix_followup` with the
rules in `src/review_compass/backtest/outcomes.py`. Before trusting the numbers
in `REPORT.md`, the labels were checked by eye against the commit history and
the pull requests on GitHub (2026-11-18, policy fingerprint `eea628015697`).

- **Precision:** is a labelled PR really reverted / really followed by a fix?
- **Recall:** which revert commits in the same time span did the rules miss?

For pydantic/pydantic and home-assistant/core every labelled PR was checked.
For vercel/next.js a sample was fixed before looking: every 3rd `reverted`
label and every 5th `fix_followup`-only label in descending PR order.

`fix_followup` includes `reverted` by construction (`fix_followup = reverted or ...`),
so the two outcomes are not independent. "Fix follow-up only" below means
labelled `fix_followup` but not `reverted`.

## Precision

| Repo | Label | Checked | True | False | Unclear |
|---|---|---:|---:|---:|---:|
| pydantic/pydantic | reverted | 12 of 12 | 12 | 0 | 0 |
| pydantic/pydantic | fix follow-up only | 6 of 6 | 6 | 0 | 0 |
| home-assistant/core | reverted | 1 of 1 | 1 | 0 | 0 |
| home-assistant/core | fix follow-up only | 13 of 13 | 9 | 0 | 4 |
| vercel/next.js | reverted | 11 of 32 | 11 | 0 | 0 |
| vercel/next.js | fix follow-up only | 11 of 51 | 4 | 5 | 2 |

Notes:

- **Reverted** labels were correct in all 24 checked cases. In vercel/next.js,
  2 of the 11 are reverts of reverts (the label means "the revert was undone")
  and 1 is a planned temporary disable that was later lifted. Only 8 of the 11
  are reverts of a change that turned out to be bad.
- **Fix follow-up** is a much weaker label:
  - pydantic/pydantic: 2 of the 6 are trivial (a docstring typo, the completion
    of an incomplete fix).
  - home-assistant/core: all 13 triggers are test, snapshot or CI repairs after
    concurrent merges, none is a production bug fix. In the 4 unclear cases the
    later PR, not the labelled one, was at fault.
  - vercel/next.js: of the 11 sampled, 6 are test or CI repairs and 5 are
    coincidental (a mention as context, a release-branch backport, a re-land
    of the same change). None is a production bug fix.

## Recall for reverts

Revert commits on the default branch in the evaluated time span (subject starts
with `Revert` or body contains `This reverts commit`):

| Repo | Revert commits | Matched to a labelled PR | Excluded by the stated rules | Missed |
|---|---:|---:|---:|---:|
| pydantic/pydantic | 30 | 12 | 12 | 6 (4 firm, 2 weak) |
| home-assistant/core | 7 | 1 | 5 | 1 |
| vercel/next.js | 35 | 27 | 6 | 2 (3 target PRs) |

"Excluded by the stated rules" covers reverts later than 14 days after the
merge, reverts whose target is not an evaluated PR, and commits titled
`Revert ...` that are not reverts.

Missed PRs (should have been labelled `reverted`):

- pydantic/pydantic, firm: #8583, #9746, #10886, #13582. Weak: #7360 (partial),
  #7636 (target not verified).
- home-assistant/core: #189191 (partial revert).
- vercel/next.js: #100651, #99689, #100597 (the 2 missed revert commits in the
  table, one of them with two targets), and a fourth PR, #100126, which was
  reverted by a commit that is not titled `Revert` and is therefore not among
  the 35 revert commits counted above. All 4 are firm.

Why they were missed: squash-merged revert PRs carry no `This reverts commit <sha>`
line, so detection depends on `Reverts #N` in the body or an exact title match.
A revert that references its target by URL (`/pull/N`), names a second target
("and #N"), has an edited title, or names no PR number at all is not detected.

The rules were **not** changed after this check. The numbers in `REPORT.md`
come from the rules as they were before the first full run.

## Sensitivity: missed reverts counted as reverted

AUC of score against `reverted`, once with the labels as reported and once with
the firm misses above added as reverted. This uses the same scores; nothing
else changes.

| Repo | Reverted as reported | AUC as reported | Reverted with firm misses | AUC with firm misses |
|---|---:|---:|---:|---:|
| pydantic/pydantic | 12 | 0.461 | 16 | 0.445 |
| home-assistant/core | 1 | 0.581 | 2 | 0.709 |
| vercel/next.js | 32 | 0.537 | 36 | 0.566 |

The missed reverts do not change the conclusion: the score does not separate
reverted PRs from the rest in a useful way.

## Pooled counts

All three repositories together (7061 evaluated PRs), from the `rows.jsonl`
files in this directory:

| Bucket | PRs | Reverted | Reverted rate | Fix follow-up | Fix follow-up rate |
|---|---:|---:|---:|---:|---:|
| low | 4428 | 23 | 0.5% | 57 | 1.3% |
| medium | 2088 | 19 | 0.9% | 46 | 2.2% |
| high | 545 | 3 | 0.6% | 12 | 2.2% |

## Signal fire rates

No signal fires on 0% or 100% of PRs in any repository (see `REPORT.md`). The
`size` signal fires on 75.2% to 91.6% of PRs and therefore separates little.
The `agent` signal ranges from 1.1% (pydantic/pydantic, whose sample reaches
back several years) to 35.9% (home-assistant/core, last weeks only), so the
three samples cover different periods and are not comparable on that signal.
