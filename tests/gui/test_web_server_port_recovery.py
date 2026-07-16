from __future__ import annotations

import socket

from distr.gui.web.server import _web_port_available


def test_web_port_probe_rejects_live_listener_and_accepts_released_port():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    assert _web_port_available("127.0.0.1", port) is False

    listener.close()

    assert _web_port_available("127.0.0.1", port) is True
