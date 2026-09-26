"""Generate a LiteLLM proxy config pointed at Codex's internal Responses API.

Every field here traces back to something empirically verified live against
the real endpoint (2026-09-22, gpt-6-sol, real ChatGPT-plan subscription) —
see README.md for the walkthrough. In particular:

- api_base must be https://chatgpt.com/backend-api/codex (NOT
  api.openai.com/v1 — that host gets a hard 400 "not supported when using
  Codex with a ChatGPT account" even with a valid, working token).
- The endpoint rejects `max_output_tokens` (which Claude Code's `max_tokens`
  translates to) and `user` (a safety-identifier field LiteLLM passes
  through by default) outright — additional_drop_params silently strips
  both rather than letting every request 400.
- `store: false` and `stream: true` are both hard requirements of this
  specific endpoint, not just recommended defaults. `stream` is forced via
  `extra_body` rather than left to whatever the incoming client request
  asked for: Claude Code's own auto-mode classifier requests (the
  self-issued safety check it falls back to whenever server-side checks
  can't reach a gateway-routed session — see docs/en/auto-mode-classifier-
  billing) are sent with `stream: false`, which this endpoint 400s on
  ("Stream must be set to true") — verified live, 2026-09-26, reproduced
  with a plain non-streaming /v1/messages curl call before the fix and
  confirmed fixed after. Unpatched, this silently blocks every tool call
  gated by auto mode's classifier (e.g. Bash), not just the classifier
  request itself.
- `max_input_tokens` is declared explicitly per model (see `model_info`
  below) rather than left to LiteLLM's own cost-map guess. LiteLLM 1.102.0
  doesn't know the "gpt-6-sol" / "gpt-5.6-sol" slugs at all and falls back
  to fuzzy-matching unrelated entries in its bundled cost map, landing on
  922000 for these models — the same map's `chatgpt/`-prefixed and other
  non-Azure channel entries for this model family consistently report
  1050000 instead, which is what every consumer of this proxy's `/v1/models`
  (e.g. Claude Code's context-window sizing, downstream launchers) should
  see instead of LiteLLM's guess.

Multi-model note: a single running proxy can expose more than one Codex
model alias at once (e.g. `sol` and `astra` simultaneously) — each
`--model slug[:alias]` on the CLI becomes its own `model_list` entry here,
all sharing the same Codex auth/headers/endpoint quirks. A Claude Code
session picks which one it wants per-launch via `ANTHROPIC_MODEL=<alias>`;
the proxy itself doesn't need restarting to switch between them.

NOT implemented here (see README "Status" / Spec/codextender-integration.md
"Open questions"): passing Claude tier names (opus/sonnet/haiku/fable)
through to the real Anthropic API from this same proxy. Once
ANTHROPIC_BASE_URL is overridden, Claude Code most likely stops using its
subscription-linked OAuth and would need a plain ANTHROPIC_AUTH_TOKEN
instead — routing tier calls onward would probably mean a real,
per-token-billed Anthropic API key, which defeats the point for anything
billed against the Max subscription. Left out rather than silently wired to
do that.
"""

from __future__ import annotations

import yaml

CODEX_RESPONSES_API_BASE = "https://chatgpt.com/backend-api/codex"

# Deliberately NOT a hardcoded model catalog — Codex's model lineup has
# already shifted once during this project's own research (5.6-sol retired
# in favor of 6-sol mid-session). Pass whatever slug your account currently
# has via --model; codextender does no validation against a maintained list.

PROXY_MASTER_KEY = "sk-codextender-local"

# Declared context window for this model family (see the module docstring's
# "max_input_tokens" note for why this overrides LiteLLM's own cost-map
# guess). Consumers such as Claude Code's context-window sizing
# (CLAUDE_CODE_MAX_CONTEXT_TOKENS) read this back via GET /v1/models.
MODEL_MAX_INPUT_TOKENS = 1_050_000
MODEL_MAX_OUTPUT_TOKENS = 128_000


def render_config_yaml(
    *,
    models: list[tuple[str, str]],
    api_base: str,
    access_token: str,
    account_id: str | None,
) -> str:
    """``models`` is a list of ``(alias, model_slug)`` pairs — one
    ``model_list`` entry each, all sharing the same Codex credentials/quirks.
    """
    extra_headers = {"originator": "codextender"}
    if account_id:
        extra_headers["chatgpt-account-id"] = account_id

    config = {
        "model_list": [
            {
                "model_name": alias,
                "litellm_params": {
                    "model": f"openai/{model}",
                    "api_base": api_base,
                    "api_key": access_token,
                    "extra_headers": extra_headers,
                    # `stream: True` is forced here, not left to the incoming
                    # client request: Claude Code's auto-mode classifier
                    # requests send `stream: false`, which this endpoint 400s
                    # on ("Stream must be set to true"). See module docstring.
                    "extra_body": {"store": False, "stream": True},
                    "drop_params": True,
                    "additional_drop_params": ["max_output_tokens", "max_tokens", "user"],
                },
                "model_info": {
                    "max_input_tokens": MODEL_MAX_INPUT_TOKENS,
                    "max_output_tokens": MODEL_MAX_OUTPUT_TOKENS,
                },
            }
            for alias, model in models
        ],
        "general_settings": {"master_key": PROXY_MASTER_KEY},
    }
    return yaml.safe_dump(config, sort_keys=False)
