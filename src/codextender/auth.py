"""Reuse the real Codex CLI's own ChatGPT-OAuth credentials.

codextender never implements its own OAuth flow. It reads the same file the
real Codex CLI writes and reads on your machine, and — when the access token
is stale — asks the real `codex` binary to refresh it, rather than
reimplementing OpenAI's refresh flow itself. This mirrors the pattern already
proven by other Codex-bridging tools in this space: reuse the credential
store and the refresh implementation of a trusted first-party binary, don't
rebuild either.

NOTE: the refresh path (refresh_via_app_server) talks to `codex app-server`
over JSON-RPC. The exact request/response shape here was reconstructed from
reading another project's source, not independently verified against a live
`codex app-server` process by this package's author. Treat it as a
best-effort first draft — verify against your installed Codex CLI version
before relying on it, and expect to adjust method/param names if your
version differs.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class CodexAuthError(RuntimeError):
    """Raised when the local Codex CLI credential file is missing, malformed,
    or not in ChatGPT-OAuth mode (e.g. it's API-key mode instead)."""


@dataclass(frozen=True)
class CodexCredentials:
    access_token: str
    account_id: str | None


def _codex_home() -> Path:
    env_home = os.environ.get("CODEX_HOME")
    if env_home:
        return Path(env_home)
    return Path.home() / ".codex"


def _auth_json_path() -> Path:
    return _codex_home() / "auth.json"


def load_credentials() -> CodexCredentials:
    """Read the current token from ~/.codex/auth.json without refreshing it.

    Raises CodexAuthError if the file is missing, unreadable, or not in
    ChatGPT-OAuth mode. Callers that need a guaranteed-fresh token should
    call refresh_via_app_server() first (or on a 401 from the upstream API).
    """
    path = _auth_json_path()
    if not path.exists():
        raise CodexAuthError(
            f"No Codex CLI credentials found at {path}. Run `codex login` "
            "(or open the Codex CLI once and sign in with ChatGPT) first."
        )

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CodexAuthError(f"Could not read/parse {path}: {exc}") from exc

    auth_mode = raw.get("auth_mode")
    if auth_mode != "chatgpt":
        raise CodexAuthError(
            f"{path} is in auth_mode={auth_mode!r}, expected 'chatgpt'. "
            "codextender only supports the ChatGPT/Codex-plan subscription "
            "login, not API-key mode."
        )

    tokens = raw.get("tokens") or {}
    access_token = tokens.get("access_token")
    if not access_token:
        raise CodexAuthError(f"{path} has no tokens.access_token.")

    return CodexCredentials(
        access_token=access_token,
        account_id=tokens.get("account_id"),
    )


def refresh_via_app_server(timeout_seconds: float = 20.0) -> CodexCredentials:
    """Ask the real `codex` binary to refresh its own stored token, then
    re-read auth.json.

    UNVERIFIED live against a real `codex app-server` process — see module
    docstring. If this raises or hangs, the safer fallback is: tell the
    operator to run `codex login` again, rather than guessing at a different
    JSON-RPC shape blind.
    """
    proc = subprocess.Popen(
        ["codex", "app-server", "-c", 'cli_auth_credentials_store="file"'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        _send_jsonrpc(proc, "initialize", {})
        _read_jsonrpc_response(proc, timeout_seconds)
        _send_jsonrpc(proc, "account/read", {"refreshToken": True})
        _read_jsonrpc_response(proc, timeout_seconds)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    return load_credentials()


def _send_jsonrpc(proc: subprocess.Popen, method: str, params: dict) -> None:
    assert proc.stdin is not None
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()


def _read_jsonrpc_response(proc: subprocess.Popen, timeout_seconds: float) -> dict:
    assert proc.stdout is not None
    line = proc.stdout.readline()
    if not line:
        stderr = proc.stderr.read() if proc.stderr else ""
        raise CodexAuthError(f"codex app-server closed without responding. stderr: {stderr[:2000]}")
    return json.loads(line)
