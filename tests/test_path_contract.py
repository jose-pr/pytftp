"""pathapi's own contract suite, run over the two path classes a TFTP server yields.

The served directory is built on disk, because TFTP cannot create a directory
or delete a file; the path under test is the client's view of it, with the
server accepting uploads and listings. A switch below is False only for what
the protocol cannot do, and says so.
"""

from __future__ import annotations

import functools

import pytest

pytest.importorskip("pathapi")
pytest.importorskip("uripath")
_testing = pytest.importorskip("pathapi.testing")
if not all(hasattr(_testing, name) for name in ("FIXTURE_TREE", "PathContract", "populate_fixture_tree")):
    pytest.skip("this pathapi ships no contract suite with a fixture tree", allow_module_level=True)

import tftp  # noqa: E402
from conftest import client_for  # noqa: E402
from pathapi import LocalPath  # noqa: E402
from pathapi.testing import (
    FIXTURE_TREE,
    PathContract,
    populate_fixture_tree,
)  # noqa: E402
from uripath import URIPath  # noqa: E402

LISTING = tftp.TFTPServerOptions(allowed=tftp.options.STANDARD_OPTIONS | tftp.options.LISTING_OPTIONS)


def cannot(contract_test, reason):
    """``contract_test`` expected to fail, for ``reason``; strict, so a fix forces the marker out."""

    @functools.wraps(contract_test)
    def wrapper(self, root):
        return contract_test(self, root)

    return pytest.mark.xfail(strict=True, reason=reason)(wrapper)


@pytest.fixture
def served_root(tmp_path, make_server):
    """``(client, base URL)`` of a writable listing server over a directory holding the contract's tree."""
    directory = tmp_path / "served"
    directory.mkdir()
    populate_fixture_tree(LocalPath(directory))
    server = make_server(directory, writable=True, overwrite=True, options=LISTING)
    return client_for(server, timeout=1), "tftp://127.0.0.1:%d/" % server.server_address[1]


#: Contract tests TFTP cannot pass, for the reason beside each, that no capability switch covers.
GAPS = {
    "test_read_directory_raises": "a server answers ERROR 1 for a directory it will not send, so a read raises FileNotFoundError",
}


def with_gaps(cls):
    """``cls`` with each test of :data:`GAPS` expected to fail."""
    for name, reason in GAPS.items():
        setattr(cls, name, cannot(getattr(PathContract, name), reason))
    return cls


class _TFTPCannot:
    """What supports() cannot say: the protocols TFTP lacks (rename, mkdir, delete, utime) and the
    server's listing are the contract's own defaults from supports(), so they are not set here."""

    supports_append = False  # a write replaces the file; there is no append
    enforces_directory_hierarchy = False  # a server may create nothing but a file: no mkdir exists
    supports_move = False  # move() refuses a source that cannot be deleted, before it copies


@with_gaps
class TestTFTPPath(_TFTPCannot, PathContract):
    @pytest.fixture
    def root(self, served_root):
        client, _ = served_root
        return client.path("")


@with_gaps
class TestTFTPURIPath(_TFTPCannot, PathContract):
    @pytest.fixture
    def root(self, served_root):
        _, base = served_root
        return URIPath(base)


def test_the_tree_the_contract_expects_is_what_the_server_holds(served_root, tmp_path):
    client, _ = served_root
    names = {entry.name for entry in client.listdir("")}
    assert names == {name for name in FIXTURE_TREE if "/" not in name}
