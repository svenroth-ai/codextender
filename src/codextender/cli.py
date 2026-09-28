"""codextender CLI entrypoint.

Applies the stop_reason patch, resolves your local Codex CLI credentials,
writes a LiteLLM proxy config pointed at Codex's internal Responses-API
endpoint, and runs the LiteLLM proxy **in this same process** (not as a
subprocess) — the patch only takes effect in the process that actually
serves requests, so codextender must launch LiteLLM in-process rather than
shelling out to the `litellm` CLI as a separate binary.
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path

from . import patch
from .auth import CodexAuthError, load_credentials, resolve_codex_binary
from .config import CODEX_RESPONSES_API_BASE, render_config_yaml
from .refresh import DEFAULT_REFRESH_INTERVAL_SECONDS, start_background_refresh

logger = logging.getLogger("codextender")

DEFAULT_PORT = 4000


def _force_utf8_streams() -> None:
    """Windows defaults stdout/stderr to the system ANSI codepage (e.g.
    cp1252), not UTF-8 — this bites hardest when output is redirected to a
    file/pipe (no console to fall back on), which is exactly the autostart
    and `Start-Process -RedirectStandardOutput` cases this tool is meant to
    support. LiteLLM's own startup banner uses box-drawing characters that
    cp1252 can't encode at all, crashing the whole proxy's startup with an
    unhandled UnicodeEncodeError before it ever binds the port — not a
    cosmetic issue, a hard failure to start. Reconfiguring here, as early as
    possible, fixes it for anything this process prints afterward,
    including LiteLLM's own internals since the proxy runs in this same
    process (see module docstring).
    """
    for stream in (sys.stdout, sys.stderr):
        encoding = getattr(stream, "encoding", None)
        reconfigure = getattr(stream, "reconfigure", None)
        if encoding is not None and encoding.lower() not in ("utf-8", "utf8") and callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass  # best-effort — an exotic stream type just keeps its own encoding


def main(argv: list[str] | None = None) -> int:
    _force_utf8_streams()
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    parser = argparse.ArgumentParser(prog="codextender")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--model",
        action="append",
        dest="models",
        metavar="SLUG[:ALIAS]",
        help="Codex model slug to expose, optionally with :alias (the name "
        "Claude Code calls it as via ANTHROPIC_MODEL). Repeatable — pass "
        "multiple times to serve several models from one running proxy, "
        "e.g. --model gpt-6-sol:sol --model gpt-6-astra:astra. Alias "
        "defaults to the slug itself when omitted. Default if unset: "
        "gpt-6-sol:sol. No catalog is maintained here on purpose — see "
        "README for why.",
    )
    parser.add_argument(
        "--no-token-refresh",
        action="store_true",
        help="Background OAuth-token refresh is ON by default (a long-running "
        "proxy — e.g. via the Windows autostart script — would otherwise "
        "start failing every request once the token baked in at startup "
        "expires). Pass this flag to turn it off.",
    )
    parser.add_argument(
        "--token-refresh-interval",
        type=int,
        default=DEFAULT_REFRESH_INTERVAL_SECONDS,
        metavar="SECONDS",
        help="Override the token-refresh interval (default: "
        f"{DEFAULT_REFRESH_INTERVAL_SECONDS}s / 20 min). Mainly for testing "
        "the refresh cycle without waiting the full default interval — e.g. "
        "--token-refresh-interval 60 to see it fire within roughly a minute.",
    )
    args = parser.parse_args(argv)

    model_pairs = _parse_model_args(args.models or ["gpt-6-sol:sol"])

    if not patch.apply():
        logger.error("Refusing to start unpatched — tool-use loops would silently break.")
        return 1

    try:
        creds = load_credentials()
    except CodexAuthError as exc:
        logger.error(str(exc))
        return 1

    config_yaml = render_config_yaml(
        models=model_pairs,
        api_base=CODEX_RESPONSES_API_BASE,
        access_token=creds.access_token,
        account_id=creds.account_id,
    )

    with tempfile.TemporaryDirectory(prefix="codextender-") as tmp_dir:
        config_path = Path(tmp_dir) / "config.yaml"
        config_path.write_text(config_yaml, encoding="utf-8")

        aliases = ", ".join(alias for alias, _ in model_pairs)
        logger.info("Starting proxy on http://127.0.0.1:%d (model aliases: %s)", args.port, aliases)
        logger.info(
            "Point Claude Code at it with: ANTHROPIC_BASE_URL=http://127.0.0.1:%d "
            "ANTHROPIC_AUTH_TOKEN=<see README> ANTHROPIC_MODEL=<alias> "
            "ANTHROPIC_DEFAULT_OPUS_MODEL=<alias> ANTHROPIC_DEFAULT_SONNET_MODEL=<alias> "
            "ANTHROPIC_DEFAULT_HAIKU_MODEL=<alias> claude",
            args.port,
        )

        if not args.no_token_refresh:
            if resolve_codex_binary() is None:
                logger.error(
                    "`codex` was not found on PATH — background token refresh "
                    "disabled. A long-running proxy will need a restart once "
                    "its token expires. Fix PATH and restart codextender to "
                    "enable it, or pass --no-token-refresh to silence this."
                )
            else:
                start_background_refresh(interval_seconds=args.token_refresh_interval)
                logger.info(
                    "background token refresh enabled (checks every 30s, "
                    "refreshes roughly every %ds or on an approaching token "
                    "expiry)",
                    args.token_refresh_interval,
                )

        # Imported here, after patch.apply(), so the patched class is what
        # actually gets used when the proxy app is constructed.
        from litellm.proxy.proxy_cli import run_server

        run_server.main(
            args=["--config", str(config_path), "--port", str(args.port)],
            standalone_mode=False,
        )

    return 0


def _parse_model_args(raw: list[str]) -> list[tuple[str, str]]:
    """``["gpt-6-sol:sol", "gpt-6-astra"]`` -> ``[("sol","gpt-6-sol"),
    ("gpt-6-astra","gpt-6-astra")]`` — alias defaults to the slug itself.
    """
    pairs: list[tuple[str, str]] = []
    for entry in raw:
        slug, _, alias = entry.partition(":")
        pairs.append((alias or slug, slug))
    return pairs


if __name__ == "__main__":
    sys.exit(main())
