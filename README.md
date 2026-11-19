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
- Reverts found in the label check that the labelling rules missed were added
  in a sensitivity check. It does not change the conclusion (AUC 0.445, 0.709,
  0.566; the home-assistant/core value rests on 2 reverted pull
  requests).
- The visible patterns are weak and none is about reverts. In vercel/next.js
  the fix follow-up rate and the median hours to merge rise from the low to the
  high bucket, but the label check shows that the fix follow-up label there is
  mostly test or CI repairs and coincidental references. The score has a weak
  positive rank correlation with review threads (0.162, 0.119, 0.187), but the
  median thread count is 0 in nearly every bucket, so that says little. The
  share of pull requests with a changes-requested review is higher in the high
  bucket than in the low bucket in all three repositories (see
  [Results](#results)).

The data does not show that this score predicts reverts. What the tool is
therefore: a transparent, deterministic reading-order and triage aid whose
reasons are visible in every comment. It is not a validated risk predictor, and
its auto-approval option (off by default) should stay off. Details, tables and
limits are in [Backtest](#backtest).

The scoring rules are unchanged since the git tag `weights-v1`, which was set
before the first full backtest run. Later commits only touch approval logic,
reporting and documentation, so the published numbers still describe the code.

Status: the action has been tested against mocked GitHub API responses and
throwaway git repositories. It has also run in GitHub Actions on one private
repository: two pull request runs on 2026-11-18, about 23 seconds each. On the
second run the comment was updated in place and the label was replaced. Not yet
exercised in a live run: auto-approval, the intent check with an API key, and
creating labels in a repository where they do not exist yet. See
[Field test](#field-test-one-repository) for what that one repository showed.

## Contents

- [The problem](#the-problem)
- [Example comment](#example-comment)
- [Install](#install)
  - [Versions and pinning](#versions-and-pinning)
- [Policy file](#policy-file-review-compassyml)
- [Local use](#local-use)
- [How the score works](#how-the-score-works)
- [Auto-approval](#auto-approval)
- [Backtest](#backtest)
- [Limits](#limits)
  - [Field test (one repository)](#field-test-one-repository)
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

This is the unmodified output of `review-compass score --no-llm --format markdown` on a small
throwaway repository (Python files, one change to an `auth` path, source
changes without tests, one importer of the changed module, one documentation
file). It is not from a real pull request.

````markdown
<!-- review-compass -->
## Review Compass: HIGH (66/100)

**Why**
- touches sensitive path: src/auth/session.py (+25)
- source changed, no test changes (+18)
- 60 lines changed (+8)
- author has not changed 100% of these files before (+8)
- 1 files depend on changed modules (+5)
- hotspot: src/api/login.py changed 1x in 90 days (+1)
- spans 2 directories (+1)

**Intent check**
not run (no API key)

**Read first**
1. `src/auth/session.py:37-67`
2. `src/api/login.py:6-9`

**Skim (1 file)**
`docs/guide.md`
````

Without an Anthropic API key the "Intent check" section reads
`not run (no API key)`, as above. The intent check and the short "why" notes
next to files in the reading list need a key. The action also sets one label
per pull request: `review:low`, `review:medium` or `review:high`.

## Install

Add `.github/workflows/review-compass.yml` to the repository:

```yaml
name: Review Compass

on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review, edited]

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
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
        with:
          fetch-depth: 0
      - uses: Paul1451/review-compass@v0
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
- The concurrency group cancels a running job when a new push arrives. The
  `edited` trigger re-runs the check when a pull request is retargeted to
  another base branch.
- What is pinned: the action installs Review Compass and its Python
  dependencies from its own committed `uv.lock` (`uv sync --locked`), so they
  do not change between runs, `astral-sh/setup-uv` is pinned by commit SHA in
  `action.yml`, and the action pins its own `uv` version and caches against
  its own `uv.lock`. It does not read `pyproject.toml` or `uv.lock` of the
  calling repository
  ([issue #3](https://github.com/Paul1451/review-compass/issues/3)). What is
  not pinned: `@v0` in the snippet above, which is a moving tag (see
  [Versions and pinning](#versions-and-pinning)). The action runs with a token
  that can write to pull requests, so consider a full commit SHA
  (`Paul1451/review-compass@<full-sha>`), and pin `actions/checkout` by SHA as
  shown.
- Inputs: `anthropic-api-key` (optional), `github-token` (default
  `github.token`), `bot-login` (login of the account that posts, default
  `github-actions[bot]`; needed when a different token identity is used).
  On GitHub Enterprise Server the action uses the `GITHUB_API_URL` that the
  runner sets and sends the token to that host only.
- Pull requests from forks get no comment and no label, because their token
  cannot write. The brief goes to the job log and the step summary only.
- Draft pull requests are skipped unless `skip_drafts: false` is set in the
  policy file: no comment, no label, no approval. The only write is that an
  earlier approval by the action is withdrawn.
- git 2.30 or newer is required (the tool passes `--end-of-options` to
  `git rev-parse`). GitHub-hosted runners have it.
- Pull requests created with the default `GITHUB_TOKEN` do not trigger
  `pull_request` workflows at all, so agents that open pull requests that way
  get no comment.
- Labels are created on first use and get GitHub's default colour unless you
  create `review:low`, `review:medium` and `review:high` beforehand. Creating
  them in a repository where they do not exist yet has not been exercised in a
  live run.

### Versions and pinning

There are three ways to reference the action:

- `Paul1451/review-compass@v0`: a moving tag that follows the newest 0.x
  release.
- An exact release tag such as `Paul1451/review-compass@v0.1.0`.
- A full commit SHA (`Paul1451/review-compass@<full-sha>`): the strictest.

`@main` also works but can change at any time.

From the first release on, the history of `main` and of release tags will not
be rewritten. It was rewritten once, on 2026-11-18, before the first release
(author metadata only, file contents identical). Commit SHAs from before that
date no longer exist, so workflows pinned to one fail with "unable to find
version"
([issue #2](https://github.com/Paul1451/review-compass/issues/2)).

If you use the action from a private copy or fork, the action repository must
allow access from the calling repository under *Settings › Actions › General ›
Access*. Otherwise the run fails with "repository not found".

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
skip_drafts: true     # drafts: no comment, label or approval; an own earlier approval is withdrawn
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

A second example, for a TypeScript monorepo with Drizzle:

```yaml
sensitive_paths:
  - "packages/db/drizzle/*.sql"      # SQL migrations (Drizzle does not use a migrations/ directory)
  - "packages/db/src/schema/**"      # table definitions
  - "**/payments.ts"                 # payment logic
  - "**/totals.ts"                   # money and tax arithmetic
  - "**/export/**"                   # accounting exports
weights:
  agent: 0                           # switch a signal off if it fires on almost every pull request
```

Why this matters: the built-in sensitive paths know common names (auth,
`migrations/`, workflows, Dockerfile, package manifests, see the list above)
but not the domain-critical files of a given project. In the field test, money,
export and migration changes were rated too low until such a policy file was
added ([issue #5](https://github.com/Paul1451/review-compass/issues/5)).

Current gap: there is no way yet to exclude generated files (for example
Drizzle `meta/*_snapshot.json`) from the size signal or the reading order
([issue #4](https://github.com/Paul1451/review-compass/issues/4)).

Label names are fixed (`review:low`, `review:medium`, `review:high`) and not
configurable in this version. `llm.max_input_tokens` is approximate: the
message to the model is cut to 4 characters per token.

## Local use

Score a revision range in a local checkout without GitHub.

Try it without installing:

```sh
uvx --from git+https://github.com/Paul1451/review-compass@v0 \
  review-compass score --base main --head HEAD --no-llm
```

`uvx` resolves dependencies fresh and does not use the project's `uv.lock`.
For a reproducible install, clone the repository, run `uv sync --locked`, then
call `uv run review-compass ...`.

```sh
git clone https://github.com/Paul1451/review-compass
cd review-compass
uv sync --locked
uv run review-compass score --repo-dir /path/to/your/checkout \
  --base origin/main --head HEAD --no-llm
```

`review-compass --version` prints the installed version.

Output format: `score --format text|markdown|json`.

- `text` is the default. It is meant for reading in a terminal and has no
  Markdown escapes.
- `markdown` prints the comment exactly as the action posts it.
- `json` (or `--json`) prints the full result.

Use `--repo-dir PATH` to point at another checkout. git 2.30 or newer is
required. The policy is read from the `--base` revision. Use
`--no-llm` to be sure nothing leaves your machine: without it, if
`ANTHROPIC_API_KEY` is set in your shell, the reading-list notes are generated
by the model and the diff of the top-ranked files is sent to the Anthropic API.
(The intent check does not run locally, because there is no linked issue.)

## How the score works

The score is a capped sum of eight signals. Each signal contributes
`weight x fraction`, rounded half up to whole points; signals with 0 points are
not listed. The weights are hand-set priors, not fitted to data. Pull requests
that change only documentation, generated files or lockfiles are not scored:
they get 0 points and the note "only documentation, generated files or
lockfiles" (see [Limits](#limits)).

| Signal | Weight | Fraction (0 to 1) | Reason shown |
|---|---:|---|---|
| `sensitive_path` | 25 | 1 if any changed file matches a sensitive path | `touches sensitive path: PATH (+N more)` |
| `no_tests` | 18 | 1 if source changed and no test file changed | source changed, no test changes |
| `dependents` | 15 | floor(log2(1 + n)) / 3, n = files that import the changed modules | n files depend on changed modules |
| `size` | 15 | log2(1 + lines / 10) / 5, lines = added and removed in source and config files | n lines changed |
| `hotspot` | 9 | busiest changed source file: commits in the last 90 days / 9 | `hotspot: PATH changed Nx in 90 days` |
| `unfamiliar` | 8 | share of changed existing source files the author has not touched before | author has not changed n% of these files before |
| `spread` | 5 | (directories with changed source or config files - 1) / 5 | spans n directories |
| `agent` | 5 | 1 if the pull request looks agent-authored | authored by agent (evidence) |

Fractions are capped at 1 and the total at 100. Buckets: `low` below 35,
`medium` from 35, `high` from 60 (thresholds are configurable).

Agent authorship is detected from a `Co-Authored-By` trailer naming a known
agent with a matching address, the exact text `Generated with [Claude Code]`
in the body, a bot account whose login names a known agent (an ordinary bot
such as dependabot does not count), or a branch prefix of one of nine known
agents (for example `claude/`). It is a heuristic.

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
- no changed path (new or old) is under `.github/` or is a `CODEOWNERS` file,
- no changed file is a lockfile or generated, and no file counted as
  documentation has a code extension (`docs/conf.py`, scripts),
- the comment and label were written without error, and
- the head commit is still the one that was scored.

The decision uses only the deterministic bucket. Every run that does not end in
a fresh approval (a git error, an invalid policy, a draft, a failed write, a
changed head, an unexpected error) dismisses the action's own earlier approval
on a best-effort basis; if the dismissal fails, the job fails and says so.
The repository setting "Allow GitHub Actions to create and approve pull
requests" (Settings, Actions, General) must be enabled for the approval to go
through.

**Required branch protection.** Turn on "Dismiss stale pull request approvals
when new commits are pushed" (or "Require approval of the most recent
reviewable push"). Between a push and the end of the next run an earlier
approval still counts, and a run can fail or be cancelled; only the branch
protection setting closes that gap.

**What a branch author controls.** Enable auto-approval only where every
person with push access to pull request branches is trusted:

- For pull requests from the same repository GitHub runs the workflow file from
  the pull request. A branch author can rewrite or delete the job, or approve
  with the token directly. The guard paths only stop Review Compass from
  approving such a pull request; they do not stop the pull request. Require
  code owner review for `.github/**` (a `CODEOWNERS` entry) where
  auto-approval is used.
- The policy is read from the base commit of the base branch the pull request
  names. The `edited` trigger re-runs the check when the base branch is
  changed, but a pull request opened against a branch with a permissive policy
  is judged by that policy until then.
- The bucket can be influenced by what a branch author changes.

## Backtest

### Method

- Three repositories: pydantic/pydantic, home-assistant/core, vercel/next.js.
- The same built-in default policy for every repository, no `.review-compass.yml`
  of its own, no LLM. Policy fingerprint `eea628015697`.
- The defaults were frozen as git tag `weights-v1` before the first full run.
  They were not tuned on the results. The scoring rules are unchanged since
  that tag; later commits only touch approval logic, reporting and
  documentation.
- The 3000 most recently created merged pull requests were requested per
  repository, whatever their base branch (release-branch pull requests are
  rows in vercel/next.js). Pull requests merged less than 14 days before the
  run were not evaluated, and a few without a head commit were skipped.
- Outcome window: 14 days after merge.
  - `reverted`: a later pull request or commit reverts it (title
    `Revert "<title>"`, `Reverts #N` in the body, or `This reverts commit <sha>`).
  - `fix_followup`: reverted, or a later non-bot pull request with a title
    containing fix, fixes, fixed, hotfix or regression references it by
    number. Follow-ups that are backports or cherry-picks, and ones naming
    more than 5 pull requests, are excluded.
- Each pull request is scored as it was at its merge base (the commit it
  branched from), from git history only.

The full report is [`backtest/REPORT.md`](backtest/REPORT.md); the per-pull-request
rows are in `backtest/<owner>__<repo>/rows.jsonl`.

### Results

Share is the share of evaluated pull requests; CI is a 95 % confidence interval.

#### pydantic/pydantic

2971 evaluated PRs, 1 skipped (no head).

| Bucket | PRs | Share | Reverted (95% CI) | Fix follow-up (95% CI) | Median review threads | Median hours to merge | Changes requested |
|---|---:|---:|---|---|---:|---:|---:|
| low | 1924 | 64.8% | 0.4% (0.2% to 0.8%) | 0.7% (0.4% to 1.2%) | 0.0 | 6.5 | 6.6% |
| medium | 815 | 27.4% | 0.5% (0.2% to 1.3%) | 0.6% (0.3% to 1.4%) | 0.0 | 8.1 | 7.2% |
| high | 232 | 7.8% | 0.0% (0.0% to 1.6%) | 0.0% (0.0% to 1.6%) | 0.0 | 7.4 | 8.2% |

Skimming low-risk PRs would cover 64.8% of PRs and contain 8 of 12 reverted PRs.

Reverted rate, high bucket divided by low bucket: 0.00x

- AUC, score vs reverted: 0.461
- AUC, score vs fix follow-up: 0.471
- Spearman, score vs review threads: 0.162

#### home-assistant/core

1386 evaluated PRs, 0 skipped (no head).

| Bucket | PRs | Share | Reverted (95% CI) | Fix follow-up (95% CI) | Median review threads | Median hours to merge | Changes requested |
|---|---:|---:|---|---|---:|---:|---:|
| low | 1030 | 74.3% | 0.1% (0.0% to 0.5%) | 1.0% (0.5% to 1.8%) | 0.0 | 3.7 | 4.3% |
| medium | 330 | 23.8% | 0.0% (0.0% to 1.2%) | 1.2% (0.5% to 3.1%) | 0.0 | 2.2 | 5.2% |
| high | 26 | 1.9% | 0.0% (0.0% to 12.9%) | 0.0% (0.0% to 12.9%) | 0.5 | 3.0 | 19.2% |

Skimming low-risk PRs would cover 74.3% of PRs and contain 1 of 1 reverted PRs.

Reverted rate, high bucket divided by low bucket: 0.00x

- AUC, score vs reverted: 0.581
- AUC, score vs fix follow-up: 0.533
- Spearman, score vs review threads: 0.119

#### vercel/next.js

2704 evaluated PRs, 13 skipped (no head).

| Bucket | PRs | Share | Reverted (95% CI) | Fix follow-up (95% CI) | Median review threads | Median hours to merge | Changes requested |
|---|---:|---:|---|---|---:|---:|---:|
| low | 1474 | 54.5% | 0.9% (0.6% to 1.6%) | 2.3% (1.7% to 3.2%) | 0.0 | 18.2 | 1.5% |
| medium | 943 | 34.9% | 1.6% (1.0% to 2.6%) | 3.9% (2.9% to 5.4%) | 0.0 | 33.9 | 1.4% |
| high | 287 | 10.6% | 1.0% (0.4% to 3.0%) | 4.2% (2.4% to 7.2%) | 0.0 | 45.7 | 3.5% |

Skimming low-risk PRs would cover 54.5% of PRs and contain 14 of 32 reverted PRs.

Reverted rate, high bucket divided by low bucket: 1.10x

- AUC, score vs reverted: 0.537
- AUC, score vs fix follow-up: 0.583
- Spearman, score vs review threads: 0.187

Reading the tables: in none of the three repositories does the high bucket
revert more often than the medium bucket, and the intervals overlap almost
everywhere. The ratio of the high-bucket revert rate to the low-bucket revert
rate is 0.00x in pydantic/pydantic (no revert in the high bucket), 0.00x in
home-assistant/core and 1.10x in vercel/next.js; 1.0x would mean no
difference. In home-assistant/core the high bucket has 26 pull requests and no
reverts, which says little. In vercel/next.js the fix follow-up rate and the
median hours to merge rise with the bucket (2.3%, 3.9%, 4.2% and 18.2, 33.9,
45.7 hours), but see the label check below: that label is not a reliable sign
of defects. The share of pull requests with a changes-requested review rises
from the low to the high bucket in all three repositories (6.6% to 8.2%, 4.3%
to 19.2% on 26 pull requests, 1.5% to 3.5%), a weak pattern that is not an
outcome of the kind the backtest was built to test. Skimming the low bucket
would have skipped 14 of the 32 reverted pull requests in vercel/next.js and 8
of 12 in pydantic/pydantic, roughly in proportion to the low bucket's share of
all pull requests.

Pooled over all three repositories (7061 evaluated PRs); this table and the
sensitivity table below are printed by
[`backtest/derive_numbers.py`](backtest/derive_numbers.py), which reads the
`rows.jsonl` files:

| Bucket | PRs | Reverted | Reverted rate | Fix follow-up | Fix follow-up rate |
|---|---:|---:|---:|---:|---:|
| low | 4428 | 23 | 0.5% | 57 | 1.3% |
| medium | 2088 | 19 | 0.9% | 46 | 2.2% |
| high | 545 | 3 | 0.6% | 12 | 2.2% |

`REPORT.md` also contains signal fire rates and a separate table for
agent-authored pull requests. The `size` signal fires on 75.2% to 91.6% of
pull requests and so separates little.

### Label check

The labels were checked by an AI assistant (Claude, run by the author) reading
commit history and pull request pages on 2026-11-18. The check was not
independently verified by a person. The write-up is
[`backtest/LABEL_CHECK.md`](backtest/LABEL_CHECK.md). All labelled pull requests
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
ones. `--policy FILE` replays a different policy file. The cached clone is
only brought up to date on its default branch; for a clean rerun delete the
cache directory first.

## Limits

- Reverts are rare. Most buckets contain few or no reverts, so the confidence
  intervals are wide and small differences between buckets mean nothing.
- Only merged pull requests are evaluated. Pull requests that were rejected or
  abandoned, possibly because a reviewer saw a problem, are not in the data
  (survivorship).
- No causal claim. A bucket correlating with an outcome would not show that
  review effort at that bucket changes the outcome.
- Revert detection is incomplete. The label check found reverts the rules
  missed in all three repositories (see above).
- Fix follow-up detection is fuzzy: it matches titles and references, and the
  label check found it mostly picks up test and CI repairs and coincidental
  mentions, not production bugs.
- The three samples cover different time spans. pydantic/pydantic reaches back
  several years, home-assistant/core only a recent window of a few weeks. The
  share of agent-authored pull requests in the rows is 1.3% (pydantic/pydantic),
  35.9% (home-assistant/core) and 21.0% (vercel/next.js), so the samples are not
  comparable on that signal.
- Classification is by path name. Known lockfile names (`package-lock.json`,
  `uv.lock`, ...), anything under a `dist`, `build`, `vendor` or `node_modules`
  directory, minified, protobuf and snapshot files (`*.min.js`, `*_pb2.py`,
  `*.snap`, `*.generated.*`), images, fonts, archives and other binaries
  (including `.jar`, `.zip`, `.gz`, `.tar`, `.bin`), and everything under a
  top-level `docs/` directory is treated as lockfile, generated or
  documentation, whatever it contains. A pull request that changes only such
  paths scores 0, whatever its size and even if the path would otherwise be
  sensitive. Auto-approval is blocked for lockfile and generated changes and
  for code files under `docs/`, but the comment itself still reads
  `LOW (0/100)`. In the backtest 23.3% of the pydantic/pydantic rows and 21.0%
  of the vercel/next.js rows have score 0 (not all of them for this reason; see
  [`backtest/LABEL_CHECK.md`](backtest/LABEL_CHECK.md)); 4 of 691 and 5 of 569
  of those were reverted. Future work: treat lockfiles as dependency manifests
  and stop zeroing generated and vendored code. That changes scores, so it
  needs a new weights version and a fresh backtest.
- The built-in sensitive paths are generic. They do not know your repository;
  extend them with `sensitive_paths`.
- Python import resolution matches module suffixes and can count too many
  dependents when module names repeat across packages. For JavaScript and
  TypeScript only relative imports (`./x`, `../x`) are followed; path aliases,
  workspace packages and bare specifiers are not counted, so dependents are
  undercounted in monorepos.
- The dependents count also includes importers of changed non-source files
  (tests, documentation scripts, vendored code) once any changed source file is
  supported.
- `no_tests` treats a pull request that only deletes a test file as having
  changed tests, so source changes that delete their tests do not get that
  signal.
- `unfamiliar` compares commit e-mail addresses. In repositories that squash
  merge, the address on the default branch can differ from the one on the
  branch commits, so the signal fires more often than it should (24.4% to 42.7% of
  pull requests in the report).
- The weights are hand-set priors, not fitted. They were frozen before the
  first run and not tuned afterwards, which is also why they are not good.
- Dependents are not measured for languages other than Python and
  JavaScript/TypeScript.
- All changed paths are passed to git in one call. A pull request with so many
  changed files that the argument list exceeds the operating system limit
  cannot be scored: the action fails (and withdraws its own earlier approval),
  and the backtest skips that pull request.

### Field test (one repository)

The action was tried on one private TypeScript monorepo (an invoicing
application with an `apps/` and `packages/` layout and Drizzle migrations) on
2026-11-18. The analysis was done by an AI assistant on behalf of the author
and was not independently verified by a person. Findings were filed as issues
[#1](https://github.com/Paul1451/review-compass/issues/1) to
[#8](https://github.com/Paul1451/review-compass/issues/8).

The test was 10 local scorings of historical commits and open pull requests,
plus 2 live runs of the action. The reviewer judged 6 of the 10 local results
plausible. One repository, judged by an AI assistant, not verified by a person:
an anecdote, not a measurement.

Known wrong behaviours:

- [#1](https://github.com/Paul1451/review-compass/issues/1): author familiarity
  compares commit e-mail addresses only, so a person who commits with two
  addresses counts as unfamiliar with their own files.
- [#4](https://github.com/Paul1451/review-compass/issues/4): a change that only
  adds a comment line to a source file is scored like a code change (the
  no-tests, dependents and hotspot signals all fire), and generated files and
  large fixtures count towards size.
- [#5](https://github.com/Paul1451/review-compass/issues/5): a sensitive path
  alone adds 25 points and therefore stays LOW, and major version bumps of a
  database driver or runtime by a dependency bot were rated LOW.
- [#7](https://github.com/Paul1451/review-compass/issues/7): the no-tests
  signal is switched off by any changed line in any test file, deleting tests
  is not a signal, and the built-in `auth` pattern also matches test files.
- [#8](https://github.com/Paul1451/review-compass/issues/8): a one-line change
  to a critical file cannot reach HIGH even with a policy file, because the
  sensitive-path signal is a flat 25 points and HIGH starts at 60.
- [#6](https://github.com/Paul1451/review-compass/issues/6): on that
  repository, lines changed alone ranked changes about as well as the full
  score (reported AUC 0.88 for size alone versus 0.84 for the score, 76
  commits, a label built from lines later touched by fix commits; single
  repository, not reproduced in this repository's backtest).

None of these are fixed in the scoring rules yet. The rules are unchanged since
tag `weights-v1`, and any change to them will come with a rerun of the backtest.

## Security notes

- The policy is read from the base commit only. A pull request cannot change
  the rules that score it.
- `.review-compass.yml` and `.github/workflows/**` are guard paths: always
  sensitive, cannot be disabled, always block auto-approval. Auto-approval is
  also blocked for any changed path under `.github/` and for `CODEOWNERS`
  files.
- The workflow uses `pull_request`. `pull_request_target` is never used.
- Code from the pull request is never executed. It is only read through git,
  with argument lists and no shell strings. Revisions are passed after
  `--end-of-options`, so a file named like a commit or branch cannot change
  what git does.
- Model output is text only. It fills the intent check and the reading-list
  notes and cannot change the score, the bucket or the approval decision.
- Text from pull requests (titles, bodies, issues, diffs, file names) is
  untrusted. It is escaped in the comment and in error summaries, and passed
  to the model inside delimited blocks with an instruction to treat it as
  data.
- Fork pull requests are never auto-approved and get no comment or label.
- Fail closed: every run on a same-repository pull request that does not end
  in a fresh approval (not eligible, draft, git error, invalid policy, failed
  write, changed head, unexpected error) tries to dismiss the action's own
  earlier approvals. If that dismissal fails, the job fails and names the
  failure. This does not cover a job that never runs or is cancelled; see the
  branch protection requirement under [Auto-approval](#auto-approval).
- "Own approvals" are identified by the bot login only (default
  `github-actions[bot]`). Approvals given by other workflows under the same
  login are therefore also dismissed by a non-approving run, even when
  `auto_approve` is off.
- Without `ANTHROPIC_API_KEY` no code leaves the runner. With a key, the pull
  request title, body (first 4000 characters), up to three linked issues
  (title and first 4000 characters of the body), the paths of the five
  top-ranked files and their diff are sent to the Anthropic API; the whole
  message is cut to about `llm.max_input_tokens` (4 characters per token).
  Set `llm.enabled: false` in the policy file to prevent this even when a key
  is present. No model call is made for pull requests that change only
  documentation, generated files or lockfiles.

## License

See [LICENSE](LICENSE).
