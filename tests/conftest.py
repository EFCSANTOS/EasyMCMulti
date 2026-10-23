"""Pytest setup: shared fixtures (from support.py), no network, a fresh runtime per test (QUAL-02)."""
import socket

import pytest

from support import *  # noqa: F401,F403 - registers the shared fixtures with pytest
from support import app
from webapp.state import RuntimeState

# Loopback still works (local test servers); everything else fails fast instead of reaching the internet.
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", None}


@pytest.fixture(autouse=True)
def no_network(request, monkeypatch):
    """Tests are hermetic: no DNS, no outside connections. Mark a test `network` to allow them."""
    if request.node.get_closest_marker("network"):
        return
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo

    def guarded_connect(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if host in _LOCAL_HOSTS:
            return real_connect(sock, address)
        raise OSError(f"network disabled in tests (tried {host}); mark the test with @pytest.mark.network")

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host in _LOCAL_HOSTS:
            return real_getaddrinfo(host, *args, **kwargs)
        raise socket.gaierror(f"network disabled in tests (looked up {host})")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)


@pytest.fixture(autouse=True)
def fresh_runtime():
    """Every test starts from a new RuntimeState, so no test depends on what an earlier one left."""
    app.state.runtime.__dict__.update(RuntimeState().__dict__)
    app.state.services.reset_notifications()
    app.state.services.reset_health()
    yield
