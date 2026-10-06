"""The guards that keep the suite honest: nothing leaves the host, and the structure guards exist."""

from __future__ import annotations

import pathlib
import socket

import pytest

TESTS = pathlib.Path(__file__).resolve().parent


@pytest.mark.resolves_off_host
@pytest.mark.parametrize("name", ["example.org", "boot.example.net", "pool.ntp.org"])
def test_a_name_that_would_leave_the_host_is_refused(name):
    with pytest.raises(AssertionError, match="resolved"):
        socket.getaddrinfo(name, 69)
    with pytest.raises(AssertionError, match="resolved"):
        socket.gethostbyname(name)


@pytest.mark.parametrize("name", ["127.0.0.1", "::1", "fe80::1%1", "localhost", "", None])
def test_an_address_or_localhost_is_resolved_as_ever(name):
    try:
        socket.getaddrinfo(name, 69, type=socket.SOCK_DGRAM)
    except socket.gaierror:
        pass  # a host without IPv6 or without the zone: the guard let it through


def test_a_name_under_invalid_is_not_known_without_asking_anyone():
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("no-such-host.invalid", 69)


@pytest.mark.parametrize(
    "guard",
    ["test_surface.py", "test_import_structure.py", "test_exceptions.py", "test_readme.py", "test_hints.py"],
)
def test_each_guard_module_of_the_surface_exists(guard):
    assert (TESTS / guard).is_file()
