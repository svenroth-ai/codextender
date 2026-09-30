# codextender

Run [Claude Code](https://claude.com/claude-code) against your own **OpenAI
ChatGPT/Codex-plan subscription models** (billed against your Codex plan
quota, not per-token API billing), inside Claude Code's own native session.
No separate CLI harness, no rewriting hooks/skills/agents to be
Codex-compatible: point `claude` at a local proxy with a few environment
variables.

Why: if you're on both a Claude Pro/Max plan and a ChatGPT Codex plan, your
Codex quota becomes usable from the same Claude Code setup. Fall back to your
OpenAI subscription's models when Claude quota runs out, without changing a
line of your existing setup: hooks, skills, subagents and slash commands work
unchanged.

What you get:

- **One proxy, several Codex models.** Map `opus`, `sonnet` and `haiku` to
  different models, for example `opus` subagents on `astra` and everything
  else on `sol`. Switch per session, no restart (see [Usage](#usage)).
- **Auto mode works.** Claude Code's non-streaming auto-mode classifier
  request is served instead of rejected.
- **Stays logged in.** OAuth tokens refresh on a background timer, including
  after the machine wakes from suspend, not only when a request needs a
  fresh token.
- **Prompt cache stays warm.** A header line Claude Code changes on every
  request is stripped before the system prompt is forwarded, so repeated
  turns keep a stable prefix for Codex's prompt caching.
- **Long sessions.** The real 1'050'000-token context window is reported to
  Claude Code, so it doesn't compact early.

Other local proxies solve the same problem; this isn't the only way to do
it.

## How it works

Claude Code already supports pointing itself at a different backend via
`ANTHROPIC_BASE_URL`. codextender runs a small local proxy, built on top of
[LiteLLM](https://github.com/BerriAI/litellm), that:

1. Speaks Claude Code's expected Anthropic Messages API on one side.
2. Translates to OpenAI's Responses API on the other side, and sends it to
   the **real internal Codex backend** your `codex` CLI already talks to:
   `https://chatgpt.com/backend-api/codex/responses`.
3. Authenticates using the OAuth credentials your local Codex CLI already
   has on disk (`~/.codex/auth.json`); no separate API key, no separate
   billing relationship.

That endpoint is deliberately not the public `api.openai.com/v1`: that host
enforces an entitlement check that rejects newer Codex-plan models outright
when called with a generic API client (see below). The ChatGPT-internal host
is what actually accepts your subscription's OAuth token for these models.

### Staying authenticated

A background thread checks roughly every 30 seconds and refreshes the OAuth
token when the ~20-minute interval elapses, a decoded JWT expiry is
approaching, or the machine looks like it just woke from suspend, then
pushes the new access token straight into the running proxy. A proxy
started once (for example via the Windows autostart script below) keeps
working past the original token's expiry, with no restart needed. A failed
refresh logs a warning and keeps using the existing token rather than
crashing. Disable this with `--no-token-refresh`.

## What this repo had to figure out (the annoying parts)

If you're trying to do something similar yourself, these were the
non-obvious blockers, found by testing directly against the live endpoint:

- **Wrong host = hard 400, even with a valid token.** `api.openai.com/v1`
  responds with `"not supported when using Codex with a ChatGPT account"`
  for newer models. The fix isn't a different token or header: it's calling
  `chatgpt.com/backend-api/codex/responses` instead.
- **The endpoint has undocumented required fields.** It 400s unless
  `stream: true` and `store: false` are both set explicitly, and it 400s
  *again* if you send `max_output_tokens`/`max_tokens`/`user` at all
  (LiteLLM's `drop_params: true` alone isn't enough; you need
  `additional_drop_params: [max_output_tokens, max_tokens, user]`).
- **System prompts break the request outright.** Claude Code always requests
  prompt caching, which makes LiteLLM insert a `role: "developer"` item into
  the Responses API `input` array for the system prompt instead of using the
  plain `instructions` field. Codex's dialect 400s on any non-user/assistant
  role there ("System messages are not allowed"), even "developer", unlike
  standard OpenAI Responses API. codextender patches LiteLLM's request
  translator to fold that content into `instructions` instead.
- **The system prompt itself was quietly busting the prompt cache.** Claude
  Code prepends an `x-anthropic-billing-header:` line to what it sends as
  the system prompt, and part of that line's content changes on every
  single request. Folded verbatim into `instructions`, it meant Codex saw a
  different prefix every turn, so it couldn't reuse anything from previous
  turns. codextender strips that one line before forwarding.
- **Claude's tier aliases fail against a Codex-only proxy.** Claude Code
  resolves the aliases `opus`, `sonnet` and `haiku` (subagent `model:`
  fields, `claude --model`, and internal jobs such as the auto-mode
  classifier) to `claude-*` names and sends those to the proxy, which only
  knows your Codex aliases: `400 Invalid model name`. A subagent with
  `model: opus` fails outright, and a failing classifier request blocks
  every tool call gated by auto mode. Routing those names inside the proxy
  would work but hides which model actually runs. The fix is on the client
  side: set
  `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL` and
  `ANTHROPIC_DEFAULT_HAIKU_MODEL` to your Codex aliases and the proxy never
  sees a `claude-*` name. `CLAUDE_CODE_SUBAGENT_MODEL` doesn't help: a
  subagent's own `model:` field takes precedence over it.
- **Tool-use breaks silently without a patch.** After a tool call, this
  endpoint's `response.completed` event ships an *empty* `output` array
  (unlike standard OpenAI Responses API, which repeats the function-call
  item there). LiteLLM's Anthropic-response adapter infers `stop_reason`
  from that array, so it incorrectly reports `end_turn` instead of
  `tool_use`, which silently breaks Claude Code's agentic tool loop, since
  it never realizes a tool call is pending. codextender patches this at the
  public `__anext__` boundary of LiteLLM's stream wrapper (not by forking
  LiteLLM), correcting `stop_reason` using the same SSE chunk shapes LiteLLM
  already emits.

The patches all work the same way: call LiteLLM's real code unmodified, then
correct the one thing that's wrong at a public method boundary, not a fork
of LiteLLM's internals. See `src/codextender/patch.py` for the full writeup
of all three (stop reason, system prompt role, non-streaming callers).

## Install

Both options need an already-authenticated Codex CLI (`codex login` done at
least once) so `~/.codex/auth.json` exists.

**With npx** (needs Node 20.12+ and Python 3.11+; no manual install):

```bash
npx @svenroth-ai/codextender@latest --port 4000
```

**From source:**

```bash
pip install -e .
codextender --port 4000
```

## Usage

The proxy defaults to exposing `gpt-6.1-sol` as alias `sol`. Pass `--model` (repeatable)
to expose one or more models from the same running proxy: no restart needed
to switch between them, just pick a different `ANTHROPIC_MODEL` per session:

```bash
codextender --port 4000 --model gpt-6.1-sol:sol --model gpt-6-astra:astra
```

`--model SLUG[:ALIAS]`: alias defaults to the slug itself if omitted. No
model catalog is maintained here on purpose, since Codex's model lineup
shifts (this project watched `gpt-5.6-sol` get retired mid-development);
pass whatever slug your account currently has.

Then, in another terminal:

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:4000 \
ANTHROPIC_AUTH_TOKEN=sk-codextender-local \
ANTHROPIC_MODEL=sol \
ANTHROPIC_DEFAULT_OPUS_MODEL=sol \
ANTHROPIC_DEFAULT_SONNET_MODEL=sol \
ANTHROPIC_DEFAULT_HAIKU_MODEL=sol \
CLAUDE_CODE_MAX_CONTEXT_TOKENS=1050000 \
CLAUDE_CODE_AUTO_MODE_SERVER=0 \
CODEXTENDER_ACTIVE=1 \
CODEXTENDER_MODEL=sol \
claude
```

Claude Code will now route requests through your Codex-plan subscription's
model for that alias.

The three `ANTHROPIC_DEFAULT_*_MODEL` vars are required. Claude Code resolves
the aliases `opus`, `sonnet` and `haiku` (used by subagent `model:` fields,
`claude --model`, and internal jobs such as the auto-mode classifier) to
`claude-*` names, and the proxy only serves your Codex aliases, so an
unmapped alias fails with `Invalid model name`. Point each one at an alias
the proxy exposes. If you ran an earlier version without these vars, add
them after upgrading, otherwise `opus`/`sonnet`/`haiku` requests fail. The
400 says which var to set and lists the aliases the proxy serves, so you (or
Claude Code itself) can fix the launch from the error text alone.

With two models running you can split them, for example
`ANTHROPIC_DEFAULT_OPUS_MODEL=astra` and the other two on `sol`:

```bash
codextender --port 4000 --model gpt-6.1-sol:sol --model gpt-6-astra:astra
# then launch claude as above, with ANTHROPIC_DEFAULT_OPUS_MODEL=astra
```

With the [autostart](#autostart-windows) proxy, reinstall it with both
models first: `.\scripts\install-windows-autostart.ps1 -ModelArgs
"gpt-6.1-sol:sol","gpt-6-astra:astra"`, then `.\scripts\start-codextender.ps1`.

Two more vars are recommended: `CLAUDE_CODE_MAX_CONTEXT_TOKENS` (the window
the proxy reports via `GET /v1/models`, so Claude Code doesn't compact
early) and `CLAUDE_CODE_AUTO_MODE_SERVER=0` (hides a cosmetic notice that
otherwise appears on every classifier-gated action for a gateway-routed
session; billing is unaffected).

The last two vars (`CODEXTENDER_ACTIVE`/`CODEXTENDER_MODEL`) aren't read by
codextender itself; they're a marker for anything downstream that needs to
tell "this session's main model is actually a Codex model, even though the
driving binary is `claude`" (e.g. picking an independent external-review
roster instead of one that would review a GPT-authored diff with another
GPT-family model). Harmless to omit if nothing downstream checks for them;
set `CODEXTENDER_MODEL` to whichever alias you passed as `ANTHROPIC_MODEL`.
It describes the main session only, not subagents mapped to another alias.

### For external tooling

The proxy is a stock LiteLLM proxy, so it comes with a couple of endpoints
beyond the Anthropic-Messages translation that's the whole point of this
project, useful for anything that wants to check the proxy is alive or
list what models it's currently serving, without invoking `codex` itself:

- `GET /health/liveliness`: no auth needed, returns a plain `"I'm alive!"`
  200. Use this to check the proxy is up before pointing something at it.
- `GET /v1/models`: **needs** `Authorization: Bearer sk-codextender-local`
  (the same value you pass as `ANTHROPIC_AUTH_TOKEN` above); without it,
  LiteLLM returns a `500` here, not a clean `401`. Easy to misdiagnose as
  "the proxy is broken" if you forget the header. With the header, returns
  the standard OpenAI list shape, `id` being the alias:
  `{"data":[{"id":"sol","object":"model",...}],"object":"list"}`.

## Autostart (Windows)

```powershell
.\scripts\install-windows-autostart.ps1
```

Installs a Startup-folder shortcut (a hidden VBS wrapper, no console window)
that starts the proxy at logon. Logs to
`%LOCALAPPDATA%\codextender\logs\codextender.log`. Pass `-ModelArgs`/`-Port`
to change what it exposes; see the script's own comment header
(`Get-Help .\scripts\install-windows-autostart.ps1 -Full`) for all
parameters. To remove: `.\scripts\install-windows-autostart.ps1 -Uninstall`.

Deliberately not a Scheduled Task: on an AzureAD/corporate-managed machine,
`Register-ScheduledTask` can need elevation just to register, and even once
registered the task can silently fail to actually launch anything
(`LastTaskResult=1`, no process, no log line), while the identical binary
launches fine from an interactive PowerShell session. A Startup-folder entry
runs in that same interactive logon session, so it doesn't hit whatever
policy distinguishes the two. No crash-restart loop yet (the Scheduled Task
version had one, via `-RestartCount`).

Run this yourself, the same way you run anything else here that touches your
local machine's persistent state or credentials; it isn't something an
agent should register on your behalf.

### Manual (re)start

```powershell
.\scripts\start-codextender.ps1
```

Stops any already-running instance, starts a fresh one in the background (no
lingering console; same launcher the autostart entry uses), polls
`/health/liveliness`, and closes its own window after 4s once confirmed up.
Requires `install-windows-autostart.ps1` to have been run at least once,
since it reuses that script's generated launcher rather than duplicating
the port/model flags in a second place. Use this after pulling/editing
source: being an editable install (`pip install -e .`), a restart alone
picks up the change, no rebuild needed.

## Limitations

- Token refresh is timer-based, not reactive. An externally invalidated or
  unexpectedly short-lived token is caught within the next refresh
  interval, not on the first failed request.
- Personal-use quality: single-user, single-machine, not a hardened
  production proxy.

## Documentation

This README covers the "how do I use it" and the annoying implementation
details. For the formal write-up:

- [`docs/requirements.md`](docs/requirements.md): what it must do and why,
  as testable FR/AC pairs.
- [`docs/architecture.md`](docs/architecture.md): how it's built (process
  layout, request flow, token refresh, install paths).
- [`docs/spec.md`](docs/spec.md): the concrete interface contract (CLI
  flags, env vars, HTTP surface, config shape) plus a plain-language
  Business Spec.

## A note on terms of service

codextender reuses your own Codex-plan subscription's OAuth credentials
(the same ones `codex login` already wrote to `~/.codex/auth.json`) from
a third-party client, against the ChatGPT-internal Codex endpoint rather
than a public, versioned API. My understanding is that this is consistent
with how OpenAI has treated subscription-based third-party tooling since
opening Codex to all paid ChatGPT tiers and adding documented OAuth
support for external tools in April 2026 (see
[OpenClaw's OpenAI provider docs](https://docs.openclaw.ai/providers/openai)).
This is my own reading of publicly available information, not a
confirmation from OpenAI, and this area of policy has shifted more than
once in 2026 for both OpenAI and Anthropic. If this understanding is wrong,
I'd rather be corrected than find out via a silent account action.

## License

MIT, see [LICENSE](LICENSE). Built on top of
[LiteLLM](https://github.com/BerriAI/litellm) (MIT).
