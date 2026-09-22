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
  specific endpoint, not just recommended defaults.
"""

from __future__ import annotations

import yaml

CODEX_RESPONSES_API_BASE = "https://chatgpt.com/backend-api/codex"

# Deliberately NOT a hardcoded model catalog — Codex's model lineup has
# already shifted once during this project's own research (5.6-sol retired
# in favor of 6-sol mid-session). Pass whatever slug your account currently
# has via --model; codextender does no validation against a maintained list.

PROXY_MASTER_KEY = "sk-codextender-local"


def render_config_yaml(
    *,
    alias: str,
    model: str,
    api_base: str,
    access_token: str,
    account_id: str | None,
) -> str:
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
                    "extra_body": {"store": False},
                    "drop_params": True,
                    "additional_drop_params": ["max_output_tokens", "max_tokens", "user"],
                },
            }
        ],
        "general_settings": {"master_key": PROXY_MASTER_KEY},
    }
    return yaml.safe_dump(config, sort_keys=False)
