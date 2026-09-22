# codextender

Run [Claude Code](https://claude.com/claude-code) against your own **OpenAI
ChatGPT/Codex-plan subscription models** — billed against your Codex plan
quota, not per-token API billing — inside Claude Code's own native session.
No separate CLI harness, no rewriting hooks/skills/agents to be
Codex-compatible. Point `claude` at a local proxy and it just works.

Why: if you're on both a Claude Pro/Max plan and a ChatGPT Codex plan, this
effectively doubles your usable agent quota — fall back to your OpenAI
subscription's models when Claude quota runs out, without changing a single
line of your existing Claude Code setup.

## How it works

Claude Code already supports pointing itself at a different backend via
`ANTHROPIC_BASE_URL`. codextender runs a small local proxy — built on top of
[LiteLLM](https://github.com/BerriAI/litellm) — that:

1. Speaks Claude Code's expected Anthropic Messages API on one side.
2. Translates to OpenAI's Responses API on the other side, and sends it to
   the **real internal Codex backend** your `codex` CLI already talks to:
   `https://chatgpt.com/backend-api/codex/responses`.
3. Authenticates using the OAuth credentials your local Codex CLI already
   has on disk (`~/.codex/auth.json`) — no separate API key, no separate
   billing relationship.

That endpoint is deliberately not the public `api.openai.com/v1` — that host
enforces an entitlement check that rejects newer Codex-plan models outright
when called with a generic API client (see below). The ChatGPT-internal host
is what actually accepts your subscription's OAuth token for these models.

## What this repo had to figure out (the annoying parts)

If you're trying to do something similar yourself, these were the
non-obvious blockers, found by testing directly against the live endpoint:

- **Wrong host = hard 400, even with a valid token.** `api.openai.com/v1`
  responds with `"not supported when using Codex with a ChatGPT account"`
  for newer models. The fix isn't a different token or header — it's calling
  `chatgpt.com/backend-api/codex/responses` instead.
- **The endpoint has undocumented required fields.** It 400s unless
  `stream: true` and `store: false` are both set explicitly, and it 400s
  *again* if you send `max_output_tokens`/`max_tokens`/`user` at all
  (LiteLLM's `drop_params: true` alone isn't enough — you need
  `additional_drop_params: [max_output_tokens, max_tokens, user]`).
- **System prompts break the request outright.** Claude Code always requests
  prompt caching, which makes LiteLLM insert a `role: "developer"` item into
  the Responses API `input` array for the system prompt instead of using the
  plain `instructions` field. Codex's dialect 400s on any non-user/assistant
  role there ("System messages are not allowed") — even "developer", unlike
  standard OpenAI Responses API. codextender patches LiteLLM's request
  translator to fold that content into `instructions` instead.
- **Tool-use breaks silently without a patch.** After a tool call, this
  endpoint's `response.completed` event ships an *empty* `output` array
  (unlike standard OpenAI Responses API, which repeats the function-call
  item there). LiteLLM's Anthropic-response adapter infers `stop_reason`
  from that array, so it incorrectly reports `end_turn` instead of
  `tool_use` — which silently breaks Claude Code's agentic tool loop, since
  it never realizes a tool call is pending. codextender patches this at the
  public `__anext__` boundary of LiteLLM's stream wrapper (not by forking
  LiteLLM), correcting `stop_reason` using the same SSE chunk shapes LiteLLM
  already emits.

Both patches work the same way: call LiteLLM's real code unmodified, then
correct the one field that's wrong at a public method boundary — not a fork
of LiteLLM's internals. See `src/codextender/patch.py` for the full writeup
of both.

## Install

```bash
pip install -e .
```

Requires an already-authenticated Codex CLI (`codex login` done at least
once) so `~/.codex/auth.json` exists.

## Usage

```bash
codextender --port 4000
```

Defaults to exposing `gpt-6-sol` as alias `sol`. Pass `--model` (repeatable)
to expose one or more models from the same running proxy — no restart needed
to switch between them, just pick a different `ANTHROPIC_MODEL` per session:

```bash
codextender --port 4000 --model gpt-6-sol:sol --model gpt-6-astra:astra
```

`--model SLUG[:ALIAS]` — alias defaults to the slug itself if omitted. No
model catalog is maintained here on purpose — Codex's model lineup shifts
(this project watched `gpt-5.6-sol` get retired mid-development); pass
whatever slug your account currently has.

Then, in another terminal:

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:4000 \
ANTHROPIC_AUTH_TOKEN=sk-codextender-local \
ANTHROPIC_MODEL=sol \
CODEXTENDER_ACTIVE=1 \
CODEXTENDER_MODEL=sol \
claude
```

Claude Code will now route requests through your Codex-plan subscription's
model for that alias.

The last two vars (`CODEXTENDER_ACTIVE`/`CODEXTENDER_MODEL`) aren't read by
codextender itself — they're a marker for anything downstream that needs to
tell "this session's main model is actually a Codex model, even though the
driving binary is `claude`" (e.g. picking an independent external-review
roster instead of one that would review a GPT-authored diff with another
GPT-family model). Harmless to omit if nothing downstream checks for them;
set `CODEXTENDER_MODEL` to whichever alias you passed as `ANTHROPIC_MODEL`.

## Autostart (Windows)

```powershell
.\scripts\install-windows-autostart.ps1
```

Registers a Scheduled Task that starts the proxy at logon and restarts it if
it crashes (up to 5 times, 1 minute apart). Logs to
`%LOCALAPPDATA%\codextender\logs\codextender.log`. Pass `-ModelArgs`/`-Port`
to change what it exposes — see the script's own comment header
(`Get-Help .\scripts\install-windows-autostart.ps1 -Full`) for all
parameters. To remove: `Unregister-ScheduledTask -TaskName "Codextender"
-Confirm:$false`.

Run this yourself, the same way you run anything else here that touches your
local machine's persistent state or credentials — it isn't something an
agent should register on your behalf.

## Status

Early / personal-use quality, not a hardened production proxy.

**No live token refresh.** `auth.py` has a `refresh_via_app_server()`
function (shells out to `codex app-server` for a fresh OAuth token), but
**it's dead code — nothing calls it.** The CLI has no `--refresh` flag; an
earlier revision of this README wrongly implied one existed. `cli.py` reads
`~/.codex/auth.json` once at startup and keeps that access token for the
life of the process. Practical effect: a long-running proxy (in particular
one started via the Windows autostart script above and left running for
hours) **will start failing every request once the token expires**, with no
automatic recovery — restart the process to pick up a fresh token in the
meantime. Wiring the existing refresh function into an actual reauth path is
open, tracked in `Spec/codextender-integration.md` (`shipwright` monorepo).

**Not implemented, and not needed**: routing Claude tier names
(`opus`/`sonnet`/etc.) through this same proxy to real Anthropic, so a
proxied session's review *subagents* could stay on real Claude while the
main loop uses a Codex model. Turns out unnecessary — Shipwright's own
`"inherit"` model-tier value already gets this for free (a subagent spawned
with no explicit model override just rides whatever backend the parent
session is already using), no proxy changes required. Also would have been
broken as originally conceived here: once `ANTHROPIC_BASE_URL` is
overridden, Claude Code most likely stops using its subscription-linked
OAuth and would need a plain, paid, per-token Anthropic API key instead.

## License

MIT — see [LICENSE](LICENSE). Built on top of
[LiteLLM](https://github.com/BerriAI/litellm) (MIT).
