# /html-report - Offline Dashboard

```bash
python tools/html_report.py --out reports/repo_report.html --top 20
```

The page is self-contained (no network): stat cards, a bar chart of the top repos by total score, and a sortable, filterable table of every discovered repo. Give the user the printed path and tell them to open it in a browser.
