# /scrape - Discover Candidate Repositories

Collect repositories from every enabled source and store them in `repo_scraper/seen_repos.json` (deduplicated). `/scrape` only discovers; scoring happens in `/rank`.

`$ARGUMENTS` may contain:
- nothing -> every seed in the profile + every saved query
- `owner/repo` (or a GitHub URL) -> run `similar` for that seed only
- quoted free text -> run that one search query only
- `--limit <N>` -> results per query / per seed (default 25)
- `--min-stars <N>` -> override the default from `03-search-queries.md`

> **Windows:** run tools as `python`, not `python3`.

## Step 1: Load configuration

Read `.claude/skills/repo-search-assistant/01-search-profile.md` (only the *Seed repositories* section) and `03-search-queries.md`. Discover sources with Glob on `.agents/skills/*/SKILL.md` and skip any with `enabled: false`.

If the profile still holds the example content and no argument was given, ask whether to run `/setup` first.

## Step 2: Run discovery

Create `repo_scraper/tmp/` if missing. For each seed:

```bash
python .agents/skills/github-search/cli/gh_search.py similar <seed> --limit 25 --min-stars 10 --format json > repo_scraper/tmp/similar_<n>.json
python tools/repo_state.py ingest --in repo_scraper/tmp/similar_<n>.json
```

For each saved query:

```bash
python .agents/skills/github-search/cli/gh_search.py search "<query>" --min-stars 10 --pushed-after <date> --limit 25 --format json > repo_scraper/tmp/query_<n>.json
python tools/repo_state.py ingest --in repo_scraper/tmp/query_<n>.json --source "query:<query>"
```

Do not print the JSON files into the conversation; `ingest` returns the counts you need. If a source reports a rate limit, stop that source, keep what was ingested, and tell the user how to authenticate (`gh auth login` or `GITHUB_TOKEN`).

## Step 3: Report

```bash
python tools/repo_state.py stats
```

Tell the user: how many repos were added vs already known, per seed and per query, and how many are now waiting for `/rank`. Show the top 5 new repos by `prescore` from `python tools/repo_state.py candidates --limit 5` (name, stars, one-line description). Suggest `/rank` next.
