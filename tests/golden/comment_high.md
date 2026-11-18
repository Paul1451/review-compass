<!-- review-compass -->
## Review Compass: HIGH (72/100)

**Why**
- touches sensitive path: auth/session.py (+25)
- source changed, no test changes (+18)
- 12 files depend on changed modules (+15)
- hotspot: auth/session.py changed 9x in 90 days (+9)
- spans 6 directories (+5)

**Intent check**
Issue \#31 asks for a logout fix. This PR also changes token lifetime.

**Read first**
1. `auth/session.py:88-140` — session invalidation rewritten
2. `api/login.py:12-30` — caller of changed function

**Skim (9 files)**
`d1.md`, `d2.md`, `d3.md`, `d4.md`, `d5.md`, `d6.md`, `d7.md`, `d8.md`, …and 1 more
