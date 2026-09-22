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

If you're trying to do something similar yourself, these were the three
non-obvious blockers, found by testing directly against the live endpoint:

- **Wrong host = hard 400, even with a valid token.** `api.openai.com/v1`
  responds with `"not supported when using Codex with a ChatGPT account"`
  for newer models. The fix isn't a different token or header — it's calling
  `chatgpt.com/backend-api/codex/responses` instead.
- **The endpoint has undocumented required fields.** It 400s unless
  `stream: true` and `store: false` are both set explicitly, and it 400s
  *again* if you send `max_output_tokens`/`max_tokens` at all (LiteLLM's
  `drop_params: true` alone isn't enough — you need
  `additional_drop_params: [max_output_tokens, max_tokens]`).
- **Tool-use breaks silently without a patch.** After a tool call, this
  endpoint's `response.completed` event ships an *empty* `output` array
  (unlike standard OpenAI Responses API, which repeats the function-call
  item there). LiteLLM's Anthropic-response adapter infers `stop_reason`
  from that array, so it incorrectly reports `end_turn` instead of
  `tool_use` — which silently breaks Claude Code's agentic tool loop, since
  it never realizes a tool call is pending. codextender patches this at the
  public `__anext__` boundary of LiteLLM's stream wrapper (not by forking
  LiteLLM), correcting `stop_reason` using the same SSE chunk shapes LiteLLM
  already emits. See `src/codextender/patch.py` for the full writeup.

## Install

```bash
pip install -e .
```

Requires an already-authenticated Codex CLI (`codex login` done at least
once) so `~/.codex/auth.json` exists.

## Usage

```bash
codextender --model gpt-6-sol --alias sol --port 4000
```

Then, in another terminal:

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:4000 \
ANTHROPIC_AUTH_TOKEN=sk-codextender-local \
claude
```

Claude Code will now route requests through your Codex-plan subscription's
`gpt-6-sol` model. No model slug is hardcoded as a maintained catalog —
Codex's model lineup shifts (this project watched `gpt-5.6-sol` get retired
mid-development); pass whatever slug your account currently has via
`--model`.

## Status

Early / personal-use quality. The `--refresh` token-refresh path (shelling
out to `codex app-server` for a fresh OAuth token) is implemented but not
yet exercised against every Codex CLI version. Treat this as a working proof
of concept, not a hardened production proxy.

## License

MIT — see [LICENSE](LICENSE). Built on top of
[LiteLLM](https://github.com/BerriAI/litellm) (MIT).
