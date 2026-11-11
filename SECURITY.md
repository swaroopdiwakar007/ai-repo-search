# Security model

ai-repo-search reads text written by strangers (READMEs, descriptions, docs) and hands it to an LLM. The defences are layered, and none of them is a sandbox:

1. **Deterministic gate** - `tools/readme_guard.py` strips HTML comments, hidden HTML blocks and zero-width/bidi characters, and flags lines that look like instructions to an AI (override attempts, text addressed to an AI reader, score manipulation, chat-template tokens, exfiltration phrases). Flags are shown to the user; they do not veto a repo. `curl ... | sh` lines are reported as `notes` only, since they are normal in install docs.
2. **Read-only agents** - `repo-scorer` and `repo-reviewer` have no Edit/Write tools, and `.claude/settings.json` limits their Bash to the search CLI and the gate.
3. **Instruction-level rules** - `CLAUDE.md` and every agent prompt state that repository text is data, never instructions; no repo code is ever executed.
4. **No writes to GitHub** - the CLI only issues GET requests. `/track` is local. `gh repo fork`, `gh api` with a method, and `git push` need explicit approval; reading the gh token or `~/.config/gh` is denied to the model (the CLI reads it in-process).
5. **Deterministic numbers** - `health` and the weighted total are computed by code, so a README cannot talk its way into a higher rank beyond the model-scored dimensions.

Report a bypass by opening an issue with a minimal README that gets through the gate.
