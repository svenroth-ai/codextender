"""Fix two Codex-dialect gaps in LiteLLM's Anthropic<->Responses translation.

Both verified live against the real chatgpt.com/backend-api/codex endpoint
(gpt-6-sol, 2026-09-22):

1. stop_reason (see _patch_stop_reason): LiteLLM's AnthropicResponsesStreamWrapper
   decides `stop_reason` by inspecting the *final* `response.completed` event's
   `response.output` array for a `function_call` item. Codex's Responses API
   dialect leaves that array empty on `response.completed`, even though the
   individual `response.output_item.added`/`.done` events during the stream
   correctly carried `type: "function_call"`. Standard OpenAI Responses API
   providers populate that array; Codex's internal ChatGPT-plan surface does
   not. Net effect, unpatched: a Claude Code session sees `stop_reason:
   "end_turn"` after a tool call and never continues the tool loop.

2. system/developer role items (see _patch_system_role_items): when Claude
   Code's system prompt carries a prompt-cache breakpoint (the normal case),
   LiteLLM inserts a `role: "developer"` message into the Responses API
   `input` array instead of using the plain `instructions` string. Codex's
   dialect 400s on any non-user/assistant role in `input`
   ("System messages are not allowed").

Neither is a LiteLLM bug — both are correct per the documented Responses API
contract that non-Codex providers actually implement. They're Codex-specific
gaps in its own dialect.

Patch strategy for both: black-box, at a public method boundary, not a
reimplementation of LiteLLM's internal translation logic. This survives
internal refactors as long as the wire-format shapes stay the same — which
they must, since those shapes ARE the documented API contracts, not
implementation details.

litellm is pinned to an exact version (see pyproject.toml) specifically so a
future LiteLLM release can't silently change these internals out from under
these patches without a deliberate version bump + re-verification here first.
"""

from __future__ import annotations

import functools
import logging

logger = logging.getLogger("codextender.patch")

_PATCHED_ATTR = "_codextender_patched"


def apply() -> bool:
    """Apply both Codex-compatibility patches. Returns True only if both
    applied — callers should treat a False return as a hard startup error,
    not a silent no-op, since running unpatched means either a broken
    tool-use loop or a hard 400 on every request against Codex.
    """
    return _patch_stop_reason() and _patch_system_role_items()


def _patch_stop_reason() -> bool:
    """Monkeypatch LiteLLM's Anthropic-Responses stream wrapper (see module
    docstring: Codex leaves `response.completed.output` empty, so LiteLLM's
    own stop_reason inference misses tool calls).
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


def _patch_system_role_items() -> bool:
    """Monkeypatch LiteLLM's Anthropic->Responses request translator.

    Root cause (verified live, 2026-09-22): when Claude Code's system prompt
    carries a prompt-cache breakpoint (routine — Claude Code always requests
    caching), LiteLLM's translate_request() inserts a `{"type": "message",
    "role": "developer", ...}` item at the front of `input` instead of using
    the plain top-level `instructions` string it uses otherwise. Codex's
    Responses API dialect 400s on any non-user/assistant role in `input`
    ("System messages are not allowed"), even "developer" — unlike standard
    OpenAI Responses API, which accepts it.

    Patch strategy: black-box, at the public translate_request() boundary.
    We call the original unmodified, then fold any system/developer role
    items in the returned `input` list into the plain `instructions` string
    instead of reimplementing the translator's cache-breakpoint logic.
    """
    try:
        from litellm.llms.anthropic.experimental_pass_through.responses_adapters import (
            transformation as mod,
        )
    except ImportError:
        logger.error(
            "codextender: could not import LiteLLM's Anthropic->Responses "
            "transformation module. Is litellm==1.102.0 installed?"
        )
        return False

    cls = getattr(mod, "LiteLLMAnthropicToResponsesAPIAdapter", None)
    if cls is None:
        logger.error(
            "codextender: LiteLLM no longer exposes "
            "LiteLLMAnthropicToResponsesAPIAdapter in the expected module. "
            "This patch needs updating for your installed litellm version."
        )
        return False

    patched_attr = _PATCHED_ATTR + "_system_role"
    if getattr(cls, patched_attr, False):
        return True

    original_translate_request = cls.translate_request

    @functools.wraps(original_translate_request)
    def patched_translate_request(self, anthropic_request, include_encrypted_reasoning=True):
        kwargs = original_translate_request(self, anthropic_request, include_encrypted_reasoning)
        return _fold_non_user_role_items_into_instructions(kwargs)

    cls.translate_request = patched_translate_request
    setattr(cls, patched_attr, True)
    logger.info(
        "codextender: applied system/developer-role patch to "
        "LiteLLMAnthropicToResponsesAPIAdapter"
    )
    return True


def _fold_non_user_role_items_into_instructions(kwargs: dict) -> dict:
    input_items = kwargs.get("input")
    if not isinstance(input_items, list):
        return kwargs

    remaining: list = []
    extracted_text: list[str] = []
    for item in input_items:
        role = isinstance(item, dict) and item.get("role")
        if role in ("system", "developer"):
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "input_text" and part.get("text"):
                    extracted_text.append(part["text"])
            continue
        remaining.append(item)

    if not extracted_text:
        return kwargs

    kwargs["input"] = remaining
    existing_instructions = kwargs.get("instructions") or ""
    joined = "\n".join(extracted_text)
    kwargs["instructions"] = f"{existing_instructions}\n{joined}" if existing_instructions else joined
    return kwargs


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
