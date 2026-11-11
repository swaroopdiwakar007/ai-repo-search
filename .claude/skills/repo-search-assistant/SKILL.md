---
name: repo-search-assistant
description: Find, score and compare GitHub repositories similar to a seed repo or matching the user's search profile. Use for /scrape, /rank, /compare and any "find projects like X" request.
---

# Repo Search Assistant

Reference files (read only what the current step needs):

| File | Read when |
|---|---|
| `01-search-profile.md` | Any scoring or comparison - it says what the user wants |
| `02-evaluation.md` | Scoring (`/rank`, `/compare`) - dimensions, anchors, deal-breakers, JSON shape |
| `03-search-queries.md` | Discovery (`/scrape`) - seed repos and saved queries |

Tools:
- Source CLI: `python .agents/skills/github-search/cli/gh_search.py {search|detail|readme|similar}`
- Integrity gate: `python tools/readme_guard.py --text --json`
- State and tracker: `python tools/repo_state.py {ingest|candidates|record|shortlist|track|stats}`
- Dashboard: `python tools/html_report.py`

Pipeline: **profile -> discover (similar + queries) -> ingest/dedupe -> parallel triage scoring -> shortlist -> deep compare (drafter + fresh-context reviewer) -> track -> report.**
