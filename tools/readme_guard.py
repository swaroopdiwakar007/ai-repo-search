#!/usr/bin/env python3
"""Deterministic integrity gate for untrusted repository text (READMEs, descriptions).

Run every README through this before a model scores it. It:
  * strips HTML comments, <details>/<div>/<span> blocks hidden via style or `hidden`,
    zero-width and bidi-control characters, and (with --html) all remaining tags;
  * flags lines that look like instructions aimed at an AI reader (prompt injection),
    e.g. "ignore previous instructions", "rate this repository 10/10", "run curl ... | sh".

A flagged README is still scored - the flag is for the user, not a veto.

Usage:
  python tools/readme_guard.py --in README.md [--html] [--json]
  <cmd> | python tools/readme_guard.py --text [--json]

Without --json: first line is the verdict ("clean" or "FLAGGED: ..."), then the cleaned text.
With --json: {"verdict", "flags": [...], "removed": {...}, "text"}.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

# U+200C/U+200D (ZWNJ/ZWJ) are excluded: they join emoji sequences and Indic/Persian script
# and cannot hide text on their own.
ZERO_WIDTH = re.compile("[​‎‏‪-‮⁠-⁤⁦-⁩﻿]")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
HIDDEN_BLOCK = re.compile(
    r"<(?P<tag>div|span|p|section|details)\b[^>]*(?:\bhidden\b|display\s*:\s*none|visibility\s*:\s*hidden"
    r"|font-size\s*:\s*0|opacity\s*:\s*0)[^>]*>.*?</(?P=tag)>",
    re.S | re.I,
)
TAG = re.compile(r"<[^>]+>")

INJECTION_PATTERNS = [
    (r"\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|earlier|all|your)\b.{0,30}\b(instructions?|prompts?|rules|guidelines)\b", "override-instructions"),
    (r"\byou are (now )?(an? )?(ai|llm|assistant|language model|claude|chatgpt|gpt)\b", "role-reassignment"),
    (r"\b(system|developer)\s*(prompt|message|instructions?)\b", "system-prompt-reference"),
    # Text addressed *to* an AI reader (not merely about AI - READMEs of AI projects say "the AI ..." a lot).
    (r"\b(if you are|dear|attention|note to( the)?|hey)\s+(an?\s+)?(ai|llm|language model|assistant|agent|claude|chatgpt|gpt)\b", "addressed-to-ai"),
    (r"\b(ai|llm|assistant|agent|model)s?\s+(reading|evaluating|reviewing|scoring|ranking|summari[sz]ing|analy[sz]ing)\s+(this|these)\b", "addressed-to-ai"),
    (r"\b(rate|score|rank)\b.{0,40}\b(this|the) (repo|repository|project)\b.{0,40}\b(10|100|highest|top|first|best|perfect)", "score-manipulation"),
    (r"\b(do not|don't|never)\b.{0,30}\b(tell|inform|mention|reveal)\b.{0,20}\b(user|human)\b", "concealment"),
    (r"(curl|wget|iwr|invoke-webrequest)\b[^\n|]*\|\s*(sudo\s+)?(sh|bash|zsh|iex|python)", "pipe-to-shell"),
    (r"<\|?(im_start|im_end|system|endoftext)\|?>|\[/?INST\]|<<SYS>>", "chat-template-token"),
    # Needs a destination ("to <url/server/webhook>") so API docs like "POST /auth/api-key" don't match.
    (r"\b(exfiltrate|send|post|upload|forward|leak)\b.{0,40}\b(tokens?|api[_ -]?keys?|secrets?|credentials?|passwords?|env(ironment)? var\w*|\.env)\b.{0,40}\bto\b.{0,30}(https?://|webhook|server|endpoint|e-?mail|\S+@\S+)", "exfiltration"),
    (r"\b(exfiltrate)\b", "exfiltration"),
]
COMPILED = [(re.compile(p, re.I), name) for p, name in INJECTION_PATTERNS]


def clean(text: str, html: bool = False) -> tuple[str, dict]:
    removed = {"zero_width": 0, "html_comments": 0, "hidden_blocks": 0, "tags": 0}
    text = text.lstrip("﻿")  # a leading BOM (e.g. from shell redirection) is not hiding anything
    removed["zero_width"] = len(ZERO_WIDTH.findall(text))
    text = ZERO_WIDTH.sub("", text)
    removed["html_comments"] = len(HTML_COMMENT.findall(text))
    text = HTML_COMMENT.sub("", text)
    removed["hidden_blocks"] = len(HIDDEN_BLOCK.findall(text))
    text = HIDDEN_BLOCK.sub("", text)
    if html:
        text = re.sub(r"<(script|style)\b.*?</\1>", "", text, flags=re.S | re.I)
        removed["tags"] = len(TAG.findall(text))
        text = TAG.sub(" ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(), removed


def scan(text: str) -> list[dict]:
    flags = []
    for n, line in enumerate(text.splitlines(), 1):
        # Code fences legitimately contain curl|sh install lines; flag them anyway but
        # they are labelled so the user can judge.
        for rx, name in COMPILED:
            if rx.search(line):
                flags.append({"line": n, "pattern": name, "text": line.strip()[:200]})
                break
    return flags


# Patterns that are common in legitimate install docs: reported, but they don't flag the README.
NOTE_ONLY = {"pipe-to-shell"}


def verdict(flags: list[dict], removed: dict) -> str:
    hidden = removed["hidden_blocks"] + removed["zero_width"]
    flags = [f for f in flags if f["pattern"] not in NOTE_ONLY]
    if not flags and not hidden:
        return "clean"
    parts = []
    if flags:
        kinds = sorted({f["pattern"] for f in flags})
        parts.append(f"{len(flags)} suspicious line(s) [{', '.join(kinds)}]")
    if hidden:
        parts.append(f"{hidden} hidden element(s)/char(s) removed")
    return "FLAGGED: " + "; ".join(parts)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--in", dest="infile", help="file to read (default: stdin)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--html", action="store_true", help="input is HTML: strip all tags")
    g.add_argument("--text", action="store_true", help="input is markdown/plain text (default)")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    if a.infile:
        with open(a.infile, encoding="utf-8", errors="replace") as f:
            raw = f.read()
    else:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        raw = sys.stdin.read()
    text, removed = clean(raw, a.html)
    flags = scan(text)
    v = verdict(flags, removed)
    if a.json:
        notes = [f for f in flags if f["pattern"] in NOTE_ONLY]
        flags = [f for f in flags if f["pattern"] not in NOTE_ONLY]
        print(json.dumps({"verdict": v, "flags": flags, "notes": notes, "removed": removed, "text": text},
                         ensure_ascii=False, indent=2))
    else:
        print(v)
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
