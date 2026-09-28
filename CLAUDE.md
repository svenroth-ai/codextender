# CLAUDE.md

Project-specific instructions for codextender. These extend, not replace,
the global instructions.

## What and how

- **Stack**: Python 3.11+, a LiteLLM proxy (pinned exact version) with
  three targeted monkeypatches. Windows PowerShell for autostart/restart.
  An npm wrapper (`npm/`) for distribution.
- **Purpose**: run Claude Code against a Codex-plan subscription's models
  via a local Anthropic-compatible proxy. Full pitch in README.md.
- **Install**: `pip install -e .`
- **Run**: `codextender --port 4000`
- **Test**: no automated test suite exists. See "Testing and verification"
  below for how to actually verify a change.

## Language and style

- Code and documentation in English, regardless of what language we're
  discussing in.
- No em dashes anywhere: code, comments, docs, commit messages. Use a
  period, comma, colon, or semicolon instead.
- Numbers in Swiss notation: apostrophe as thousands separator, period as
  decimal separator (e.g. 1'050'000, not 1,050,000 or 1.050.000).
- Clear and easy to understand for tech-savvy users. No AI-jargon or hype
  language in user-facing docs (README, docs/).

## Working method

- Read the relevant docs (docs/requirements.md, docs/architecture.md,
  docs/spec.md) before changing behavior, not just the code, so the
  change matches existing intent instead of guessing at it.
- Verify against the real thing, not by inference: run the proxy and send
  a real request, read the actual source of whatever you're patching, or
  fetch a live endpoint. Don't rely on memory or what "should" be true.
- Keep changes at the scope asked. Models in this family (Sonnet 5, Opus
  5.5) tend to widen scope on their own initiative; when a task is narrow,
  do exactly that and stop, rather than adding unrequested steps.
- Before calling a non-trivial change done, have a fresh-context Opus
  subagent review it for correctness gaps, not style preferences. A
  reviewer asked to find something will usually find something, so weigh
  a finding against whether it would actually break behavior before
  acting on it.
- Delegate to a subagent only for genuinely independent work (a broad
  investigation, an external-policy question, a fresh-context review).
  Don't spawn a subagent to verify code you just wrote yourself in the
  same turn; check it directly instead.

## Documentation

- docs/requirements.md, docs/architecture.md, docs/spec.md are the source
  of truth for why/how/what. The README stays a fast, motivating entry
  point; link to docs/ for detail instead of duplicating it.
- Update the relevant doc(s) whenever behavior, defaults, endpoints, or
  install steps change, as part of the same change, not as a follow-up.
- Docs describe current behavior, not history. No dates, no "just fixed
  X" narrative; git log already has the history.
- Match length to what the task needs: cover the substance, don't pad
  with filler sections or redundant summaries.

## Patches to LiteLLM

- Any new patch in src/codextender/patch.py follows the existing pattern:
  call LiteLLM's real code unmodified, then correct the one thing that's
  wrong at a public method boundary. Never fork LiteLLM internals.
- litellm is pinned to an exact version in pyproject.toml on purpose. This
  looks like something that should be relaxed or upgraded; it is not. A
  version bump is a deliberate, separate change that re-verifies every
  patch still applies. If the pin looks wrong, raise it, don't silently
  loosen it.

## Testing and verification

- There is no automated test suite in this repo. Verification means
  actually running codextender and checking real behavior: start the
  proxy, send a request (curl or a real Claude Code session), and read
  the response, not just the code that should produce it.
- After touching a *.ps1 script, check it against the Startup-folder-not-
  Scheduled-Task constraint in docs/architecture.md; that constraint was
  hit and fixed once already for a real reason.

## Keeping this file useful

- CLAUDE.md loads into every session, so every line costs context on
  every turn. Before adding a line, ask: would removing it cause a
  mistake? If not, cut it.
- Prefer editing an existing line over adding a new one.
- A rule that needs more than two or three lines to justify belongs in
  docs/, not here; add one line plus a pointer to the doc instead.
