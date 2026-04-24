# CLAUDE.md

Guide for Claude Code working on this repository.

The canonical, tool-agnostic working manual lives in [AGENTS.md](AGENTS.md) —
this file inherits everything from there. Read AGENTS.md first for project
overview, build/test commands, architecture map, conventions, gotchas,
testing guidelines, do/don't rules, external dependencies, and the "how to
add a new captcha type" recipe.

This file only documents Claude Code–specific workflow conventions that
don't belong in AGENTS.md.

## Claude Code Specifics

### Slash commands

- `/clear` — reset the conversation when switching topics or after a long
  session, otherwise the context fills with stale tool output.
- `/compact` — prefer over `/clear` if the prior context is still useful
  but getting large.

### Skills & workflow

- For multi-step work (new pipelines, refactors spanning >2 files),
  invoke `superpowers:brainstorming` first to agree on a design before
  coding. Do not skip brainstorming on the assumption a task is "simple."
- For implementation after a design is approved, use
  `superpowers:writing-plans` → `superpowers:executing-plans`.
- For bug reports, use `superpowers:systematic-debugging`; for any new
  feature / fix, follow `superpowers:test-driven-development`.
- Before claiming work is complete, run `superpowers:verification-before-completion`
  (actually execute the test / lint commands, don't assume).

### Task tracking

Use `TaskCreate` / `TaskUpdate` for anything with 3+ discrete steps.
Mark tasks `in_progress` before starting and `completed` immediately
after — don't batch updates.

### Tool preferences in this repo

- **Read / Edit / Write** for files you intend to change. Do NOT use
  `cat` via Bash for reading.
- **Grep** for content search, **Glob** for file-name search. Avoid
  `find` / raw `grep` via Bash.
- **Bash** is for git, `uv run`, `npm`, process/dir ops only.
- When reading or editing files >50 lines, prefer the context-mode
  execute_file tool if available — the raw Read otherwise floods context.

### PR etiquette

- Create a new commit rather than amending, unless explicitly asked.
- Never `--no-verify` or skip hooks without explicit user approval.
- Never force-push to `main`.
- Commit message trailer is not mandatory, but if you add one, use
  `Co-Authored-By: Claude <noreply@anthropic.com>`.

### Things to ask the user before doing

- Adding a new top-level dependency to `pyproject.toml`
- Introducing a browser-driven path (Selenium / Playwright / etc.) — the
  project is deliberately HTTP-only
- Changing PoW `calc_time` shaping or trajectory parameters (these tune
  against live risk-control signals)
- Deleting or renaming files under `tdc/js/` (tdc.js is vendored
  intentionally)
- Deleting, renaming, or re-quantizing files under
  `src/crack_tcaptcha/solvers/models/` (bundled ONNX models + font are
  force-included into the wheel; a rename means editing `word_ocr.py`
  and `pyproject.toml` in lockstep)

### word_click / serve mode

- Primary `word_click` path is local (YOLO detector + Siamese matcher
  ONNX models under `solvers/models/`). LLM is no longer required.
- When iterating on `solvers/word_ocr.py`, prefer the serve mode to
  avoid per-run ONNX cold-start:
  ```bash
  uv run crack-tcaptcha serve --port 9991 --workers 2
  # then hit POST /solve repeatedly
  ```
- On macOS, if solve feels slow, check provider selection: CoreML EP
  pays a per-process graph-compile cost. Force CPU with
  `TCAPTCHA_ORT_BACKEND=cpu` when benchmarking.
