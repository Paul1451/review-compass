# Review Compass backtest

## pydantic/pydantic

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

Signal fire rates:

- sensitive_path: 21.4%
- no_tests: 24.5%
- dependents: 56.0%
- size: 75.2%
- hotspot: 61.4%
- unfamiliar: 24.4%
- spread: 23.2%
- agent: 1.1%

### Agent-authored PRs (39)

| Bucket | PRs | Share | Reverted (95% CI) | Fix follow-up (95% CI) | Median review threads | Median hours to merge | Changes requested |
|---|---:|---:|---|---|---:|---:|---:|
| low | 25 | 64.1% | 0.0% (0.0% to 13.3%) | 0.0% (0.0% to 13.3%) | 0.0 | 3.3 | 0.0% |
| medium | 14 | 35.9% | 7.1% (1.3% to 31.5%) | 7.1% (1.3% to 31.5%) | 0.0 | 5.6 | 0.0% |
| high | 0 | 0.0% | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) | 0.0 | 0.0 | 0.0% |

Skimming low-risk PRs would cover 64.1% of PRs and contain 0 of 1 reverted PRs.

Reverted rate, high bucket divided by low bucket: n/a

Policy fingerprint: eea628015697
Tool version: 0.1.0
Generated: 2026-11-18

## home-assistant/core

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

Signal fire rates:

- sensitive_path: 15.9%
- no_tests: 19.8%
- dependents: 48.5%
- size: 91.6%
- hotspot: 65.4%
- unfamiliar: 42.7%
- spread: 23.7%
- agent: 35.9%

### Agent-authored PRs (498)

| Bucket | PRs | Share | Reverted (95% CI) | Fix follow-up (95% CI) | Median review threads | Median hours to merge | Changes requested |
|---|---:|---:|---|---|---:|---:|---:|
| low | 309 | 62.0% | 0.0% (0.0% to 1.2%) | 1.3% (0.5% to 3.3%) | 0.0 | 4.7 | 3.6% |
| medium | 182 | 36.5% | 0.0% (0.0% to 2.1%) | 1.1% (0.3% to 3.9%) | 0.0 | 1.0 | 1.6% |
| high | 7 | 1.4% | 0.0% (0.0% to 35.4%) | 0.0% (0.0% to 35.4%) | 4.0 | 5.5 | 42.9% |

Skimming low-risk PRs would cover 62.0% of PRs and contain 0 of 0 reverted PRs.

Reverted rate, high bucket divided by low bucket: n/a

Policy fingerprint: eea628015697
Tool version: 0.1.0
Generated: 2026-11-18

## vercel/next.js

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

Signal fire rates:

- sensitive_path: 20.5%
- no_tests: 30.4%
- dependents: 39.6%
- size: 75.4%
- hotspot: 64.1%
- unfamiliar: 36.6%
- spread: 45.8%
- agent: 18.2%

### Agent-authored PRs (569)

| Bucket | PRs | Share | Reverted (95% CI) | Fix follow-up (95% CI) | Median review threads | Median hours to merge | Changes requested |
|---|---:|---:|---|---|---:|---:|---:|
| low | 266 | 46.7% | 0.0% (0.0% to 1.4%) | 0.8% (0.2% to 2.7%) | 0.0 | 18.1 | 0.4% |
| medium | 227 | 39.9% | 1.3% (0.5% to 3.8%) | 6.2% (3.7% to 10.1%) | 0.0 | 43.9 | 1.3% |
| high | 76 | 13.4% | 1.3% (0.2% to 7.1%) | 7.9% (3.7% to 16.2%) | 1.0 | 95.4 | 6.6% |

Skimming low-risk PRs would cover 46.7% of PRs and contain 0 of 4 reverted PRs.

Reverted rate, high bucket divided by low bucket: n/a

Policy fingerprint: eea628015697
Tool version: 0.1.0
Generated: 2026-11-18
