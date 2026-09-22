"""Background token-refresh loop for a long-running (e.g. autostart) proxy.

``cli.py`` bakes the OAuth access token from ``~/.codex/auth.json`` into
LiteLLM's router once, at startup (via ``config.render_config_yaml``), and
never touches it again on its own — a proxy left running past the access
token's lifetime (short-lived; the ``refresh_token`` alongside it in
``auth.json`` is the actually-long-lived credential) would silently start
failing every request. A proxy started once via the Windows autostart script
and left running for hours is exactly that case.

This module starts a daemon background thread that periodically calls
``auth.refresh_via_app_server()`` and pushes the new access token into the
*live* ``litellm.proxy.proxy_server.llm_router`` — so an already-running
proxy process keeps working without a restart.

Credential-update mechanism: a plain in-place mutation of each deployment
dict's ``litellm_params["api_key"]`` inside ``router.model_list`` — nothing
more. This was NOT assumed from reading the source; ``Router`` has no
public "update this deployment's credentials" method, and static analysis
alone couldn't settle whether a stale, pre-built HTTP client (holding the
old key) might be cached per deployment and outlive a plain mutation. Two
designs were tried and both verified empirically against a real
``litellm.Router`` + a local HTTP server that records the actual
``Authorization`` header LiteLLM sends on the wire (not run against real
Codex — see the codextender repo's test history for both runs): (1)
``delete_deployment`` + ``add_deployment`` (the more defensive-looking
option, since ``add_deployment``'s own docstring says it "initialize[s the]
client"), and (2) this plain mutation. **Both produced the new token on the
very next outbound request** — grep of the installed litellm==1.102.0
source found nothing that actually populates a per-deployment client cache
keyed in a way a plain mutation could go stale against for this provider
path (``_get_client``'s ``{model_id}_client``/``{model_id}_async_client``
cache keys are read but never written anywhere in this version — dead code
for a plain OpenAI-compatible custom-endpoint deployment). Plain mutation
was kept over delete+add because it touches strictly less of Router's
internal bookkeeping (pattern routers, budget limiters, deployment
indices) that delete/re-add walks through for no benefit here.

Fixed-interval polling, not expiry-aware: ``auth.json`` isn't known to
expose a parsed expiry timestamp (not verified — nobody has read the raw
file's full shape for this project), so this refreshes on a conservative
fixed schedule rather than guessing at when to refresh from an expiry field
that may not exist. 20 minutes is comfortably under typical OAuth
access-token lifetimes (commonly ~1h) without refreshing so often it's
wasteful. Not implemented: reactively refreshing on an actual 401 from
Codex's endpoint, which would recover faster from an unexpectedly short
token lifetime — left as a future improvement, since it needs hooking into
LiteLLM's response path rather than living here as a plain timer.
"""

from __future__ import annotations

import logging
import threading
import time

from .auth import CodexAuthError, refresh_via_app_server

logger = logging.getLogger("codextender.refresh")

DEFAULT_REFRESH_INTERVAL_SECONDS = 20 * 60


def start_background_refresh(
    *,
    interval_seconds: float = DEFAULT_REFRESH_INTERVAL_SECONDS,
) -> threading.Thread:
    """Starts (and returns) the daemon refresh thread. Call this BEFORE the
    blocking LiteLLM proxy server call, not after — it needs to run
    concurrently with request serving, not sequentially before/after it.

    Safe to start before the LiteLLM router exists yet: the loop sleeps a
    full interval before its first refresh attempt, which is comfortably
    longer than proxy startup takes.
    """
    thread = threading.Thread(
        target=_refresh_loop,
        args=(interval_seconds,),
        name="codextender-token-refresh",
        daemon=True,
    )
    thread.start()
    return thread


def _refresh_loop(interval_seconds: float) -> None:
    while True:
        time.sleep(interval_seconds)
        try:
            _refresh_once()
        except Exception:
            # Never let a refresh-cycle exception kill this daemon thread —
            # the existing (possibly still-valid) token keeps working in the
            # meantime, and the next cycle tries again.
            logger.exception("codextender: token refresh cycle failed — will retry next interval")


def _refresh_once() -> None:
    try:
        creds = refresh_via_app_server()
    except CodexAuthError as exc:
        logger.warning(
            "codextender: token refresh failed (%s) — keeping the existing token until next cycle",
            exc,
        )
        return

    updated = _push_token_into_live_router(creds.access_token)
    if updated:
        logger.info("codextender: refreshed Codex OAuth token, updated %d live deployment(s)", updated)
    else:
        logger.warning(
            "codextender: refreshed the token but the live LiteLLM router isn't ready yet "
            "(or has no deployments) — will retry next cycle"
        )


def _push_token_into_live_router(new_access_token: str) -> int:
    from litellm.proxy import proxy_server

    router = proxy_server.llm_router
    if router is None:
        return 0

    updated = 0
    for entry in router.model_list:
        litellm_params = entry.get("litellm_params") or {}
        if litellm_params.get("api_key") is None:
            continue
        litellm_params["api_key"] = new_access_token
        updated += 1

    return updated
