"""Local port selection for the ExamPool-owned 5E static server."""

from __future__ import annotations

import socket


def available_loopback_port() -> int:
    """Return an unused ephemeral TCP port on the loopback interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
