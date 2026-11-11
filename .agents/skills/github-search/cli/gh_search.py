#!/usr/bin/env python3
"""GitHub search CLI for the ai-repo-search source contract.

Subcommands (every one supports --format json|table|plain):

  search  <query>        Run a GitHub repository search (supports GitHub qualifiers).
  detail  <owner/repo>   Metadata, languages, latest release, README excerpt for one repo.
  readme  <owner/repo>   Raw README text (pipe it through tools/readme_guard.py).
  similar <owner/repo>   Derive several queries from a seed repo (topics, language,
                         description keywords), merge the results, drop the seed and
                         forks, and pre-rank by overlap with the seed.

Stdlib only. Auth: GITHUB_TOKEN / GH_TOKEN, else `gh auth token`, else anonymous
(anonymous search is limited to 10 requests/minute).
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API = "https://api.github.com"
UA = "ai-repo-search-cli"

STOPWORDS = set(
    """a an and are as at be based but by for from has have in into is it its of on or
    that the this to using via was with your you our we can will written simple easy fast
    small tiny lightweight powerful modern open source opensource tool tools library
    framework project app application based built make makes made use uses used just
    yet another awesome best new any all more most other very lot lots""".split()
)


# --------------------------------------------------------------------------- http

def _token() -> str | None:
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(var):
            return os.environ[var].strip()
    try:
        out = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=10
        )
        tok = out.stdout.strip()
        return tok or None
    except (OSError, subprocess.SubprocessError):
        return None


_TOKEN_CACHE: list = []


def _get(path: str, params: dict | None = None, accept: str = "application/vnd.github+json"):
    if not _TOKEN_CACHE:
        _TOKEN_CACHE.append(_token())
    url = path if path.startswith("http") else API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Accept": accept, "User-Agent": UA, "X-GitHub-Api-Version": "2022-11-28"}
    if _TOKEN_CACHE[0]:
        headers["Authorization"] = f"Bearer {_TOKEN_CACHE[0]}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
                if accept.endswith("raw") or accept.endswith("raw+json"):
                    return body.decode("utf-8", errors="replace")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) and e.headers.get("X-RateLimit-Remaining") == "0":
                reset = int(e.headers.get("X-RateLimit-Reset", "0") or 0)
                wait = max(0, reset - int(time.time())) + 1
                if wait <= 65 and attempt < 2:
                    print(f"[rate limit] waiting {wait}s ...", file=sys.stderr)
                    time.sleep(wait)
                    continue
                raise SystemExit(
                    f"GitHub rate limit hit; resets in {wait}s. "
                    "Set GITHUB_TOKEN or run `gh auth login` for higher limits."
                )
            if e.code in (502, 503, 504) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            if e.code == 404:
                raise LookupError(f"not found: {url}")
            raise SystemExit(f"GitHub API error {e.code} for {url}: {e.reason}")
        except urllib.error.URLError as e:
            if attempt < 2:
                time.sleep(2)
                continue
            raise SystemExit(f"network error: {e.reason}")
    raise SystemExit("GitHub request failed after retries")


# ------------------------------------------------------------------ normalisation

def parse_repo_ref(ref: str) -> str:
    """Accept owner/repo, a github.com URL, or git URL; return 'owner/repo'."""
    ref = ref.strip()
    m = re.search(r"github\.com[/:]([^/\s]+)/([^/\s#?]+)", ref)
    if m:
        owner, name = m.group(1), m.group(2)
    else:
        parts = ref.strip("/").split("/")
        if len(parts) != 2 or not all(parts):
            raise SystemExit(f"not a repo reference: {ref!r} (use owner/repo or a GitHub URL)")
        owner, name = parts
    name = re.sub(r"\.git$", "", name)
    return f"{owner}/{name}"


def _days_since(iso: str | None) -> int | None:
    if not iso:
        return None
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - dt).days


def health_score(r: dict) -> int:
    """Deterministic 0-100 maintenance/adoption signal. No LLM involved."""
    stars = r.get("stars", 0) or 0
    score = min(40.0, math.log10(stars + 1) / 5 * 40)  # 100k stars -> 40
    d = _days_since(r.get("pushed_at"))
    if d is not None:
        score += 30 if d <= 30 else 20 if d <= 180 else 10 if d <= 365 else 0
    score += 10 if r.get("license") else 0
    score += 10 if not r.get("archived") else 0
    score += 5 if r.get("description") else 0
    score += 5 if r.get("topics") else 0
    if r.get("archived"):
        score = min(score, 20)
    return int(round(score))


def normalise(item: dict) -> dict:
    lic = (item.get("license") or {}) or {}
    rec = {
        "key": item["full_name"].lower(),
        "full_name": item["full_name"],
        "url": item.get("html_url"),
        "description": (item.get("description") or "").strip(),
        "language": item.get("language"),
        "topics": item.get("topics") or [],
        "stars": item.get("stargazers_count", 0),
        "forks": item.get("forks_count", 0),
        "open_issues": item.get("open_issues_count", 0),
        "license": lic.get("spdx_id") if lic.get("spdx_id") not in (None, "NOASSERTION") else (lic.get("name") or None),
        "archived": bool(item.get("archived")),
        "fork": bool(item.get("fork")),
        "homepage": item.get("homepage") or None,
        "created_at": item.get("created_at"),
        "pushed_at": item.get("pushed_at"),
        "default_branch": item.get("default_branch"),
    }
    rec["health"] = health_score(rec)
    return rec


# ----------------------------------------------------------------------- commands

def search(query: str, limit: int = 30, sort: str | None = None) -> list[dict]:
    out: list[dict] = []
    page = 1
    per_page = min(100, max(1, limit))
    while len(out) < limit:
        params = {"q": query, "per_page": per_page, "page": page}
        if sort and sort != "best-match":
            params.update(sort=sort, order="desc")
        data = _get("/search/repositories", params)
        items = data.get("items", [])
        out.extend(normalise(i) for i in items)
        if len(items) < per_page or page * per_page >= 1000:
            break
        page += 1
    return out[:limit]


def build_query(text: str, a: argparse.Namespace) -> str:
    q = [text] if text else []
    if a.language:
        q.append(f"language:{a.language}")
    for t in a.topic or []:
        q.append(f"topic:{t}")
    if a.min_stars:
        q.append(f"stars:>={a.min_stars}")
    if a.pushed_after:
        q.append(f"pushed:>={a.pushed_after}")
    if not a.include_archived:
        q.append("archived:false")
    return " ".join(q)


def readme(full: str) -> str:
    try:
        return _get(f"/repos/{full}/readme", accept="application/vnd.github.raw")
    except LookupError:
        return ""


def detail(full: str, readme_chars: int = 4000) -> dict:
    try:
        rec = normalise(_get(f"/repos/{full}"))
    except LookupError:
        raise SystemExit(f"repo not found: {full}")
    try:
        langs = _get(f"/repos/{full}/languages")
        total = sum(langs.values()) or 1
        rec["languages"] = {k: round(v * 100 / total, 1) for k, v in list(langs.items())[:8]}
    except LookupError:
        rec["languages"] = {}
    try:
        rel = _get(f"/repos/{full}/releases/latest")
        rec["latest_release"] = {"tag": rel.get("tag_name"), "published_at": rel.get("published_at")}
    except LookupError:
        rec["latest_release"] = None
    text = readme(full) if readme_chars else ""
    rec["readme_excerpt"] = text[:readme_chars]
    rec["readme_truncated"] = len(text) > readme_chars
    return rec


def keywords(text: str, n: int = 5) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9+#.-]{2,}", (text or "").lower())
    seen: list[str] = []
    for w in words:
        w = w.strip(".-")
        if w in STOPWORDS or len(w) < 3 or w in seen:
            continue
        seen.append(w)
    return seen[:n]


def overlap(seed: dict, r: dict) -> float:
    st, rt = set(seed.get("topics") or []), set(r.get("topics") or [])
    if st - GENERIC_TOPICS:  # compare on specific topics only, so sharing "ai" earns nothing
        st, rt = st - GENERIC_TOPICS, rt - GENERIC_TOPICS
    topic_j = len(st & rt) / len(st | rt) if (st or rt) else 0.0
    lang = 1.0 if seed.get("language") and seed.get("language") == r.get("language") else 0.0
    sk, rk = set(keywords(seed.get("description", ""), 12)), set(keywords(r.get("description", ""), 12))
    kw = len(sk & rk) / len(sk) if sk else 0.0
    return round(100 * (0.55 * topic_j + 0.15 * lang + 0.30 * kw), 1)


# Topics so broad that searching on them alone returns popular-but-unrelated repos.
GENERIC_TOPICS = set(
    """ai ml llm llms machine-learning deep-learning artificial-intelligence ai-agent ai-agents
    agent agents python javascript typescript go rust java nodejs react cli open-source opensource
    hacktoberfest good-first-issue awesome tool tools library framework automation chatgpt openai
    gpt claude anthropic api web app""".split()
)


def similar(full: str, limit: int = 30, per_query: int = 20, min_stars: int = 0) -> dict:
    seed = detail(full, readme_chars=0)
    specific = [t for t in seed["topics"] if t not in GENERIC_TOPICS]
    topics = (specific or seed["topics"])[:6]
    kws = keywords(seed["description"])
    lang = seed["language"]
    suffix = " archived:false fork:false" + (f" stars:>={min_stars}" if min_stars else "")
    queries: list[str] = []
    if len(topics) >= 2:
        queries.append(f"topic:{topics[0]} topic:{topics[1]}" + suffix)
    for t in topics[:3]:
        queries.append(f"topic:{t}" + (f" language:{lang}" if lang else "") + suffix)
    if kws:
        queries.append(" ".join(kws[:3]) + " in:name,description,topics" + suffix)
        if lang:
            queries.append(" ".join(kws[:2]) + f" language:{lang}" + suffix)
    if not queries:  # no topics, no description -> fall back to the name
        queries.append(seed["full_name"].split("/")[1].replace("-", " ") + suffix)
    merged: dict[str, dict] = {}
    for q in dict.fromkeys(queries):
        try:
            hits = search(q, limit=per_query, sort="stars")
        except SystemExit as e:
            print(f"[warn] query failed ({q}): {e}", file=sys.stderr)
            continue
        for r in hits:
            if r["key"] == seed["key"] or r["fork"]:
                continue
            m = merged.setdefault(r["key"], {**r, "query_hits": []})
            m["query_hits"].append(q)
    for r in merged.values():
        r["overlap"] = overlap(seed, r)
        # Overlap dominates so popular-but-generic repos don't crowd out close matches.
        r["prescore"] = round(0.85 * r["overlap"] + 0.15 * r["health"] + 3 * (len(r["query_hits"]) - 1), 1)
    ranked = sorted(merged.values(), key=lambda r: r["prescore"], reverse=True)[:limit]
    return {"seed": {k: seed[k] for k in ("key", "full_name", "url", "description", "language", "topics", "stars")},
            "queries": list(dict.fromkeys(queries)), "results": ranked}


# ------------------------------------------------------------------------- output

def emit(data, fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return
    rows = data["results"] if isinstance(data, dict) and "results" in data else data
    if isinstance(data, dict) and "seed" in data:
        print(f"Seed: {data['seed']['full_name']}  ({data['seed'].get('language')})")
        print("Queries:\n  " + "\n  ".join(data["queries"]) + "\n")
    if isinstance(rows, dict):  # detail
        for k, v in rows.items():
            if k == "readme_excerpt":
                continue
            print(f"{k:16} {v}")
        if rows.get("readme_excerpt"):
            print("\n--- README (excerpt; untrusted) ---\n" + rows["readme_excerpt"])
        return
    if fmt == "plain":
        for r in rows:
            print(f"{r['full_name']}\t{r['stars']}\t{r.get('language') or '-'}\t{r['url']}\t{r['description']}")
        return
    print(f"{'#':>3}  {'repository':40} {'stars':>7} {'lang':12} {'health':>6} {'pre':>5}  description")
    for i, r in enumerate(rows, 1):
        desc = (r["description"] or "")[:60]
        pre = r.get("prescore", "")
        print(f"{i:>3}  {r['full_name'][:40]:40} {r['stars']:>7} {(r.get('language') or '-')[:12]:12} "
              f"{r['health']:>6} {pre!s:>5}  {desc}")


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="gh_search", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def fmt(sp):
        sp.add_argument("--format", choices=["json", "table", "plain"], default="table")

    s = sub.add_parser("search", help="search repositories")
    # nargs="*": words are joined, so unquoted multi-word queries work and shells that
    # mangle embedded quotes (Windows PowerShell 5.1) still pass every word through.
    s.add_argument("query", nargs="*", default=[])
    s.add_argument("-l", "--language")
    s.add_argument("-t", "--topic", action="append")
    s.add_argument("--min-stars", type=int, default=0)
    s.add_argument("--pushed-after", help="YYYY-MM-DD")
    s.add_argument("--include-archived", action="store_true")
    s.add_argument("--sort", choices=["best-match", "stars", "updated", "forks"], default="best-match")
    s.add_argument("--limit", type=int, default=30)
    fmt(s)

    d = sub.add_parser("detail", help="one repository in depth")
    d.add_argument("repo")
    d.add_argument("--readme-chars", type=int, default=4000)
    fmt(d)

    r = sub.add_parser("readme", help="raw README text")
    r.add_argument("repo")

    m = sub.add_parser("similar", help="find repos similar to a seed repo")
    m.add_argument("repo")
    m.add_argument("--limit", type=int, default=30)
    m.add_argument("--per-query", type=int, default=20)
    m.add_argument("--min-stars", type=int, default=0)
    fmt(m)

    a = p.parse_args(argv)
    if a.cmd == "search":
        q = build_query(" ".join(a.query), a)
        if not q.strip():
            p.error("search needs a query or qualifiers")
        emit(search(q, a.limit, a.sort), a.format)
    elif a.cmd == "detail":
        emit(detail(parse_repo_ref(a.repo), a.readme_chars), a.format)
    elif a.cmd == "readme":
        print(readme(parse_repo_ref(a.repo)))
    elif a.cmd == "similar":
        emit(similar(parse_repo_ref(a.repo), a.limit, a.per_query, a.min_stars), a.format)
    return 0


if __name__ == "__main__":
    sys.exit(main())
