"""
Unix socket IPC server for the Vectrax daemon.

The daemon listens on ~/.vectrax/events.sock for incoming event text.
Any process (including `vectrax creator inject`) can send a line of text
and the daemon will ingest it as a star in the creator channel.

Protocol (line-based):
    <text>\n            → ingest event in creator channel
    PING\n              → server responds PONG\n
    STATUS\n            → server responds with JSON summary

The socket is non-blocking and polled via select() in the daemon loop.
"""
from __future__ import annotations

import json
import os
import select
import socket
from pathlib import Path
from typing import List, Optional

from vectrax import config as cfg

SOCKET_PATH = str(Path.home() / ".vectrax" / "events.sock")
BUFFER_SIZE = 4096


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

def create_server() -> socket.socket:
    """Create and bind the Unix domain socket. Removes stale socket if present."""
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.setblocking(False)
    srv.bind(SOCKET_PATH)
    srv.listen(10)
    os.chmod(SOCKET_PATH, 0o600)   # owner-only access
    return srv


def close_server(srv: socket.socket) -> None:
    try:
        srv.close()
    except Exception:
        pass
    try:
        os.unlink(SOCKET_PATH)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Non-blocking poll — called from the daemon main loop via select()
# ---------------------------------------------------------------------------

def poll(srv: socket.socket, timeout: float = 0.1) -> List[str]:
    """
    Non-blocking poll for incoming connections.
    Returns a list of event strings received in this poll cycle.
    """
    events: List[str] = []
    try:
        readable, _, _ = select.select([srv], [], [], timeout)
    except (ValueError, OSError):
        return events

    for _ in readable:
        try:
            conn, _ = srv.accept()
            conn.settimeout(2.0)
            try:
                data = conn.recv(BUFFER_SIZE)
                if data:
                    text = data.decode("utf-8", errors="replace").strip()
                    if text == "PING":
                        conn.sendall(b"PONG\n")
                    elif text == "STATUS":
                        summary = cfg.observer_summary()
                        conn.sendall((json.dumps(summary) + "\n").encode())
                    elif text:
                        events.append(text)
                        conn.sendall(b"OK\n")
            except socket.timeout:
                pass
            finally:
                conn.close()
        except OSError:
            pass

    return events


# ---------------------------------------------------------------------------
# Client helpers (used by `vectrax creator inject`)
# ---------------------------------------------------------------------------

def send_event(text: str, timeout: float = 3.0) -> bool:
    """
    Send an event to the running daemon.
    Returns True if the daemon acknowledged it (responded OK).
    """
    if not os.path.exists(SOCKET_PATH):
        return False
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(timeout)
        c.connect(SOCKET_PATH)
        c.sendall((text.strip() + "\n").encode("utf-8"))
        resp = c.recv(64).decode("utf-8", errors="replace").strip()
        c.close()
        return resp == "OK"
    except Exception:
        return False


def ping_daemon(timeout: float = 2.0) -> bool:
    """Return True if the daemon is alive and responding."""
    if not os.path.exists(SOCKET_PATH):
        return False
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(timeout)
        c.connect(SOCKET_PATH)
        c.sendall(b"PING\n")
        resp = c.recv(16).decode("utf-8", errors="replace").strip()
        c.close()
        return resp == "PONG"
    except Exception:
        return False


def get_daemon_status(timeout: float = 2.0) -> Optional[dict]:
    """Return the daemon's status dict or None if unreachable."""
    if not os.path.exists(SOCKET_PATH):
        return None
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(timeout)
        c.connect(SOCKET_PATH)
        c.sendall(b"STATUS\n")
        data = c.recv(BUFFER_SIZE).decode("utf-8", errors="replace").strip()
        c.close()
        return json.loads(data)
    except Exception:
        return None
