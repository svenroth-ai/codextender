"""Reuse the real Codex CLI's own ChatGPT-OAuth credentials.

codextender never implements its own OAuth flow. It reads the same file the
real Codex CLI writes and reads on your machine, and — when the access token
is stale — asks the real `codex` binary to refresh it, rather than
reimplementing OpenAI's refresh flow itself. This mirrors the pattern already
proven by other Codex-bridging tools in this space: reuse the credential
store and the refresh implementation of a trusted first-party binary, don't
rebuild either.

NOTE: the refresh path (refresh_via_app_server) talks to `codex app-server`
over JSON-RPC. **Verified end-to-end live, 2026-09-23** (operator's own
real Codex CLI + real account, via codextender's own background refresh
loop — not run by this package's own tooling, which never touches real
credentials): the full round-trip — `initialize`, `account/read`, re-read
`auth.json`, push the new token into the live LiteLLM router — completed
successfully, logged as `refreshed Codex OAuth token, updated 1 live
deployment(s)`. Got there in two iterations, both driven by concrete
errors from the real process rather than guesswork: the request/response
shape was originally reconstructed from reading another project's source,
and the first live attempt was rejected outright (`missing field
'clientInfo'`) — fixed, then confirmed working on the very next run.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import queue
import shutil
import subprocess
import threading
import time
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


def decode_jwt_exp(access_token: str) -> float | None:
    """Best-effort read of a JWT's `exp` claim (Unix timestamp), without
    verifying its signature.

    This is only ever used to schedule *this process's own* refresh checks —
    never for an authorization decision — so signature verification would be
    pointless work: a forged `exp` could at worst make codextender refresh
    too early or too late, not grant access to anything. Returns None for
    any malformed/non-JWT input rather than raising, since callers treat a
    missing expiry as "fall back to fixed-interval refresh," not an error.
    """
    try:
        payload_b64 = access_token.split(".")[1]
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        exp = payload.get("exp")
        return float(exp) if exp is not None else None
    except (IndexError, ValueError, TypeError, binascii.Error):
        return None


def resolve_codex_binary() -> str | None:
    """Resolves the real `codex` binary's full path via PATH lookup.

    Deliberately not just ``["codex", ...]`` passed straight to ``Popen``:
    on Windows, `codex` on a Node-managed PATH is typically a `.cmd` shim
    with no `.exe` sibling, and Windows `CreateProcess` (what `Popen` calls
    without `shell=True`) only auto-appends `.exe` to an extensionless name
    — it does not resolve `.cmd`/`.bat` the way `cmd.exe` or `shutil.which`
    do, so `Popen(["codex", ...])` raises `FileNotFoundError` even though
    `codex` works fine when typed at a prompt. `shutil.which` performs the
    same PATHEXT-aware resolution a shell does and returns a path `Popen`
    can launch directly.
    """
    return shutil.which("codex")


def refresh_via_app_server(timeout_seconds: float = 20.0) -> CodexCredentials:
    """Ask the real `codex` binary to refresh its own stored token, then
    re-read auth.json.

    UNVERIFIED live against a real `codex app-server` process — see module
    docstring. If this raises or hangs, the safer fallback is: tell the
    operator to run `codex login` again, rather than guessing at a different
    JSON-RPC shape blind.
    """
    codex_path = resolve_codex_binary()
    if codex_path is None:
        raise CodexAuthError(
            "`codex` was not found on PATH — cannot refresh the OAuth token. "
            "Install/PATH the Codex CLI, or run `codex login` manually and "
            "restart codextender."
        )

    proc = subprocess.Popen(
        [codex_path, "app-server", "-c", 'cli_auth_credentials_store="file"'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        # Never PIPE stderr without draining it: an app-server that writes
        # more than one pipe buffer's worth of diagnostics to stderr would
        # otherwise block on that write forever, and since nothing here
        # reads stderr on the happy path, every future refresh cycle would
        # hang at the same point. Nothing here needs stderr's contents.
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    try:
        # `clientInfo` is REQUIRED — confirmed live, 2026-09-23: an earlier
        # version sent {} here and got a real, concrete rejection from a
        # real codex app-server process: {"code": -32600, "message":
        # "Invalid request: missing field `clientInfo`"}. The exact shape
        # (name/version, not e.g. a nested object) is not independently
        # confirmed beyond "this satisfies the missing-field check" — if a
        # future codex CLI version wants more (protocolVersion,
        # capabilities, etc., as MCP's initialize does), expect another
        # concrete rejection naming the next missing field, the same way
        # this one was found.
        _send_jsonrpc(
            proc,
            1,
            "initialize",
            {"clientInfo": {"name": "codextender", "version": "0.2.0"}},
        )
        _read_matching_response(proc, 1, timeout_seconds)
        _send_jsonrpc(proc, 2, "account/read", {"refreshToken": True})
        _read_matching_response(proc, 2, timeout_seconds)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    return load_credentials()


def _send_jsonrpc(proc: subprocess.Popen, request_id: int, method: str, params: dict) -> None:
    if proc.stdin is None:
        raise RuntimeError("codex app-server subprocess has no stdin pipe")
    payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()


def _read_matching_response(proc: subprocess.Popen, expected_id: int, timeout_seconds: float) -> dict:
    """Reads JSON-RPC lines until one whose ``id`` matches ``expected_id``,
    within a single overall time budget (not one budget per line).

    LSP-derived JSON-RPC servers (this protocol's family) routinely emit
    unsolicited notifications between a request and its response; reading
    exactly one line per request — as an earlier version of this function
    did — would silently pair a stray notification with the wrong request
    and leave the real response unread, corrupting the whole handshake.
    Also raises CodexAuthError on an explicit ``{"error": ...}`` reply,
    rather than treating any well-formed JSON line as success.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CodexAuthError(
                f"codex app-server did not respond to id={expected_id} within "
                f"{timeout_seconds}s (only unrelated/no messages seen)."
            )
        message = _read_jsonrpc_response(proc, remaining)
        if message.get("id") != expected_id:
            continue  # an unrelated notification or stale message — keep reading
        if "error" in message:
            raise CodexAuthError(f"codex app-server returned an error for id={expected_id}: {message['error']}")
        return message


def _read_jsonrpc_response(proc: subprocess.Popen, timeout_seconds: float) -> dict:
    """Reads one line with a REAL timeout.

    ``proc.stdout.readline()`` alone can block forever if ``codex
    app-server`` never responds — there is no portable way to put a
    read-with-timeout directly on a subprocess pipe on Windows (no
    ``select()`` on pipes there), so this reads on a daemon helper thread
    and joins it with a timeout instead. On timeout, the helper thread stays
    blocked in ``readline()`` until the caller's ``finally: proc.terminate()``
    kills the process — that closes the pipe and unblocks it in the common
    case. Note this guarantee is *not* airtight: ``proc.terminate()`` on
    Windows only signals this one process, not any child it may have spawned
    that inherited the stdout handle, so a misbehaving app-server could still
    leak a blocked reader thread in that (unverified as possible) edge case.
    """
    if proc.stdout is None:
        raise RuntimeError("codex app-server subprocess has no stdout pipe")
    stdout = proc.stdout  # captured as a plain local so the type-narrowing
    # above actually holds inside the nested closure below (pyright can't
    # see through `proc.stdout` re-accessed via a closure otherwise).
    result: queue.Queue[str] = queue.Queue(maxsize=1)

    def _reader() -> None:
        try:
            result.put(stdout.readline())
        except (OSError, ValueError):
            # A closed/broken pipe (e.g. the process died mid-read) must
            # still unblock the caller's queue.get() rather than leave it
            # waiting the full timeout for nothing.
            result.put("")

    threading.Thread(target=_reader, daemon=True).start()
    try:
        line = result.get(timeout=timeout_seconds)
    except queue.Empty as exc:
        raise CodexAuthError(
            f"codex app-server did not respond within {timeout_seconds}s "
            "(no JSON-RPC response on stdout)."
        ) from exc

    if not line:
        raise CodexAuthError("codex app-server closed its stdout without responding.")
    return json.loads(line)
