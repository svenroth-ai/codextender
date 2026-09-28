# Spec

See `docs/requirements.md` for *why* each piece exists, and
`docs/architecture.md` for *how* it's built. This document is split into a
**Business Spec** (what you get, in plain terms) and a **Technical Spec**
(the concrete interface contract).

## Business Spec

codextender exists to let you use an existing Codex subscription as the
model behind Claude Code, without changing how Claude Code itself is used
or paying for a second, separate API relationship. Under the hood it's a
small local proxy, built on LiteLLM, that stands in for Anthropic's own
API: Claude Code is pointed at `127.0.0.1` instead of `api.anthropic.com`,
and every request and response is translated transparently in both
directions. A normal turn — including one that calls tools like file edits
or shell commands — carries on correctly instead of stopping early, because
codextender corrects the specific places where Codex's own response format
would otherwise confuse Claude Code (for example, signaling that a tool
call is still in progress rather than that the turn has ended) and adapts
requests that would otherwise fail outright, such as a session with prompt
caching turned on, which is Claude Code's default. It also serves both
streaming and non-streaming callers correctly — not every request Claude
Code itself sends is streamed; its own auto-mode classifier request, for
one, asks for a single, complete answer, and Codex is always talked to as
a stream either way.

There's no new account to create and nothing extra to pay for: codextender
reuses the login the Codex CLI has already established on the machine, and
fails fast with a clear, actionable error — not a stack trace — if that
login isn't set up correctly. Because Codex models support a much larger
conversation history than Claude Code assumes by default, codextender also
tells Claude Code the real, larger limit, so long working sessions aren't
compacted or summarized away earlier than necessary.

The proxy is meant to just be there when you need it. It can expose one
Codex model or several at once, and switching which one a session uses is
a client-side setting, not a proxy restart. It can be installed to start
automatically at Windows logon with no visible console window left open,
and — since it may then run unattended for a long time — its credentials
refresh themselves in the background before they'd otherwise expire,
without interrupting whatever's currently running; a refresh that fails
logs a warning and keeps using the existing token rather than taking the
whole proxy down. After pulling or editing the source, restarting it is a
single command that confirms the new instance is actually reachable before
it finishes, rather than leaving you to guess — though it does need the
autostart install to have been set up once beforehand, since it reuses
that launcher.

Getting started doesn't require cloning a repository or setting up a
Python environment by hand: anyone with the right Node and Python versions
can run it directly via `npx`, and it keeps itself up to date on each run
unless that check is explicitly skipped. For people developing on it, a
plain `pip install -e .` from source works the same way, with both paths
tracing back to the same GitHub repo — there's no separate package to
keep in sync. Either way, other tools — for instance an internal launcher
— can check that the proxy is up and see exactly which models it's
currently serving over plain HTTP, without needing to understand Codex's
own API at all.

For the reasoning behind each of these points, see `docs/requirements.md`.
For the exact flags, variables, and endpoints, see the Technical Spec
below.

## Technical Spec

### CLI (`codextender` / `npx @svenroth-ai/codextender@latest`)

| Flag | Default | Meaning |
|------|---------|---------|
| `--port` | `4000` | Port the proxy listens on. |
| `--model SLUG[:ALIAS]` | `gpt-6-sol:sol` | Repeatable. Codex model slug to expose, optionally aliased. No catalog is maintained — pass whatever slug your account currently has. |
| `--no-token-refresh` | off | Disables the background OAuth refresh thread. |
| `--token-refresh-interval SECONDS` | `1200` (20 min) | Interval the refresh loop targets; mainly for testing. |
| `--no-upgrade` (npm wrapper only) | off | Skip the `pip install --upgrade` freshness check on a run where the venv is already installed. |

### Environment read by codextender itself

| Variable | Meaning |
|----------|---------|
| `CODEX_HOME` | Overrides `~/.codex` as the location of `auth.json`. Optional. |

codextender reads no other environment variables. `ANTHROPIC_*` variables
below are what **Claude Code** reads — codextender never inspects them.

### Environment a caller sets to use the proxy

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:4000 \
ANTHROPIC_AUTH_TOKEN=sk-codextender-local \
ANTHROPIC_MODEL=sol \
CODEXTENDER_ACTIVE=1 \
CODEXTENDER_MODEL=sol \
claude
```

- `ANTHROPIC_AUTH_TOKEN` must equal the proxy's master key
  (`sk-codextender-local`, defined in `config.py::PROXY_MASTER_KEY` —
  hardcoded, not per-install, since the proxy only ever binds `127.0.0.1`).
- `ANTHROPIC_MODEL` must be an alias currently exposed by the running proxy
  (see `--model` above).
- `CODEXTENDER_ACTIVE` / `CODEXTENDER_MODEL` are **not read by codextender
  itself** — a marker convention for downstream tooling (e.g. an external
  code-review roster picker) that needs to know "this session's main model
  is actually a Codex model, even though the driving binary is `claude`".
  Harmless to omit if nothing downstream checks for them.
- Recommended, to suppress a cosmetic Claude Code notice that otherwise
  appears on every classifier-gated action for any gateway-routed session
  (billing is unaffected either way — see
  `code.claude.com/docs/en/auto-mode-classifier-billing`):
  `CLAUDE_CODE_AUTO_MODE_SERVER=0`.
- Recommended, to avoid premature compaction (FR-06):
  `CLAUDE_CODE_MAX_CONTEXT_TOKENS=<max_input_tokens from GET /v1/models>`.

### HTTP surface

codextender is a stock LiteLLM proxy under the hood, patched in-process (see
architecture doc). Three endpoints matter:

#### `POST /v1/messages` — Anthropic Messages API

The whole point of the proxy. Accepts a standard Anthropic Messages request
(`Authorization: Bearer <master key>`), and returns either a standard
`AnthropicMessagesResponse` (non-streaming) or an SSE stream (streaming),
matching what the caller asked for — even though the request to Codex
itself is always sent as a stream either way (see FR-05).

#### `GET /health/liveliness`

No auth. Returns a plain `"I'm alive!"` 200. Use to confirm the proxy is up
before pointing anything at it.

#### `GET /v1/models`

Requires `Authorization: Bearer <master key>` — without it, LiteLLM returns
a bare `500`, not a `401` (a LiteLLM quirk, not a codextender choice — easy
to misdiagnose as "the proxy is broken").

```json
{
  "data": [
    {
      "id": "sol",
      "object": "model",
      "max_input_tokens": 1050000,
      "max_output_tokens": 128000
    }
  ],
  "object": "list"
}
```

`id` is the alias (not the raw Codex slug; defaults to the slug itself if
`--model` was passed without one). One entry per `--model` passed at
startup.

### Config shape (`config.render_config_yaml`, internal)

Not a public interface — no file on disk to hand-edit; regenerated fresh in
a temp directory on every launch. Documented here because it's the one place
every wire-level requirement (FR-01, FR-05, FR-06) is actually declared:

```yaml
model_list:
  - model_name: sol                 # the alias
    litellm_params:
      model: openai/gpt-6-sol       # the real slug
      api_base: https://chatgpt.com/backend-api/codex
      api_key: <codex OAuth access token>
      extra_headers: {originator: codextender, chatgpt-account-id: ...}
      extra_body: {store: false, stream: true}       # FR-01, FR-05
      drop_params: true
      additional_drop_params: [max_output_tokens, max_tokens, user]
    model_info:
      max_input_tokens: 1050000     # FR-06
      max_output_tokens: 128000
  # Same params again for each of these, pointing at the FIRST --model's slug.
  # Not listed by GET /v1/models.
  - model_name: claude-sonnet-*
  - model_name: claude-haiku-*
general_settings:
  master_key: sk-codextender-local
```

### Explicit non-goals

- **No PyPI package.** GitHub is the only distribution channel for the
  Python package; the npm wrapper is the only distribution channel for
  end-users (FR-11).
- **No Claude-tier passthrough.** Routing `opus`/`sonnet`/etc. through this
  same proxy to real Anthropic was considered and dropped — orchestration
  layers that let subagents inherit the parent session's model already get
  equivalent behavior for free, and once `ANTHROPIC_BASE_URL` is
  overridden, Claude Code most likely stops using its subscription-linked
  OAuth anyway (would need a separate, paid, per-token Anthropic API key).
  Exception: `claude-sonnet-*` and `claude-haiku-*` are wildcard-routed to
  the first `--model` (see config shape), because Claude Code sends
  internal requests (auto-mode safety classifier) to those hardcoded names
  and an unrouted name gets a 400, which silently disables the check.
- **No reactive 401 refresh.** Token refresh (FR-08) is poll-driven, not
  triggered by an actual 401 from Codex. Would recover faster from an
  externally-invalidated token; not yet implemented.
- **No hardened production posture.** Single-user, single-machine,
  `127.0.0.1`-only proxy. Not intended to be exposed beyond localhost/a
  Tailscale-style private network the operator already trusts.
</content>
