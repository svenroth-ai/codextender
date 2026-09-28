# Requirements

## What this is

codextender lets you use an existing Codex subscription (ChatGPT Plus/Pro's
Codex access) as the model behind Claude Code, instead of paying separately
for Anthropic API usage. It runs as a small local proxy — built on top of
LiteLLM — that speaks the same API Claude Code already expects on one side,
and Codex's own API on the other, so Claude Code works normally once it's
pointed at `127.0.0.1` instead of Anthropic. The goal is simple: reuse a
subscription you already have, with no separate login, billing relationship,
or change to how Claude Code is used day to day.

For the exact CLI flags, environment variables, and HTTP contract this
implies, see `docs/spec.md`.

---

Lightweight requirements list (FR/AC format, scaled down for a
single-maintainer tool). Each requirement has an ID, a one-line
statement, and acceptance criteria that describe observable behavior, not
implementation.

| ID | Title |
|----|-------|
| [FR-01](#fr-01-anthropic-to-codex-translation) | Anthropic-to-Codex request/response translation |
| [FR-02](#fr-02-reuse-codex-cli-credentials) | Reuse Codex CLI credentials |
| [FR-03](#fr-03-correct-tool-use-stop_reason) | Correct tool-use `stop_reason` |
| [FR-04](#fr-04-fold-system-prompt-into-instructions) | Fold system prompt into `instructions` |
| [FR-05](#fr-05-serve-non-streaming-callers) | Serve non-streaming callers |
| [FR-06](#fr-06-declare-the-real-context-window) | Declare the real context window |
| [FR-07](#fr-07-serve-multiple-models-from-one-proxy) | Serve multiple models from one proxy |
| [FR-08](#fr-08-background-oauth-token-refresh) | Background OAuth token refresh |
| [FR-09](#fr-09-windows-autostart) | Windows autostart |
| [FR-10](#fr-10-manual-restart) | Manual restart |
| [FR-11](#fr-11-npx-distribution) | npx distribution |
| [FR-12](#fr-12-liveness-and-model-discovery-for-external-tooling) | Liveness + model discovery for external tooling |

---

## FR-01: Anthropic-to-Codex translation

Claude Code needs to work exactly as if it were talking to Anthropic —
nothing about how it's configured or used should change. codextender
presents that same interface locally and transparently translates every
request and response to and from Codex's backend behind the scenes.

**Acceptance criteria**
- Given `ANTHROPIC_BASE_URL` points at a running codextender instance, a
  normal Claude Code turn (text only, no tools) completes successfully.
- The public `api.openai.com/v1` host is never used — only
  `chatgpt.com/backend-api/codex/responses`.

## FR-02: Reuse Codex CLI credentials

Users shouldn't need a separate login or a second billing relationship just
to use their Codex subscription this way. codextender reuses the same login
the Codex CLI has already established on the machine.

**Acceptance criteria**
- Startup fails with a clear, actionable error if `auth.json` is missing or
  not in `chatgpt`-OAuth mode (not a stack trace).
- No separate API key or billing relationship is ever created.

## FR-03: Correct tool-use `stop_reason`

Claude Code needs to know when a turn ended because it should keep working
(e.g. a file edit or command is still in progress) versus when it's truly
done, so multi-step tasks complete correctly instead of stopping early.

**Acceptance criteria**
- After a tool call, the response Claude Code receives carries
  `stop_reason: tool_use`, not `end_turn`.
- A turn with no tool call is unaffected (`stop_reason` unchanged).

## FR-04: Fold system prompt into `instructions`

A standard Claude Code session, with its default settings, including
prompt caching, must work out of the box, without the user needing to
change any configuration to accommodate this proxy.

**Acceptance criteria**
- A session with prompt caching enabled (the default) does not 400 on the
  first turn.
- The system prompt's content still reaches the model (folded into
  `instructions`), not silently dropped.
- Claude Code's `x-anthropic-billing-header:` line, whose content changes
  on every request, is stripped out of the folded text rather than passed
  through, so repeated turns present a stable prefix instead of one that
  changes every time and defeats prefix-based prompt caching on Codex's
  backend.

## FR-05: Serve non-streaming callers

Not every request Claude Code itself sends is streamed — its own auto-mode
classifier request, for one, asks for a single, complete answer instead.
Both styles of caller need to keep working.

**Acceptance criteria**
- A non-streaming `/v1/messages` call against codextender returns a normal,
  complete `AnthropicMessagesResponse` JSON object (not a 400, not a raw SSE
  stream).
- A streaming call is unaffected — still returns SSE, unchanged shape.
- Both text and tool-use responses aggregate correctly for a non-streaming
  caller (text content and `tool_use` blocks with parsed `input`).

## FR-06: Declare the real context window

Codex models support a much larger conversation history than Claude Code
assumes by default. Without correcting that assumption, Claude Code
compacts and summarizes conversations far earlier than necessary, losing
context the model could actually still use.

**Acceptance criteria**
- `GET /v1/models` reports `max_input_tokens`/`max_output_tokens` matching
  the real Codex model window, not LiteLLM's own cost-map guess for an
  unrecognized model slug.
- The declared numbers are a named constant in one place (`config.py`), not
  duplicated elsewhere in this repo.

## FR-07: Serve multiple models from one proxy

A user may want access to more than one Codex model variant without running
several proxies side by side, and switching between them should be
effortless.

**Acceptance criteria**
- `--model slug[:alias]` is repeatable on the CLI; each becomes its own
  `model_list` entry.
- Switching which model a session uses is a client-side `ANTHROPIC_MODEL`
  choice — it never requires restarting the proxy.
- Requests for `claude-sonnet-*` and `claude-haiku-*` are answered by the
  first `--model` instead of failing. Claude Code sends internal requests
  (such as the auto-mode safety check) to those hardcoded names regardless
  of `ANTHROPIC_MODEL`; a 400 there silently disables the check.

## FR-08: Background OAuth token refresh

A proxy that's left running for a long time — for instance, started
automatically at login — must keep working past the point where its
original credentials would normally expire, with no manual restart and no
interruption to the user.

**Acceptance criteria**
- A proxy left running past its baked-in token's expiry continues serving
  requests successfully (refresh happens transparently).
- A refresh failure (codex not on PATH, app-server unreachable) logs a
  warning and keeps using the existing token — it never crashes the proxy.
- Enabled by default; `--no-token-refresh` turns it off.

## FR-09: Windows autostart

For everyday use, the proxy should simply be available whenever the user
logs into Windows, with no manual step and nothing cluttering the desktop —
and it should be just as easy to remove again.

**Acceptance criteria**
- `install-windows-autostart.ps1` installs a Startup-folder entry (not a
  Scheduled Task — see `docs/architecture.md` for why).
- The proxy is reachable at `/health/liveliness` shortly after logon, with
  no console window left open.
- `-Uninstall` removes it cleanly.

## FR-10: Manual restart

After pulling or editing the source, restarting the proxy should be one
simple, reliable command — not something that requires knowing how it was
originally launched.

**Acceptance criteria**
- `start-codextender.ps1` stops any already-running instance, starts a
  fresh one, and confirms it's up via `/health/liveliness` before exiting.
- Requires no separate rebuild step (editable install).
- Requires `install-windows-autostart.ps1` (FR-09) to have been run at
  least once beforehand — it generates the launcher this script reuses, so
  the launch command/port/model flags stay in exactly one place.

## FR-11: npx distribution

Getting started shouldn't require cloning a repository or manually managing
a Python environment. Anyone with the right Node and Python versions should
be able to just run codextender.

**Acceptance criteria**
- `npx @svenroth-ai/codextender@latest <args>` finds/validates a Python
  3.11+ interpreter, creates an isolated venv at `~/.codextender/venv` if
  missing, installs/upgrades codextender into it from GitHub, and execs the
  real `codextender` binary with all arguments passed through unchanged.
- `--no-upgrade` skips the update check on a run where the venv already has
  an install.
- No PyPI package exists or is required — GitHub is the only source.

## FR-12: Liveness and model discovery for external tooling

Other tools that want to use this proxy — for example an internal launcher
— need a simple, reliable way to check that it's running and see which
models it currently offers, without having to talk to Codex directly.

**Acceptance criteria**
- `GET /health/liveliness` requires no auth and returns 200 when the proxy
  is up.
- `GET /v1/models` (with the master-key bearer token) returns the standard
  OpenAI list shape, one entry per currently-exposed alias, including
  `max_input_tokens`/`max_output_tokens` (see FR-06).
</content>
</invoke>
