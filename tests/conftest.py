"""Suite-wide offline guard.

Several skill-level tests drive code paths that make best-effort enrichment calls
(citation chasing, PDF download, metadata enrichment). Those calls swallow failures by
design, so a test that forgot to mock one still passes — but on a networked machine it
silently hits the live APIs with whatever keys are in config/api_keys.yaml. This fixture
makes any non-loopback connection fail fast instead, so the suite is deterministic and
never touches a real service.

Tests that genuinely need the network must be marked ``@pytest.mark.network``; they are
skipped unless LIT_REVIEW_NETWORK_TESTS=1 is set.
"""
from __future__ import annotations

import os
import socket

import pytest

# Hosts getaddrinfo may resolve (None = passive/wildcard lookup).
_LOOPBACK = {None, "localhost", "127.0.0.1", "::1", b"localhost", b"127.0.0.1", b"::1"}


def _is_loopback(address) -> bool:
    # AF_UNIX addresses are paths (str/bytes); asyncio uses them for its self-pipe.
    if isinstance(address, (str, bytes)):
        return True
    return isinstance(address, tuple) and address[0] in _LOOPBACK


def pytest_collection_modifyitems(config, items):
    if os.environ.get("LIT_REVIEW_NETWORK_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="network test — set LIT_REVIEW_NETWORK_TESTS=1 to run")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _block_network(request, monkeypatch):
    if "network" in request.keywords:
        return

    real_getaddrinfo = socket.getaddrinfo

    def guard(real):
        def guarded(self, address):
            if not _is_loopback(address):
                raise OSError(f"network access blocked in tests: connect to {address!r}")
            return real(self, address)
        return guarded

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host not in _LOOPBACK:
            raise socket.gaierror(f"network access blocked in tests: DNS lookup for {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guard(socket.socket.connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guard(socket.socket.connect_ex))
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)

