#!/usr/bin/env python3
"""State store for discovered repositories (repo_scraper/seen_repos.json) and the
adoption tracker (repo_tracker.csv). All writes to state go through this tool so the
model never has to read or hand-edit the whole state file.

Subcommands:
  ingest     --in FILE|-  [--source LABEL]    add gh_search JSON (search or similar output)
  candidates [--limit 10] [--all] [--focus TEXT] [--seed KEY]
  record     --in FILE|-                      store scorer results, compute weighted totals
  shortlist  [--top 10] [--seed KEY] [--format table|json]
  track      --repo OWNER/NAME --status S [--notes TEXT]
  stats

Statuses: new -> ranked | vetoed | unreachable | gone ; tracker statuses are separate
(starred, trying, adopted, forked, rejected).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.environ.get("REPO_STATE_FILE") or os.path.join(ROOT, "repo_scraper", "seen_repos.json")
TRACKER = os.environ.get("REPO_TRACKER_FILE") or os.path.join(ROOT, "repo_tracker.csv")
TRACKER_FIELDS = ["date", "repo", "url", "seed", "status", "total", "notes"]
TRACKER_STATUSES = ["starred", "trying", "adopted", "forked", "rejected"]

# Weights for the final score. `health` is computed deterministically by gh_search.py;
# the other four come from the repo-scorer agents. Keep in sync with
# .claude/skills/repo-search-assistant/02-evaluation.md.
WEIGHTS = {"purpose": 0.35, "features": 0.25, "stack": 0.15, "docs": 0.10, "health": 0.15}

META_FIELDS = ["full_name", "url", "description", "language", "topics", "stars", "forks",
               "open_issues", "license", "archived", "homepage", "created_at", "pushed_at", "health"]


# --------------------------------------------------------------------------- io

def load() -> dict:
    if not os.path.exists(STATE):
        return {"version": 1, "seeds": {}, "repos": {}}
    with open(STATE, encoding="utf-8") as f:
        return json.load(f)


def save(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(STATE), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE)


def read_input(path: str):
    if path == "-":
        sys.stdin.reconfigure(encoding="utf-8-sig")
        return json.load(sys.stdin)
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def _gh_search():
    import importlib.util
    path = os.path.join(ROOT, ".agents", "skills", "github-search", "cli", "gh_search.py")
    spec = importlib.util.spec_from_file_location("gh_search", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def prescore(st: dict, r: dict, gh=None) -> float:
    """Overlap with the closest seed (or 0 without seeds) blended with health, plus a bonus
    for being found by several queries. Same formula as gh_search.similar."""
    gh = gh or _gh_search()
    ov = max((gh.overlap(s, r) for s in st["seeds"].values()), default=0.0)
    r["overlap"] = ov
    return round(0.85 * ov + 0.15 * (r.get("health") or 0) + 3 * (len(r.get("sources", [])) - 1), 1)


def tracked_keys() -> dict[str, str]:
    if not os.path.exists(TRACKER):
        return {}
    with open(TRACKER, encoding="utf-8", newline="") as f:
        return {row["repo"].lower(): row["status"] for row in csv.DictReader(f) if row.get("repo")}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------------ commands

def cmd_ingest(a) -> dict:
    data = read_input(a.infile)
    seed_key = None
    if isinstance(data, dict) and "results" in data:
        seed = data.get("seed")
        if seed:
            seed_key = seed["key"]
        rows = data["results"]
    elif isinstance(data, list):
        rows = data
    else:
        raise SystemExit("ingest expects gh_search JSON (a list, or an object with 'results')")
    st = load()
    if seed_key:
        st["seeds"][seed_key] = {**{k: seed.get(k) for k in ("full_name", "url", "description", "language", "topics", "stars")},
                                 "queries": data.get("queries", []), "last_run": now()}
    added = updated = skipped = 0
    for r in rows:
        key = r["key"].lower()
        if key in st["seeds"]:
            skipped += 1
            continue
        meta = {k: r.get(k) for k in META_FIELDS}
        src = a.source or (f"similar:{seed_key}" if seed_key else "search")
        if key in st["repos"]:
            e = st["repos"][key]
            e.update(meta)
            e["sources"] = sorted(set(e.get("sources", [])) | {src})
            if seed_key and seed_key not in e.get("seeds", []):
                e.setdefault("seeds", []).append(seed_key)
            e["overlap"] = max(e.get("overlap") or 0, r.get("overlap") or 0)
            e["last_seen"] = now()
            updated += 1
        else:
            st["repos"][key] = {**meta, "status": "new", "sources": [src],
                                "seeds": [seed_key] if seed_key else [],
                                "overlap": r.get("overlap"), "prescore": r.get("prescore"),
                                "first_seen": now(), "last_seen": now()}
            added += 1
    # Re-score every unranked repo against all known seeds so query hits and seed hits
    # are ordered on the same scale (and repeat hits across queries rise).
    gh = _gh_search()
    for e in st["repos"].values():
        if e["status"] in ("new", "unreachable"):
            e["prescore"] = prescore(st, e, gh)
    save(st)
    return {"added": added, "updated": updated, "skipped_seeds": skipped, "total_repos": len(st["repos"])}


def cmd_candidates(a) -> dict:
    st = load()
    if not st["repos"]:
        raise SystemExit("not found: no repos in state yet - run /scrape first")
    tracked = tracked_keys()
    focus = (a.focus or "").lower()
    eligible, excluded = [], 0
    for key, r in st["repos"].items():
        if key in tracked:
            excluded += 1
            continue
        if not a.all and r["status"] not in ("new", "unreachable"):
            continue
        if a.seed and a.seed.lower() not in r.get("seeds", []):
            continue
        if focus:
            hay = " ".join([key, r.get("description") or "", " ".join(r.get("topics") or []), r.get("language") or ""]).lower()
            if focus not in hay:
                continue
        eligible.append((key, r))
    eligible.sort(key=lambda kr: (kr[1].get("prescore") or 0, kr[1].get("health") or 0), reverse=True)
    batch = eligible[: a.limit]
    return {
        "eligible": len(eligible), "selected": len(batch), "deferred": max(0, len(eligible) - a.limit),
        "excluded_by_tracker": excluded,
        "candidates": [{"key": k, "url": r["url"], "description": (r.get("description") or "")[:200],
                        "language": r.get("language"), "topics": r.get("topics"), "stars": r.get("stars"),
                        "license": r.get("license"), "archived": r.get("archived"),
                        "pushed_at": r.get("pushed_at"), "health": r.get("health"),
                        "seeds": r.get("seeds")} for k, r in batch],
    }


def weighted_total(scores: dict, health: int) -> float:
    vals = {**{k: float(scores.get(k, 0) or 0) for k in ("purpose", "features", "stack", "docs")}, "health": float(health or 0)}
    return round(sum(WEIGHTS[k] * max(0.0, min(100.0, v)) for k, v in vals.items()), 1)


def cmd_record(a) -> dict:
    results = read_input(a.infile)
    if isinstance(results, dict):
        results = [results]
    st = load()
    counts = {"ranked": 0, "vetoed": 0, "unreachable": 0, "gone": 0, "unknown_keys": []}
    for res in results:
        key = (res.get("key") or "").lower()
        if key not in st["repos"]:
            counts["unknown_keys"].append(key)
            continue
        e = st["repos"][key]
        status = res.get("status")
        e["guard"] = res.get("guard")
        e["ranked_at"] = now()
        if status == "scored":
            e["scores"] = {k: res.get("scores", {}).get(k) for k in ("purpose", "features", "stack", "docs")}
            e["total"] = weighted_total(e["scores"], e.get("health"))
            e["dealbreaker"] = res.get("dealbreaker", "PASS")
            e["dealbreaker_note"] = res.get("dealbreaker_note")
            e["strengths"] = res.get("strengths", [])[:3]
            e["gaps"] = res.get("gaps", [])[:3]
            e["summary"] = res.get("summary")
            e["status"] = "vetoed" if e["dealbreaker"] == "FAIL" else "ranked"
            counts[e["status"]] += 1
        elif status in ("gone", "unreachable"):
            e["status"] = status
            e["fetch_error"] = res.get("fetch_error")
            counts[status] += 1
        else:
            counts["unknown_keys"].append(f"{key} (bad status {status!r})")
    save(st)
    return counts


def cmd_shortlist(a):
    st = load()
    tracked = tracked_keys()
    rows = [(k, r) for k, r in st["repos"].items()
            if r["status"] == "ranked" and (not a.seed or a.seed.lower() in r.get("seeds", []))]
    rows.sort(key=lambda kr: kr[1].get("total") or 0, reverse=True)
    out = []
    for k, r in rows[: a.top]:
        out.append({"key": k, "url": r["url"], "total": r.get("total"), "scores": r.get("scores"),
                    "health": r.get("health"), "stars": r.get("stars"), "language": r.get("language"),
                    "license": r.get("license"), "dealbreaker": r.get("dealbreaker"),
                    "dealbreaker_note": r.get("dealbreaker_note"), "summary": r.get("summary"),
                    "strengths": r.get("strengths"), "gaps": r.get("gaps"), "guard": r.get("guard"),
                    "tracked": tracked.get(k)})
    return out


def cmd_track(a) -> dict:
    key = a.repo.lower().strip()
    if "github.com/" in key:
        key = "/".join(key.split("github.com/")[1].split("/")[:2])
    st = load()
    e = st["repos"].get(key, {})
    new_file = not os.path.exists(TRACKER)
    with open(TRACKER, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TRACKER_FIELDS)
        if new_file:
            w.writeheader()
        w.writerow({"date": date.today().isoformat(), "repo": e.get("full_name", key),
                    "url": e.get("url", f"https://github.com/{key}"),
                    "seed": ";".join(e.get("seeds", [])), "status": a.status,
                    "total": e.get("total", ""), "notes": a.notes or ""})
    if e:
        e["tracked"] = a.status
        save(st)
    return {"tracked": key, "status": a.status, "in_state": bool(e)}


def cmd_stats(a) -> dict:
    st = load()
    by_status: dict[str, int] = {}
    for r in st["repos"].values():
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    return {"seeds": list(st["seeds"]), "repos": len(st["repos"]), "by_status": by_status,
            "tracked": tracked_keys()}


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("ingest"); s.add_argument("--in", dest="infile", required=True); s.add_argument("--source")
    s = sub.add_parser("candidates"); s.add_argument("--limit", type=int, default=10); s.add_argument("--all", action="store_true")
    s.add_argument("--focus"); s.add_argument("--seed")
    s = sub.add_parser("record"); s.add_argument("--in", dest="infile", required=True)
    s = sub.add_parser("shortlist"); s.add_argument("--top", type=int, default=10); s.add_argument("--seed")
    s.add_argument("--format", choices=["json", "table"], default="table")
    s = sub.add_parser("track"); s.add_argument("--repo", required=True)
    s.add_argument("--status", required=True, choices=TRACKER_STATUSES); s.add_argument("--notes")
    sub.add_parser("stats")
    a = p.parse_args(argv)

    if a.cmd == "shortlist":
        rows = cmd_shortlist(a)
        if a.format == "json":
            print(json.dumps(rows, indent=2, ensure_ascii=False))
        else:
            print(f"{'#':>3}  {'repository':42} {'total':>5} {'purp':>4} {'feat':>4} {'stck':>4} {'docs':>4} {'hlth':>4}  flags")
            for i, r in enumerate(rows, 1):
                s = r["scores"] or {}
                flags = " ".join(x for x in [r["dealbreaker"] if r["dealbreaker"] != "PASS" else "",
                                             "GUARD" if r["guard"] and r["guard"] != "clean" else "",
                                             f"[{r['tracked']}]" if r["tracked"] else ""] if x)
                print(f"{i:>3}  {r['key'][:42]:42} {r['total']:>5} {s.get('purpose', 0):>4} {s.get('features', 0):>4} "
                      f"{s.get('stack', 0):>4} {s.get('docs', 0):>4} {r['health']:>4}  {flags}")
        return 0
    fn = {"ingest": cmd_ingest, "candidates": cmd_candidates, "record": cmd_record,
          "track": cmd_track, "stats": cmd_stats}[a.cmd]
    print(json.dumps(fn(a), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
