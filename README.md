# Review Compass

Review Compass is a GitHub Action and Python command line tool. For each pull
request it computes a deterministic risk score from a handful of visible
signals and posts one comment: a risk bucket with reasons, a suggested reading
order, and a list of files that can be skimmed.

## Read this first: the backtest result is weak

The score was replayed over the merged pull requests of three public
repositories and compared with what happened afterwards (reverted within 14
days, or followed by a fix). The result does not support using it as a risk
predictor:

- AUC of score versus "reverted" is 0.461 (pydantic/pydantic), 0.581
  (home-assistant/core) and 0.537 (vercel/next.js). 0.5 is chance.
- The high bucket does not have a higher revert rate than the medium bucket in
  any of the three repositories.
- Reverts found by hand that the labelling rules missed were added in a
  sensitivity check. It does not change the picture (AUC 0.445, 0.709, 0.566).
- The only visible pattern is in vercel/next.js: fix follow-up rate and median
  hours to merge rise from the low to the high bucket. The hand check shows
  that the fix follow-up label there is mostly test or CI repairs and
  coincidental references, so this is not evidence of risk either.

The data does not show that this score predicts reverts. What the tool is
therefore: a transparent, deterministic reading-order and triage aid whose
reasons are visible in every comment. It is not a validated risk predictor, and
its auto-approval option (off by default) should stay off. Details, tables and
limits are in [Backtest](#backtest).

## Contents

- [The problem](#the-problem)
- [Example comment](#example-comment)
- [Install](#install)
- [Policy file](#policy-file-review-compassyml)
- [Local use](#local-use)
- [How the score works](#how-the-score-works)
- [Auto-approval](#auto-approval)
- [Backtest](#backtest)
- [Limits](#limits)
- [Security notes](#security-notes)

## The problem

Chen and Stratton (Harvard), *Artificial Intelligence in the Firm: Bottlenecks
in Software Production*, 2026, report that AI coding agents raise the number of
pull requests by 35.7 % but the number of completed tickets by only 13.7 %.
Code review is the bottleneck: time to merge rises by 56.1 %, waiting time by
59.9 %, active review time by 58.5 % and comments per pull request by 38.6 %.
AI review bots are in use at 79.1 % of the firms studied, yet only 10.8 % of
pull requests receive an AI comment. Humans still read everything.

Review Compass does not add another pile of comments. It posts one comment per
pull request, updated on every push, and tries to answer two questions: how
closely should a human look, and where first. Every point in the score has a
stated reason. Whether the first answer is any good is what the backtest
measures; see above for the outcome.

## Example comment

This is the format of a real rendered comment. The content comes from a test
fixture, not from a real pull request.

````markdown
<!-- review-compass -->
## Review Compass: HIGH (72/100)

**Why**
- touches sensitive path: auth/session.py (+25)
- source changed, no test changes (+18)

**Intent check**
Issue \#31 asks for a logout fix. This PR also changes token lifetime.

**Read first**
1. `auth/session.py:88-140` — session invalidation rewritten
2. `api/login.py:12-30` — caller of changed function

**Skim (9 files)**
`d1.md`, `d2.md`, `d3.md`, `d4.md`, `d5.md`, `d6.md`, `d7.md`, `d8.md`, …and 1 more

<sub>Notes: dependents: not measured (unsupported language)</sub>
````

Without an Anthropic API key the "Intent check" section reads
`not run (no API key)` and everything else is unchanged. The action also sets
one label per pull request: `review:low`, `review:medium` or `review:high`.

## Install

Add `.github/workflows/review-compass.yml` to the repository:

```yaml
name: Review Compass

on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]

permissions:
  contents: read
  pull-requests: write
  issues: read

concurrency:
  group: review-compass-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  review-compass:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
      - uses: Paul1451/review-compass@main
        with:
          # Optional. Without it the score runs as usual, only the intent check is skipped.
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
```

Notes:

- `fetch-depth: 0` is required. The score reads file history (churn in the last
  90 days, who touched a file before) and the merge base. With a shallow clone
  the run fails with a hint.
- Permissions: `contents: read` to read the repository, `pull-requests: write`
  to post the comment, set the label and submit a review, and `issues: read`
  to read linked issues (closing keywords such as `fixes #12` in the pull
  request body).
- The concurrency group cancels a running job when a new push arrives.
- Pin the action to a commit SHA (`Paul1451/review-compass@<full-sha>`)
  instead of `@main`. The action runs with a token that can write to pull
  requests, so a moving branch is a supply-chain risk.
- Inputs: `anthropic-api-key` (optional), `github-token` (default
  `github.token`), `bot-login` (login of the account that posts, default
  `github-actions[bot]`; needed when a different token identity is used).
- Pull requests from forks get no comment and no label, because their token
  cannot write. The brief goes to the job log and the step summary only.
- Draft pull requests are skipped unless `skip_drafts: false` is set in the
  policy file.

## Policy file (`.review-compass.yml`)

An optional file at the repository root. It is always read from the base
commit of the pull request, never from the pull request head, so a pull
request cannot loosen its own rules. An empty file means all defaults. Unknown
keys and invalid values make the run fail with a message instead of being
ignored.

```yaml
sensitive_paths: []   # gitignore-style patterns, added to the built-in list
thresholds:
  medium: 35          # score >= medium is "medium"
  high: 60            # score >= high is "high"; needs 0 < medium < high <= 100
weights:              # points per signal at full strength (integers >= 0)
  sensitive_path: 25
  no_tests: 18
  dependents: 15
  size: 15
  hotspot: 9
  unfamiliar: 8
  spread: 5
  agent: 5
skip_drafts: true     # do nothing for draft pull requests
auto_approve: false   # see Auto-approval
llm:
  enabled: true       # false: never call the model, even with a key
  model: claude-haiku-5-5
  max_input_tokens: 30000
```

Sensitive paths:

- Built-in patterns (always active): `**/auth/**`, `**/*auth*`,
  `**/security/**`, `**/crypto/**`, `**/payments/**`, `**/billing/**`,
  `**/migrations/**`, `**/*migration*`, `**/Dockerfile*`, `**/*.tf`,
  `**/k8s/**`, `**/helm/**`, `**/pyproject.toml`, `**/requirements*.txt`,
  `**/setup.py`, `**/setup.cfg`, `**/package.json`, `**/go.mod`,
  `**/Cargo.toml`.
- `sensitive_paths` in the file adds to this list. A negation pattern in the
  file cannot cancel a built-in match.
- Guard paths `.review-compass.yml` and `.github/workflows/**` are always
  sensitive and cannot be turned off.

## Local use

Score a revision range in a local checkout without GitHub:

```sh
uvx --from git+https://github.com/Paul1451/review-compass \
  review-compass score --base origin/main --head HEAD
```

Add `--json` for the machine-readable result and `--repo-dir PATH` to point at
another checkout. The policy is read from the `--base` revision. If
`ANTHROPIC_API_KEY` is set in your shell, the intent check runs and the diff of
the top-ranked files is sent to the Anthropic API; unset the variable to keep
everything local.

## How the score works

The score is a capped sum of eight signals. Each signal contributes
`weight x fraction`, rounded half up to whole points; signals with 0 points are
not listed. The weights are hand-set priors, not fitted to data. Pull requests
that change only documentation score 0.

| Signal | Weight | Fraction (0 to 1) | Reason shown |
|---|---:|---|---|
| `sensitive_path` | 25 | 1 if any changed file matches a sensitive path | touches sensitive path: ... |
| `no_tests` | 18 | 1 if source changed and no test file changed | source changed, no test changes |
| `dependents` | 15 | floor(log2(1 + n)) / 3, n = files that import the changed modules | n files depend on changed modules |
| `size` | 15 | log2(1 + lines / 10) / 5, lines = added and removed in source and config files | n lines changed |
| `hotspot` | 9 | busiest changed source file: commits in the last 90 days / 9 | hotspot: file changed n times in 90 days |
| `unfamiliar` | 8 | share of changed existing source files the author has not touched before | author has not changed n% of these files before |
| `spread` | 5 | (directories touched - 1) / 5 | spans n directories |
| `agent` | 5 | 1 if the pull request looks agent-authored | authored by agent (evidence) |

Fractions are capped at 1 and the total at 100. Buckets: `low` below 35,
`medium` from 35, `high` from 60 (thresholds are configurable).

Agent authorship is detected from a `Co-Authored-By` trailer naming a known
agent with a matching address, a "Generated with Claude Code" marker in the
body, a bot author, or a branch prefix such as `claude/`. It is a heuristic.

Dependents are measured for Python and JavaScript/TypeScript files
(`.py`, `.js`, `.jsx`, `.ts`, `.tsx`, `.mjs`, `.cjs`, `.mts`, `.cts`). For
other languages the signal is skipped and the comment notes it.

Reading order, per file: score from sensitive path, dependents, hotspot,
unfamiliarity and size. Source files come first, then test files, then a skim
list of documentation, generated files, lockfiles and pure renames. For the top
files the comment shows the largest changed hunk as a line range.

## Auto-approval

Off by default (`auto_approve: false`). Given the backtest result, leave it
off.

If switched on, the action submits an approving review only when all of these
hold:

- the pull request is not from a fork,
- it is not a draft,
- the bucket is `low`,
- no changed file is sensitive and no guard path is touched,
- the comment and label were written without error, and
- the head commit is still the one that was scored.

The decision uses only the deterministic bucket. When a pull request stops
being eligible, the action dismisses its own earlier approval. The
repository setting "Allow GitHub Actions to create and approve pull requests"
(Settings, Actions, General) must be enabled for the approval to go through.

Enable it only where every person with push access to pull request branches
is trusted, because the bucket can be influenced by what a branch author
changes.

## Backtest

### Method

- Three repositories: pydantic/pydantic, home-assistant/core, vercel/next.js.
- The same built-in default policy for every repository, no `.review-compass.yml`
  of its own, no LLM. Policy fingerprint `eea628015697`.
- The defaults were frozen as git tag `weights-v1` before the first full run.
  They were not tuned on the results.
- The 3000 most recent merged pull requests were requested per repository.
  Pull requests merged less than 14 days before the run were not evaluated,
  and a few without a head commit were skipped.
- Outcome window: 14 days after merge.
  - `reverted`: a later pull request or commit reverts it (title
    `Revert "<title>"`, `Reverts #N` in the body, or `This reverts commit <sha>`).
  - `fix_followup`: reverted, or a later non-bot pull request with a title
    containing fix, hotfix or regression references it by number.
- Each pull request is scored as it was at merge time, from git history only.

The full report is [`backtest/REPORT.md`](backtest/REPORT.md); the per-pull-request
rows are in `backtest/<owner>__<repo>/rows.jsonl`.

### Results

Share is the share of evaluated pull requests; CI is a 95 % confidence interval.

#### pydantic/pydantic

2971 evaluated PRs, 1 skipped (no head).

| Bucket | PRs | Share | Reverted (95% CI) | Fix follow-up (95% CI) | Median review threads | Median hours to merge |
|---|---:|---:|---|---|---:|---:|
| low | 1924 | 64.8% | 0.4% (0.2% to 0.8%) | 0.7% (0.4% to 1.2%) | 0.0 | 6.5 |
| medium | 815 | 27.4% | 0.5% (0.2% to 1.3%) | 0.6% (0.3% to 1.4%) | 0.0 | 8.1 |
| high | 232 | 7.8% | 0.0% (0.0% to 1.6%) | 0.0% (0.0% to 1.6%) | 0.0 | 7.4 |

Skimming low-risk PRs would cover 64.8% of PRs and contain 8 of 12 reverted PRs.

- AUC, score vs reverted: 0.461
- AUC, score vs fix follow-up: 0.471
- Spearman, score vs review threads: 0.162

#### home-assistant/core

1386 evaluated PRs, 0 skipped (no head).

| Bucket | PRs | Share | Reverted (95% CI) | Fix follow-up (95% CI) | Median review threads | Median hours to merge |
|---|---:|---:|---|---|---:|---:|
| low | 1030 | 74.3% | 0.1% (0.0% to 0.5%) | 1.0% (0.5% to 1.8%) | 0.0 | 3.7 |
| medium | 330 | 23.8% | 0.0% (0.0% to 1.2%) | 1.2% (0.5% to 3.1%) | 0.0 | 2.2 |
| high | 26 | 1.9% | 0.0% (0.0% to 12.9%) | 0.0% (0.0% to 12.9%) | 0.5 | 3.0 |

Skimming low-risk PRs would cover 74.3% of PRs and contain 1 of 1 reverted PRs.

- AUC, score vs reverted: 0.581
- AUC, score vs fix follow-up: 0.533
- Spearman, score vs review threads: 0.119

#### vercel/next.js

2704 evaluated PRs, 13 skipped (no head).

| Bucket | PRs | Share | Reverted (95% CI) | Fix follow-up (95% CI) | Median review threads | Median hours to merge |
|---|---:|---:|---|---|---:|---:|
| low | 1474 | 54.5% | 0.9% (0.6% to 1.6%) | 2.3% (1.7% to 3.2%) | 0.0 | 18.2 |
| medium | 943 | 34.9% | 1.6% (1.0% to 2.6%) | 3.9% (2.9% to 5.4%) | 0.0 | 33.9 |
| high | 287 | 10.6% | 1.0% (0.4% to 3.0%) | 4.2% (2.4% to 7.2%) | 0.0 | 45.7 |

Skimming low-risk PRs would cover 54.5% of PRs and contain 14 of 32 reverted PRs.

- AUC, score vs reverted: 0.537
- AUC, score vs fix follow-up: 0.583
- Spearman, score vs review threads: 0.187

Reading the tables: in none of the three repositories does the high bucket
revert more often than the medium bucket, and the intervals overlap almost
everywhere. In home-assistant/core the high bucket has 26 pull requests and no
reverts, which says little. In vercel/next.js the fix follow-up rate and the
median hours to merge rise with the bucket (2.3%, 3.9%, 4.2% and 18.2, 33.9,
45.7 hours), but see the hand check below: that label is not a reliable sign
of defects. Skimming the low bucket would have skipped 14 of the 32 reverted
pull requests in vercel/next.js and 8 of 12 in pydantic/pydantic, roughly in
proportion to the low bucket's share of all pull requests.

Pooled over all three repositories (7061 evaluated PRs):

| Bucket | PRs | Reverted | Reverted rate | Fix follow-up | Fix follow-up rate |
|---|---:|---:|---:|---:|---:|
| low | 4428 | 23 | 0.5% | 57 | 1.3% |
| medium | 2088 | 19 | 0.9% | 46 | 2.2% |
| high | 545 | 3 | 0.6% | 12 | 2.2% |

`REPORT.md` also contains signal fire rates and a separate table for
agent-authored pull requests. The `size` signal fires on 75.2% to 91.6% of
pull requests and so separates little.

### Hand check

The labels were checked by eye against commit history and GitHub
([`backtest/HANDCHECK.md`](backtest/HANDCHECK.md)). All labelled pull requests
were checked in pydantic/pydantic and home-assistant/core; in vercel/next.js a
sample fixed in advance (every 3rd `reverted`, every 5th fix-follow-up-only
label).

- **Precision of `reverted`:** all 24 checked labels were correct (12 of 12,
  1 of 1, 11 of 32). In vercel/next.js 2 of the 11 are reverts of reverts and
  1 is a planned temporary disable, so 8 of the 11 are reverts of a change that
  turned out to be bad.
- **Precision of fix follow-up:** weak. In home-assistant/core all 13 checked
  triggers are test, snapshot or CI repairs after concurrent merges, none a
  production bug fix. In vercel/next.js, of 11 sampled, 6 are test or CI
  repairs and 5 are coincidental (a mention as context, a backport, a re-land);
  none is a production bug fix. In pydantic/pydantic 2 of the 6 are trivial.
- **Recall of `reverted`:** the rules missed some reverts. Squash-merged revert
  pull requests often carry no `This reverts commit` line, and a revert that
  names its target by URL, names a second target, has an edited title or names
  no number is not detected. Missed pull requests: pydantic/pydantic 6 (4
  firm, 2 weak) against 12 labelled, home-assistant/core 1 (a partial revert)
  against 1 labelled, vercel/next.js 4 against 32 labelled.
- **The rules were not changed after this check.** The reported numbers come
  from the rules as they were before the first full run.

Sensitivity: AUC of score versus `reverted`, as reported and with the firm
misses added as reverted (same scores, nothing else changed):

| Repo | Reverted as reported | AUC as reported | Reverted with firm misses | AUC with firm misses |
|---|---:|---:|---:|---:|
| pydantic/pydantic | 12 | 0.461 | 16 | 0.445 |
| home-assistant/core | 1 | 0.581 | 2 | 0.709 |
| vercel/next.js | 32 | 0.537 | 36 | 0.566 |

The home-assistant/core value rests on two reverted pull requests and is not
informative. Overall the missed reverts do not change the conclusion.

### Rerun

```sh
export GITHUB_TOKEN=...   # or log in with `gh auth login`
review-compass backtest pydantic/pydantic home-assistant/core vercel/next.js \
  --limit 3000 --out backtest
review-compass report backtest
```

`backtest` downloads the pull request history through the GitHub API, caches it
in `--cache-dir` (default `.cache`), and writes `report.json` and `rows.jsonl`
per repository. `report` rebuilds `REPORT.md` from the `report.json` files. A
run takes a long time and many API requests on large repositories. Results
change with the date of the run, because the pull requests are the most recent
ones. `--policy FILE` replays a different policy file.

## Limits

- Reverts are rare. Most buckets contain few or no reverts, so the confidence
  intervals are wide and small differences between buckets mean nothing.
- Only merged pull requests are evaluated. Pull requests that were rejected or
  abandoned, possibly because a reviewer saw a problem, are not in the data
  (survivorship).
- No causal claim. A bucket correlating with an outcome would not show that
  review effort at that bucket changes the outcome.
- Revert detection is incomplete. The hand check found reverts the rules
  missed in all three repositories (see above).
- Fix follow-up detection is fuzzy: it matches titles and references, and the
  hand check found it mostly picks up test and CI repairs and coincidental
  mentions, not production bugs.
- The three samples cover different time spans. pydantic/pydantic reaches back
  several years, home-assistant/core only a few weeks. The share of
  agent-authored pull requests ranges from 1.1% to 35.9%, so the samples are
  not comparable on that signal.
- The built-in sensitive paths are generic. They do not know your repository;
  extend them with `sensitive_paths`.
- Python import resolution matches module suffixes and can count too many
  dependents when module names repeat across packages.
- The weights are hand-set priors, not fitted. They were frozen before the
  first run and not tuned afterwards, which is also why they are not good.
- Dependents are not measured for languages other than Python and
  JavaScript/TypeScript.

## Security notes

- The policy is read from the base commit only. A pull request cannot change
  the rules that score it.
- `.review-compass.yml` and `.github/workflows/**` are guard paths: always
  sensitive, cannot be disabled, always block auto-approval.
- The workflow uses `pull_request`. `pull_request_target` is never used.
- Code from the pull request is never executed. It is only read through git,
  with argument lists and no shell strings.
- Model output is text only. It fills the intent check and the reading-list
  notes and cannot change the score, the bucket or the approval decision.
- Text from pull requests (titles, bodies, issues, diffs, file names) is
  untrusted. It is escaped in the comment, and passed to the model inside
  delimited blocks with an instruction to treat it as data.
- Fork pull requests are never auto-approved and get no comment or label.
- Without `ANTHROPIC_API_KEY` no code leaves the runner. With a key, the pull
  request title, body (first 4000 characters), linked issues, the paths of the
  five top-ranked files and their diff (truncated to `llm.max_input_tokens`)
  are sent to the Anthropic API.
  Set `llm.enabled: false` in the policy file to prevent this even when a
  key is present.

## License

See [LICENSE](LICENSE).
