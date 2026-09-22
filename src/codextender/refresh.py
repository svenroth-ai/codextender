"""Background token-refresh loop for a long-running (e.g. autostart) proxy.

``cli.py`` bakes the OAuth access token from ``~/.codex/auth.json`` into
LiteLLM's router once, at startup (via ``config.render_config_yaml``), and
never touches it again on its own — a proxy left running past the access
token's lifetime (short-lived; the ``refresh_token`` alongside it in
``auth.json`` is the actually-long-lived credential) would silently start
failing every request. A proxy started once via the Windows autostart script
and left running for hours — including through a laptop suspend/resume — is
exactly that case.

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
very next outbound request** — an independent read of the installed
litellm==1.102.0 source (the version this project pins) confirmed why: each
deployment's api_key is read fresh out of ``litellm_params`` per request
(``Router._acompletion``/``_ageneric_api_call_with_fallbacks_helper`` both
do a shallow ``.copy()`` of ``litellm_params`` at call time), and the one
client-side cache that *is* keyed per-request (``in_memory_llm_clients_cache``)
keys on a hash of the api_key itself, so a rotated key naturally misses that
cache and gets a fresh client rather than reusing a stale one. Plain
mutation was kept over delete+add because it touches strictly less of
Router's internal bookkeeping (pattern routers, budget limiters, deployment
indices) that delete/re-add walks through for no benefit here.

Scheduling: this does NOT sleep a flat interval and call it done — a fixed
``time.sleep(20 * 60)`` would (a) not notice a laptop waking from a longer
suspend until up to 20 more minutes had passed with a dead token, and (b)
have to guess blindly at how long a token stays valid. Instead this polls
every ``_POLL_SECONDS`` and refreshes when either: the plain interval has
elapsed; the wall clock jumped far ahead of monotonic time since the last
poll (a suspend/resume); or the access token's own JWT ``exp`` claim (when
decodable — see ``auth.decode_jwt_exp``) is within ``_EXP_REFRESH_MARGIN_SECONDS``
of expiring. The fixed interval remains the fallback whenever ``exp`` isn't
decodable, since ``auth.json``'s full shape hasn't been independently
verified to always carry a usable expiry this project can read without
guessing at its format.

Not implemented: reactively refreshing on an actual 401 from Codex's
endpoint, which would recover faster than any polling scheme from a token
that turned out to be invalidated early (e.g. a manual `codex logout`
elsewhere) — left as a future improvement, since it needs hooking into
LiteLLM's response path rather than living here as a background poller.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from .auth import CodexAuthError, decode_jwt_exp, refresh_via_app_server

logger = logging.getLogger("codextender.refresh")

DEFAULT_REFRESH_INTERVAL_SECONDS = 20 * 60

# How often the loop wakes up to check whether a refresh is due. Small
# relative to the refresh interval so a suspend/resume or an approaching
# JWT expiry is noticed promptly rather than up to a full interval late.
_POLL_SECONDS = 30

# Refresh this long before a decoded JWT `exp`, not exactly at it, so a
# request arriving right at the boundary doesn't race an expiring token.
_EXP_REFRESH_MARGIN_SECONDS = 5 * 60

# A wall-clock gap this much larger than the monotonic gap between two polls
# means the machine was suspended in between (monotonic time doesn't advance
# while suspended; wall clock does) — treated as "assume the token may now
# be stale, refresh immediately" rather than waiting out the rest of the
# normal interval.
_SUSPEND_GAP_THRESHOLD_SECONDS = _POLL_SECONDS * 4


@dataclass
class _RefreshState:
    last_token: str | None = None
    last_refresh_monotonic: float | None = None
    next_deadline_monotonic: float | None = None  # from a decoded JWT `exp`, if any


def start_background_refresh(
    *,
    interval_seconds: float = DEFAULT_REFRESH_INTERVAL_SECONDS,
) -> threading.Thread:
    """Starts (and returns) the daemon refresh thread. Call this BEFORE the
    blocking LiteLLM proxy server call, not after — it needs to run
    concurrently with request serving, not sequentially before/after it.

    Safe to start before the LiteLLM router exists yet: the loop's first
    refresh attempt is gated on ``interval_seconds`` having elapsed, which is
    comfortably longer than proxy startup takes.
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
    state = _RefreshState(last_refresh_monotonic=time.monotonic())
    last_wall = time.time()
    last_mono = time.monotonic()

    while True:
        time.sleep(_POLL_SECONDS)
        now_wall = time.time()
        now_mono = time.monotonic()
        wall_elapsed = now_wall - last_wall
        mono_elapsed = now_mono - last_mono
        suspended = (wall_elapsed - mono_elapsed) > _SUSPEND_GAP_THRESHOLD_SECONDS
        last_wall, last_mono = now_wall, now_mono

        assert state.last_refresh_monotonic is not None
        interval_due = (now_mono - state.last_refresh_monotonic) >= interval_seconds
        deadline_due = (
            state.next_deadline_monotonic is not None
            and now_mono >= state.next_deadline_monotonic - _EXP_REFRESH_MARGIN_SECONDS
        )

        if not (interval_due or deadline_due or suspended):
            continue

        try:
            _refresh_once(state)
        except (CodexAuthError, OSError, ValueError) as exc:
            # Expected operational failures (codex not on PATH, app-server
            # unreachable/misbehaving, malformed JSON) — worth a warning,
            # not a full traceback every cycle.
            logger.warning("token refresh failed (%s) — will retry", exc)
        except Exception:
            # Anything else is unexpected and worth the full traceback.
            logger.exception("token refresh cycle raised an unexpected error — will retry")
        finally:
            state.last_refresh_monotonic = time.monotonic()


def _refresh_once(state: _RefreshState) -> None:
    creds = refresh_via_app_server()

    exp = decode_jwt_exp(creds.access_token)
    state.next_deadline_monotonic = time.monotonic() + (exp - time.time()) if exp is not None else None

    if creds.access_token == state.last_token:
        logger.info("checked Codex OAuth token — unchanged, still valid")
        return
    state.last_token = creds.access_token

    updated = _push_token_into_live_router(creds.access_token)
    if updated:
        logger.info("refreshed Codex OAuth token, updated %d live deployment(s)", updated)
    else:
        logger.warning(
            "refreshed the token but the live LiteLLM router isn't ready yet "
            "(or has no deployments) — will retry next check"
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
