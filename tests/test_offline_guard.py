"""Canary for the suite-wide network guard in tests/conftest.py: if the autouse fixture
is ever disabled or broken, this fails instead of the suite silently going back online."""
import socket

import pytest


def test_guard_blocks_non_loopback_connect():
    with socket.socket() as sock, pytest.raises(OSError, match="network access blocked"):
        sock.connect(("192.0.2.1", 443))  # TEST-NET-1, never routable


def test_guard_blocks_dns():
    with pytest.raises(socket.gaierror, match="network access blocked"):
        socket.getaddrinfo("api.semanticscholar.org", 443)
