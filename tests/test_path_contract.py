"""pathlib_next's own contract suite, run over the two path classes a TFTP server yields.

The served directory is built on disk, because TFTP cannot create a directory
or delete a file; the path under test is the client's view of it, with the
server accepting uploads and listings. A switch below is False only for what
the protocol cannot do, and says so.
"""

from __future__ import annotations

import functools

import pytest

pytest.importorskip("pathlib_next")
pytest.importorskip("uritools")
if not hasattr(pytest.importorskip("pathlib_next.testing"), "PathContract"):
    pytest.skip("this pathlib_next ships no contract suite", allow_module_level=True)

import tftp  # noqa: E402
from conftest import client_for  # noqa: E402
from pathlib_next import LocalPath  # noqa: E402
from pathlib_next.testing import (
    FIXTURE_TREE,
    PathContract,
    populate_fixture_tree,
)  # noqa: E402
from pathlib_next.uri import UriPath  # noqa: E402

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


_NO_DIRECTORY = "TFTP has no request that creates a directory"
_NO_DELETE = "TFTP has no request that deletes a file or a directory"
_NO_MOVE = "a move is a copy and a delete, and TFTP cannot delete"

#: Contract tests TFTP cannot pass, for the reason beside each. pathlib_next has no capability
#: switch for them (mkdir, delete, move, and "reading a directory raises a directory error").
GAPS = {
    "test_read_directory_raises": "a server answers ERROR 1 for a directory it will not send, so a read raises FileNotFoundError",
    "test_mkdir_and_is_dir": _NO_DIRECTORY,
    "test_mkdir_existing_raises_file_exists": _NO_DIRECTORY,
    "test_mkdir_parents": _NO_DIRECTORY,
    "test_copy_recursive": _NO_DIRECTORY,
    "test_listing_reflects_writes": _NO_DIRECTORY,
    "test_unlink": _NO_DELETE,
    "test_unlink_missing_raises_then_missing_ok": _NO_DELETE,
    "test_unlink_directory_raises": _NO_DELETE,
    "test_rmdir_requires_empty": _NO_DELETE,
    "test_rmdir_missing_raises_file_not_found": _NO_DELETE,
    "test_rmdir_file_raises_not_a_directory": _NO_DELETE,
    "test_rm_recursive": _NO_DELETE,
    "test_rm_non_recursive_directory_requires_empty": _NO_DELETE,
    "test_move": _NO_MOVE,
    "test_move_existing_target_raises_without_overwrite": _NO_MOVE,
    "test_move_directory": _NO_MOVE,
}


def with_gaps(cls):
    """``cls`` with each test of :data:`GAPS` expected to fail."""
    for name, reason in GAPS.items():
        setattr(cls, name, cannot(getattr(PathContract, name), reason))
    return cls


class _TFTPCannot:
    """What no TFTP client can do, whichever way it is spelled."""

    supports_rename = False  # no rename request exists
    supports_append = False  # a write replaces the file; there is no append
    enforces_directory_hierarchy = False  # a server may create nothing but a file: no mkdir exists


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
        return UriPath(base)


def test_the_tree_the_contract_expects_is_what_the_server_holds(served_root, tmp_path):
    client, _ = served_root
    names = {entry.name for entry in client.listdir("")}
    assert names == {name for name in FIXTURE_TREE if "/" not in name}
