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
from .auth import CodexAuthError, load_credentials
from .config import CODEX_RESPONSES_API_BASE, render_config_yaml

logger = logging.getLogger("codextender")

DEFAULT_PORT = 4000


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    parser = argparse.ArgumentParser(prog="codextender")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--model",
        default="gpt-6-sol",
        help="Codex model slug to expose (default: gpt-6-sol). "
        "No catalog is maintained here on purpose — see README for why.",
    )
    parser.add_argument(
        "--alias",
        default="sol",
        help="Model name Claude Code / your client will call this as (default: sol).",
    )
    args = parser.parse_args(argv)

    if not patch.apply():
        logger.error("Refusing to start unpatched — tool-use loops would silently break.")
        return 1

    try:
        creds = load_credentials()
    except CodexAuthError as exc:
        logger.error(str(exc))
        return 1

    config_yaml = render_config_yaml(
        alias=args.alias,
        model=args.model,
        api_base=CODEX_RESPONSES_API_BASE,
        access_token=creds.access_token,
        account_id=creds.account_id,
    )

    with tempfile.TemporaryDirectory(prefix="codextender-") as tmp_dir:
        config_path = Path(tmp_dir) / "config.yaml"
        config_path.write_text(config_yaml, encoding="utf-8")

        logger.info("Starting proxy on http://127.0.0.1:%d (model alias: %s)", args.port, args.alias)
        logger.info(
            "Point Claude Code at it with: ANTHROPIC_BASE_URL=http://127.0.0.1:%d "
            "ANTHROPIC_AUTH_TOKEN=<see README> claude",
            args.port,
        )

        # Imported here, after patch.apply(), so the patched class is what
        # actually gets used when the proxy app is constructed.
        from litellm.proxy.proxy_cli import run_server

        run_server.main(
            args=["--config", str(config_path), "--port", str(args.port)],
            standalone_mode=False,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
