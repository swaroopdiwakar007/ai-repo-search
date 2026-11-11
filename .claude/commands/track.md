# /track - Record a Decision About a Repository

Append a row to `repo_tracker.csv`. Local only - never star, fork or comment on GitHub.

`$ARGUMENTS`: `<owner/repo or URL> <status> [notes...]` where status is one of `starred`, `trying`, `adopted`, `forked`, `rejected`.
With no arguments, show the tracker (`python tools/repo_state.py stats`) grouped by status.

```bash
python tools/repo_state.py track --repo <owner/repo> --status <status> --notes "<notes>"
```

If the status is missing or invalid, ask for it. Tracked repos are excluded from future `/rank` runs. Confirm in one line.
