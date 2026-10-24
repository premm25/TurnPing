"""Authenticated local IPC between hooks/CLI and the desktop app.

Transport: a Windows named pipe (``\\\\.\\pipe\\TurnPing-<user-hash>``) via
``multiprocessing.connection``. There is no network listener.

Authentication: an HMAC challenge/response using a random 32-byte key stored in the
per-user data directory (``ipc.key``). Only processes that can read the user's profile
can connect. Authentication runs on a worker thread so a client that stalls cannot
block the accept loop.

Framing: we only ever use ``send_bytes``/``recv_bytes`` with JSON. ``recv``/``send``
(which unpickle) are never called, so a connection cannot execute code. Messages are
capped at :data:`MAX_MESSAGE_BYTES`.

Messages (JSON objects, ``v`` = protocol version 1):

* ``{"v":1,"type":"event","event":{...AgentEvent wire form...}}``
* ``{"v":1,"type":"command","command":"show"|"open_event"|"ping","event_id":...}``

Responses: ``{"ok": true, ...}`` or ``{"ok": false, "error": "..."}``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sys
import threading
from collections.abc import Callable
from multiprocessing import AuthenticationError
from multiprocessing.connection import (
    Client,
    Connection,
    Listener,
    answer_challenge,
    deliver_challenge,
)
from pathlib import Path
from typing import Any

from turnping.security.paths import current_user, get_paths

log = logging.getLogger(__name__)

MAX_MESSAGE_BYTES = 64 * 1024
PROTOCOL_VERSION = 1
CLIENT_TIMEOUT = 2.0
MAX_CONCURRENT_CLIENTS = 8


def pipe_address(home: Path | None = None) -> str:
    """Per-user (and per data dir) pipe name, so test instances never collide."""
    home = home or get_paths().home
    ident = f"{current_user()}|{str(home).lower()}"
    digest = hashlib.sha256(ident.encode("utf-8")).hexdigest()[:16]
    if sys.platform == "win32":
        return rf"\\.\pipe\TurnPing-{digest}"
    return str(home / f"turnping-{digest}.sock")


def _family() -> str:
    return "AF_PIPE" if sys.platform == "win32" else "AF_UNIX"


def load_or_create_key(path: Path | None = None) -> bytes:
    path = path or get_paths().ipc_key_file
    try:
        data = path.read_bytes()
        if len(data) >= 32:
            return data[:32]
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(key)
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(path)
    return key


def read_key(path: Path | None = None) -> bytes | None:
    path = path or get_paths().ipc_key_file
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data[:32] if len(data) >= 32 else None


class AlreadyRunningError(RuntimeError):
    pass


class IpcServer:
    def __init__(
        self,
        handler: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        address: str | None = None,
        key: bytes | None = None,
    ) -> None:
        self.address = address or pipe_address()
        self.key = key or load_or_create_key()
        self.handler = handler
        self._listener: Listener | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT_CLIENTS)
        self.rejected = 0

    def start(self) -> None:
        try:
            # authkey=None: we authenticate on a worker thread (see module docstring).
            self._listener = Listener(self.address, family=_family())
        except (PermissionError, OSError) as exc:
            raise AlreadyRunningError(f"another TurnPing instance owns {self.address}") from exc
        self._thread = threading.Thread(target=self._accept_loop, name="turnping-ipc", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        # Unblock accept() by connecting to ourselves.
        try:
            conn = Client(self.address, family=_family())
            conn.close()
        except Exception:  # noqa: S110 - only used to wake the accept loop
            pass
        if self._listener is not None:
            try:
                self._listener.close()
            except Exception:  # noqa: S110
                pass
        if self._thread is not None:
            self._thread.join(3.0)

    def _accept_loop(self) -> None:
        assert self._listener is not None
        while not self._stopping.is_set():
            try:
                conn = self._listener.accept()
            except Exception:
                if self._stopping.is_set():
                    break
                log.exception("IPC accept failed")
                continue
            if self._stopping.is_set():
                conn.close()
                break
            if not self._slots.acquire(blocking=False):
                self.rejected += 1
                conn.close()
                continue
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: Connection) -> None:
        try:
            try:
                deliver_challenge(conn, self.key)
                answer_challenge(conn, self.key)
            except (AuthenticationError, EOFError, OSError):
                self.rejected += 1
                log.warning("rejected unauthenticated IPC client")
                return
            if not conn.poll(CLIENT_TIMEOUT):
                return
            try:
                raw = conn.recv_bytes(maxlength=MAX_MESSAGE_BYTES)
            except OSError:
                self.rejected += 1
                log.warning("rejected oversized or broken IPC message")
                return
            response = self._handle(raw)
            conn.send_bytes(json.dumps(response).encode("utf-8"))
        except Exception:
            log.exception("IPC client handling failed")
        finally:
            try:
                conn.close()
            finally:
                self._slots.release()

    def _handle(self, raw: bytes) -> dict[str, Any]:
        try:
            message = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"ok": False, "error": "message is not valid JSON"}
        if not isinstance(message, dict) or message.get("v") != PROTOCOL_VERSION:
            return {"ok": False, "error": "unsupported protocol version"}
        try:
            return self.handler(message)
        except Exception as exc:
            log.exception("IPC handler failed")
            return {"ok": False, "error": exc.__class__.__name__}


def send_message(
    message: dict[str, Any], *, address: str | None = None, key: bytes | None = None
) -> dict[str, Any]:
    """Send one message to the running app. Raises ConnectionError if unreachable."""
    address = address or pipe_address()
    key = key if key is not None else read_key()
    if key is None:
        raise ConnectionError("TurnPing has never been started (no IPC key yet)")
    payload = json.dumps({"v": PROTOCOL_VERSION, **message}).encode("utf-8")
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ValueError("message too large")
    try:
        conn = Client(address, family=_family())
    except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
        raise ConnectionError(f"TurnPing is not running ({exc.__class__.__name__})") from exc
    try:
        answer_challenge(conn, key)
        deliver_challenge(conn, key)
        conn.send_bytes(payload)
        if not conn.poll(5.0):
            raise ConnectionError("TurnPing did not answer in time")
        response = json.loads(conn.recv_bytes(maxlength=MAX_MESSAGE_BYTES).decode("utf-8"))
        return response if isinstance(response, dict) else {"ok": False, "error": "bad response"}
    except (AuthenticationError, EOFError) as exc:
        raise ConnectionError(f"IPC authentication failed ({exc.__class__.__name__})") from exc
    finally:
        conn.close()


def is_app_running() -> bool:
    try:
        return bool(send_message({"type": "command", "command": "ping"}).get("ok"))
    except (ConnectionError, ValueError, OSError):
        return False
