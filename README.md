# ai-repo-search

An AI agent framework, built on **Claude Code**, for finding GitHub projects similar to the ones you already know - or matching a need you describe - and deciding which are worth your time.

Modelled on [ai-job-search](https://github.com/ai-job-search-dev/ai-job-search): instead of job postings and a candidate profile, it works with repositories and a *search profile*.

```
/setup owner/repo   ->  profile + saved queries
/scrape             ->  similar-repo discovery + query search, deduplicated
/rank               ->  parallel agents score each repo against your profile
/compare <repo>     ->  deep comparison, drafter + fresh-context reviewer
/track <repo> <st>  ->  starred / trying / adopted / forked / rejected
/html-report        ->  offline dashboard
```

## How it works

| Stage | What happens | Where |
|---|---|---|
| Profile | What you want, seed repos, must-haves, stack, licenses, deal-breakers | `.claude/skills/repo-search-assistant/01-search-profile.md` |
| Discover | `similar` turns each seed's topics, language and description keywords into several GitHub searches, merges and pre-ranks by overlap; saved queries run alongside | `.agents/skills/github-search/` |
| Dedupe / state | Every repo gets a stable key and a status (`new -> ranked / vetoed / unreachable / gone`) | `tools/repo_state.py`, `repo_scraper/seen_repos.json` |
| Gate | READMEs are cleaned and scanned for prompt-injection before a model reads them | `tools/readme_guard.py` |
| Triage | `repo-scorer` agents (read-only, ~5 repos each, in parallel) score `purpose`, `features`, `stack`, `docs`; code adds `health` and computes the weighted total | `.claude/commands/rank.md`, `02-evaluation.md` |
| Deep dive | Drafter writes a capability matrix with evidence; `repo-reviewer` re-reads the READMEs and challenges every claim; drafter revises | `.claude/commands/compare.md` |
| Track & report | CSV tracker; self-contained HTML dashboard | `repo_tracker.csv`, `tools/html_report.py` |

### Scoring

| Dimension | Weight | Scored by |
|---|---|---|
| purpose - same problem as the seed / your need | 35% | model |
| features - must-haves with README evidence | 25% | model |
| stack - language, platform, license fit | 15% | model |
| docs - usable from the README alone | 10% | model |
| health - stars (log), push recency, license, not archived | 15% | code |

Deal-breakers (e.g. archived, no license, inactive for 2 years) veto a repo.

## Quick start

Requirements: [Claude Code](https://claude.com/claude-code), Python 3.10+ (stdlib only, no `pip install`), and optionally the GitHub CLI.

```bash
git clone <your fork> ai-repo-search
cd ai-repo-search
gh auth login          # or set GITHUB_TOKEN; anonymous works but search is limited to 10 req/min
claude
```

Then inside Claude Code:

```
/setup langchain-ai/langchain
/scrape
/rank
/compare some-owner/some-repo
```

The tools also work without Claude:

```bash
python .agents/skills/github-search/cli/gh_search.py similar owner/repo --limit 20
python .agents/skills/github-search/cli/gh_search.py search "terminal file manager" -l Rust --min-stars 100 --sort stars
python tools/repo_state.py shortlist --top 10
python tools/html_report.py
```

## Layout

```
CLAUDE.md                              workspace rules
.claude/commands/                      setup, scrape, rank, compare, track, html-report
.claude/agents/                        repo-scorer, repo-reviewer (read-only)
.claude/skills/repo-search-assistant/  profile, evaluation rubric, saved queries
.claude/settings.json                  tool allow / ask / deny lists
.agents/skills/github-search/          source CLI (search, detail, readme, similar)
tools/                                 readme_guard, repo_state, html_report
tests/                                 offline unit tests
```

## Adding a source

Create `.agents/skills/<name>/SKILL.md` with `enabled: true` and a CLI that implements `search`, `detail`, `readme` and `--format json` returning the same record fields (`key`, `full_name`, `url`, `description`, `language`, `topics`, `stars`, `license`, `archived`, `pushed_at`, `health`). Add it to the allow list in `.claude/settings.json` and to `/scrape`. GitLab, Codeberg or Hugging Face Spaces fit this contract.

## Tests

```bash
python -m unittest discover -s tests -v
```

See [SECURITY.md](SECURITY.md) for the threat model. MIT licensed.
