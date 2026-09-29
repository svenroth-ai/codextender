"""Fix three Codex-dialect gaps in LiteLLM's Anthropic<->Responses translation.

All verified live against the real chatgpt.com/backend-api/codex endpoint
(gpt-6-sol, 2026-09-22 for the first two, 2026-09-26 for the third):

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
   ("System messages are not allowed"). While folding that content into
   `instructions`, we also drop Claude Code's `x-anthropic-billing-header:`
   line (see _strip_billing_header_line): its content changes on every
   request, so leaving it in would put a different prefix in front of Codex
   on every turn and defeat any prefix-based prompt caching on the backend.

3. Non-streaming requests (see _patch_force_streaming): Codex's endpoint hard-
   requires `stream: true` in the wire body, rejecting anything else with a
   400 ("Stream must be set to true") -- config.py's `extra_body` forces that
   at the wire level, but LiteLLM's OWN Python-level branch on whether to
   expect a plain `ResponsesAPIResponse` or a streaming iterator is driven
   separately, by the *caller's* original `stream` argument (i.e. whatever
   the client's own /v1/messages request said), not by extra_body. A caller
   that asks for `stream: false` -- which config.py's forced wire-level
   `stream: true` has nothing to do with -- still gets back a
   ResponsesAPIStreamingIterator instead of the ResponsesAPIResponse LiteLLM's
   own non-streaming code path expects, and it raises. Concretely, this is
   Claude Code's own auto-mode classifier request (see auto-mode-classifier-
   billing docs): when server-side classifier checks can't reach a gateway-
   routed session, Claude Code falls back to a self-issued, non-streaming
   classifier call over the SAME connection, and unpatched, every one of
   those calls breaks -- silently blocking every tool call gated by auto
   mode (Bash included), not just the classifier request itself.

None of the three is a LiteLLM bug -- all are correct per the documented
Responses API contract that non-Codex providers actually implement. They're
Codex-specific gaps in its own dialect.

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
    """Apply all three Codex-compatibility patches. Returns True only if all
    applied — callers should treat a False return as a hard startup error,
    not a silent no-op, since running unpatched means a broken tool-use loop,
    a hard 400 on every request against Codex, or every auto-mode classifier
    request silently failing.
    """
    return _patch_stop_reason() and _patch_system_role_items() and _patch_force_streaming()


_TIER_ENV_VARS = (
    ("opus", "ANTHROPIC_DEFAULT_OPUS_MODEL"),
    ("sonnet", "ANTHROPIC_DEFAULT_SONNET_MODEL"),
    ("haiku", "ANTHROPIC_DEFAULT_HAIKU_MODEL"),
)


def _unknown_model_hint(model_name: str, aliases: list[str]) -> str:
    """Actionable text for a request whose model this proxy does not serve.
    Names the exact env var to set when the name is a Claude tier model
    (claude-opus-*, claude-sonnet-*, claude-haiku-*), a generic pointer otherwise.
    """
    served = ", ".join(aliases)
    lowered = str(model_name).lower()
    var = next((v for tier, v in _TIER_ENV_VARS if lowered.startswith("claude") and tier in lowered), None)
    if var is not None:
        fix = (
            f"Set {var} (or ANTHROPIC_MODEL, if it names this model) to one of them "
            "before launching Claude Code."
        )
    else:
        fix = "Set ANTHROPIC_MODEL to one of them before launching Claude Code."
    return f"codextender serves only these model aliases: {served}. {fix} See README, section Usage."


def install_unknown_model_hint(aliases: list[str]) -> bool:
    """Make the proxy's 'Invalid model name' 400 say what to do about it.

    Cosmetic and diagnostic only, so unlike ``apply()`` a failure here is a
    warning, not a startup error. Same strategy as the other patches: call
    LiteLLM's real ``ProxyModelNotFoundError.__init__`` unmodified, then
    replace the one field that is unhelpful (``detail``) with the original
    message plus the hint. Claude Code shows that text to the user or the
    agent, which can then fix its own launch env.
    """
    try:
        from litellm.proxy import route_llm_request as mod
    except ImportError:
        logger.warning("codextender: could not import LiteLLM's route_llm_request; unknown-model hint not installed.")
        return False

    cls = getattr(mod, "ProxyModelNotFoundError", None)
    if cls is None:
        logger.warning("codextender: LiteLLM no longer exposes ProxyModelNotFoundError; unknown-model hint not installed.")
        return False

    if getattr(cls, _PATCHED_ATTR, False):
        return True

    original_init = cls.__init__

    @functools.wraps(original_init)
    def patched_init(self, route, model_name, *args, **kwargs):
        original_init(self, route, model_name, *args, **kwargs)
        try:
            detail = self.detail
            if isinstance(detail, dict) and isinstance(detail.get("error"), str):
                detail["error"] = f"{detail['error']} {_unknown_model_hint(model_name, aliases)}"
        except Exception:  # never let a cosmetic hint mask the real 400
            logger.debug("codextender: could not append unknown-model hint", exc_info=True)

    cls.__init__ = patched_init
    setattr(cls, _PATCHED_ATTR, True)
    return True


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


def _patch_force_streaming() -> bool:
    """Monkeypatch LiteLLM's Anthropic->Responses async handler so a
    non-streaming caller (Claude Code's auto-mode classifier request) still
    works against an endpoint that hard-requires `stream: true`.

    Root cause (verified live, 2026-09-26): config.py's `extra_body: {"stream":
    true}` forces the WIRE request Codex sees to always stream, which fixes
    Codex's 400. But `async_anthropic_messages_handler`'s Python-level branch
    (SSE-wrap vs. plain-response) reads its own `stream` argument -- the
    caller's original request flag, untouched by extra_body -- so a caller
    that asked for `stream: false` still hits the non-streaming branch,
    which then chokes on the ResponsesAPIStreamingIterator extra_body's
    override produced instead of the ResponsesAPIResponse it expects.

    Patch strategy: always call the original handler with `stream=True`
    (matching what actually happens on the wire either way), then:
      - caller wanted streaming: return the SSE-encoded generator unchanged
        -- zero behavior change for the main chat path.
      - caller wanted non-streaming: drain the underlying stream wrapper's
        raw Anthropic-shaped event dicts ourselves (going around the
        SSE-encoding step, which only encodes bytes for transport) and
        aggregate them into the same AnthropicMessagesResponse shape
        `_ADAPTER.translate_response()` would have produced for a plain
        response, per the documented Anthropic Messages streaming event
        sequence (message_start / content_block_* / message_delta /
        message_stop).
    """
    try:
        from litellm.llms.anthropic.experimental_pass_through.responses_adapters import (
            handler as mod,
        )
    except ImportError:
        logger.error(
            "codextender: could not import LiteLLM's Anthropic->Responses "
            "handler module. Is litellm==1.102.0 installed?"
        )
        return False

    cls = getattr(mod, "LiteLLMMessagesToResponsesAPIHandler", None)
    if cls is None:
        logger.error(
            "codextender: LiteLLM no longer exposes "
            "LiteLLMMessagesToResponsesAPIHandler in the expected module. "
            "This patch needs updating for your installed litellm version."
        )
        return False

    patched_attr = _PATCHED_ATTR + "_force_streaming"
    if getattr(cls, patched_attr, False):
        return True

    original_async_handler = cls.async_anthropic_messages_handler

    @functools.wraps(original_async_handler)
    async def patched_async_handler(*args, stream: bool | None = False, **kwargs):
        result = await original_async_handler(*args, stream=True, **kwargs)
        if stream:
            return result
        return await _aggregate_anthropic_sse_stream(result)

    cls.async_anthropic_messages_handler = staticmethod(patched_async_handler)
    setattr(cls, patched_attr, True)
    logger.info(
        "codextender: applied force-streaming patch to "
        "LiteLLMMessagesToResponsesAPIHandler.async_anthropic_messages_handler"
    )
    return True


async def _aggregate_anthropic_sse_stream(sse_bytes_iter) -> dict:
    """Drain an Anthropic-format SSE byte stream (as produced by
    `AnthropicResponsesStreamWrapper.async_anthropic_sse_wrapper()`) into a
    single AnthropicMessagesResponse-shaped dict, per the documented event
    sequence: message_start, content_block_start/delta/stop (repeated per
    block), message_delta, message_stop.
    """
    import json as _json

    message: dict | None = None
    blocks: dict[int, dict] = {}
    partial_json: dict[int, str] = {}
    usage_updates: dict = {}

    async for raw in sse_bytes_iter:
        for event in _parse_sse_events(raw):
            etype = event.get("type")
            if etype == "message_start":
                message = dict(event.get("message") or {})
            elif etype == "content_block_start":
                idx = event.get("index")
                block = dict(event.get("content_block") or {})
                if block.get("type") == "tool_use":
                    partial_json[idx] = ""
                blocks[idx] = block
            elif etype == "content_block_delta":
                idx = event.get("index")
                block = blocks.get(idx)
                if block is None:
                    continue
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta":
                    block["text"] = block.get("text", "") + delta.get("text", "")
                elif delta.get("type") == "input_json_delta":
                    partial_json[idx] = partial_json.get(idx, "") + delta.get("partial_json", "")
            elif etype == "content_block_stop":
                idx = event.get("index")
                if idx in partial_json:
                    raw_json = partial_json.pop(idx)
                    try:
                        blocks[idx]["input"] = _json.loads(raw_json) if raw_json else {}
                    except ValueError:
                        blocks[idx]["input"] = {}
            elif etype == "message_delta":
                delta = event.get("delta") or {}
                if message is not None:
                    for key in ("stop_reason", "stop_sequence"):
                        if key in delta:
                            message[key] = delta[key]
                usage = event.get("usage")
                if isinstance(usage, dict):
                    usage_updates.update(usage)
            elif etype == "message_stop":
                break

    if message is None:
        raise ValueError(
            "codextender: aggregated an empty Codex response stream (no "
            "message_start event) -- see _aggregate_anthropic_sse_stream"
        )

    message["content"] = [blocks[i] for i in sorted(blocks)]
    if usage_updates:
        message["usage"] = {**(message.get("usage") or {}), **usage_updates}
    return message


def _parse_sse_events(raw: bytes | str) -> list[dict]:
    """Parse one or more `data: {...}` SSE lines out of a raw chunk into
    their decoded JSON event dicts, ignoring `event:`/blank lines and a
    trailing `data: [DONE]` sentinel.
    """
    import json as _json

    text = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
    events: list[dict] = []
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            events.append(_json.loads(payload))
        except ValueError:
            continue
    return events


_BILLING_HEADER_PREFIX = "x-anthropic-billing-header:"


def _strip_billing_header_line(text: str) -> str:
    """Drop Claude Code's `x-anthropic-billing-header:` line, if present.

    That line's content changes on every request (verified live,
    2026-09-28: two consecutive `claude -p` calls carried different
    `cc_version` build hashes), so leaving it in `instructions` puts a
    different prefix in front of Codex on every turn and defeats any
    prefix-based prompt caching the backend might do.
    """
    lines = text.split("\n")
    filtered = [
        line for line in lines
        if not line.strip().lower().startswith(_BILLING_HEADER_PREFIX)
    ]
    return "\n".join(filtered)


def _fold_non_user_role_items_into_instructions(kwargs: dict) -> dict:
    # Strip unconditionally, even if there's nothing to fold from `input`:
    # a request with no prompt-cache breakpoint never gets a `developer`
    # role item, so LiteLLM puts the system text straight into
    # `instructions` and the loop below never sees it.
    existing_instructions = kwargs.get("instructions") or ""
    if existing_instructions:
        kwargs["instructions"] = _strip_billing_header_line(existing_instructions)

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
                    stripped = _strip_billing_header_line(part["text"])
                    if stripped:
                        extracted_text.append(stripped)
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
