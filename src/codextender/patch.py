"""Fix LiteLLM's Anthropic-passthrough stop_reason for Codex's endpoint.

Root cause (verified live against the real chatgpt.com/backend-api/codex
endpoint, gpt-6-sol, 2026-09-22): LiteLLM's AnthropicResponsesStreamWrapper
decides `stop_reason` by inspecting the *final* `response.completed` event's
`response.output` array for a `function_call` item. Codex's Responses API
dialect leaves that array empty on `response.completed`, even though the
individual `response.output_item.added`/`.done` events during the stream
correctly carried `type: "function_call"`. Standard OpenAI Responses API
providers populate that array; Codex's internal ChatGPT-plan surface does
not. Net effect, unpatched: a Claude Code session sees `stop_reason:
"end_turn"` after a tool call and never continues the tool loop.

This is not a LiteLLM bug — it's doing the right thing per the documented
Responses API contract. It's a Codex-specific gap.

Patch strategy: black-box, at the public output boundary (__anext__), not a
reimplementation of LiteLLM's internal event parser. We watch the same
Anthropic-format SSE chunks LiteLLM already emits (content_block_start with
content_block.type == "tool_use") and correct only the one field
(message_delta.delta.stop_reason) if it's wrong. This survives internal
refactors of LiteLLM's event-parsing logic as long as the wire-format chunk
shapes stay the same — which they must, since that shape IS the Anthropic
Messages API contract, not an implementation detail.

litellm is pinned to an exact version (see pyproject.toml) specifically so a
future LiteLLM release can't silently change AnthropicResponsesStreamWrapper
out from under this patch without a deliberate version bump + re-verification
here first.
"""

from __future__ import annotations

import functools
import logging

logger = logging.getLogger("codextender.patch")

_PATCHED_ATTR = "_codextender_patched"


def apply() -> bool:
    """Monkeypatch LiteLLM's Anthropic-Responses stream wrapper. Returns True
    if the patch was applied, False if the target class couldn't be found
    (e.g. a LiteLLM version whose internals moved) — callers should treat a
    False return as a hard startup error, not a silent no-op, since running
    unpatched means tool-use loops will break against Codex.
    """
    try:
        from litellm.llms.anthropic.experimental_pass_through.responses_adapters import (
            streaming_iterator as mod,
        )
    except ImportError:
        logger.error(
            "codextender: could not import LiteLLM's AnthropicResponsesStreamWrapper "
            "module. Is litellm==1.102.0 installed? (pip show litellm)"
        )
        return False

    cls = getattr(mod, "AnthropicResponsesStreamWrapper", None)
    if cls is None:
        logger.error(
            "codextender: LiteLLM no longer exposes AnthropicResponsesStreamWrapper "
            "in the expected module. This patch needs updating for your installed "
            "litellm version."
        )
        return False

    if getattr(cls, _PATCHED_ATTR, False):
        return True  # already patched (e.g. re-entrant import)

    original_anext = cls.__anext__

    @functools.wraps(original_anext)
    async def patched_anext(self):
        chunk = await original_anext(self)
        _track_tool_use(self, chunk)
        _maybe_fix_stop_reason(self, chunk)
        return chunk

    cls.__anext__ = patched_anext
    setattr(cls, _PATCHED_ATTR, True)
    logger.info("codextender: applied stop_reason patch to AnthropicResponsesStreamWrapper")
    return True


def _track_tool_use(instance, chunk: dict) -> None:
    if chunk.get("type") != "content_block_start":
        return
    content_block = chunk.get("content_block") or {}
    if content_block.get("type") == "tool_use":
        instance.__dict__["_codextender_saw_tool_use"] = True


def _maybe_fix_stop_reason(instance, chunk: dict) -> None:
    if chunk.get("type") != "message_delta":
        return
    if not instance.__dict__.get("_codextender_saw_tool_use"):
        return
    delta = chunk.get("delta") or {}
    if delta.get("stop_reason") == "end_turn":
        delta["stop_reason"] = "tool_use"
        logger.debug("codextender: corrected stop_reason end_turn -> tool_use")
