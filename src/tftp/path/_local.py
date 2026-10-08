"""``TFTPPath``: a pathlib_next ``Path`` for files on one TFTP server."""

from __future__ import annotations

import copy
import errno
import posixpath
from typing import Any, Callable, Iterator, List, Optional, Tuple

from pathlib_next import Path, Pathname
from pathlib_next.utils.stat import FileStat

from ..client._sync import TFTPClient
from ..client._core import _ClientBase, _mode
from ..exceptions import FileNotFound, TFTPError
from .._uri import TFTPURL
from ._stream import open_reader, open_writer, os_error

__all__ = ["TFTPPath", "client_factory", "tftp_stat", "tftp_open", "tftp_scandir", "tftp_unlink"]


def check_client(client: Any) -> Any:
    """``client``, if it is the synchronous :class:`TFTPClient`; ``TypeError`` for an asyncio one.

    A path's reads and writes run in the calling thread. An asyncio client's
    methods return coroutines that nothing here would await, so every
    operation would appear to succeed having sent nothing.
    """
    if isinstance(client, _ClientBase) and not isinstance(client, TFTPClient):
        raise TypeError(
            "a path needs the synchronous tftp.TFTPClient, not %s, whose methods are coroutines"
            % type(client).__name__
        )
    return client


def client_factory(client: TFTPClient) -> Callable[..., TFTPClient]:
    """A function returning a copy of ``client`` with some settings changed.

    An ``on_negotiated`` override runs after the client's own hook, which stays.
    """

    def make(**overrides: Any) -> TFTPClient:
        derived = copy.copy(client)
        own, outer = overrides.get("on_negotiated"), client.on_negotiated
        if own is not None and outer is not None:

            def chained(negotiated: Any, peer: Any) -> None:
                outer(negotiated, peer)
                own(negotiated, peer)

            overrides["on_negotiated"] = chained
        for name, value in overrides.items():
            setattr(derived, name, value)
        return derived

    return make


def tftp_stat(client: TFTPClient, filename: str, mode: str, path: Any) -> FileStat:
    """A ``FileStat`` from :meth:`TFTPClient.stat`: one probe, nothing transferred.

    ``st_size`` is 0 and ``st_mtime`` 0 when the server reports none; a
    directory is recognised only by a server speaking ``x-list``.
    """
    try:
        info = client.stat(filename, mode=mode)
    except TFTPError as exc:
        raise os_error(exc, path) from None
    return FileStat(st_size=info.size or 0, st_mtime=info.mtime or 0, is_dir=info.is_dir)


def tftp_scandir(client: TFTPClient, dirname: str, path: Any) -> Iterator[Tuple[str, FileStat]]:
    """``(name, FileStat)`` per entry, from one ``x-list`` listing."""
    try:
        entries = client.listdir(dirname)
    except TFTPError as exc:
        raise os_error(exc, path) from None
    for entry in entries:
        yield entry.name, FileStat(st_size=entry.size, st_mtime=entry.mtime or 0, is_dir=entry.is_dir)


def tftp_unlink(path: Any, missing_ok: bool) -> None:
    """``unlink()`` for a TFTP path, which cannot delete.

    ``missing_ok=True`` returns without doing anything: it is how
    :meth:`pathlib_next.Path.copy` asks a target to make way, and the write
    that follows replaces the file. A plain call, a deletion nobody can do,
    raises ``NotImplementedError``.
    """
    if missing_ok:
        return
    raise NotImplementedError("TFTP cannot delete a file")


def tftp_open(client: TFTPClient, filename: str, transfer_mode: str, mode: str, path: Any) -> Any:
    """pathlib_next's ``_open()``: ``r`` streams a download, ``w``/``x`` an upload.

    ``x`` checks existence with a size probe first, which is not atomic: TFTP
    has no exclusive create. ``a`` and ``+`` modes cannot be expressed in TFTP.
    """
    factory = client_factory(client)
    if mode == "r":
        return open_reader(factory, filename, transfer_mode, path)
    if mode == "x":
        try:
            client.size(filename, mode=transfer_mode)
        except FileNotFound:
            pass
        except TFTPError as exc:
            raise os_error(exc, path) from None
        else:
            raise FileExistsError(errno.EEXIST, "file exists", str(path))
        return open_writer(factory, filename, transfer_mode, path)
    if mode == "w":
        return open_writer(factory, filename, transfer_mode, path)
    raise NotImplementedError("TFTP can only read or write a whole file, not %r" % mode)


class TFTPPath(Path):
    """A file on a TFTP server, addressed like a ``PurePosixPath``.

    ``TFTPPath("boot/pxelinux.0", client=tftp.TFTPClient("192.0.2.1"))``, or
    ``client.path("boot", "pxelinux.0")``. The path text is the filename sent
    to the server, so ``/boot/x`` and ``boot/x`` stay distinct (some servers
    resolve them differently). ``mode="netascii"`` selects the transfer mode.

    TFTP can read and write whole files: ``open("r")``,
    ``open("w")``/``"x"``, ``read_bytes``/``write_bytes``/``read_text``/
    ``write_text``, ``stat()`` (a probe), ``exists()``, ``is_file()``, and
    ``copy()`` to and from any pathlib_next path and ``move()`` to one. Against a server
    speaking pytftp's ``x-list``/``x-mtime`` extensions, ``iterdir()``,
    ``is_dir()``, ``walk()``, ``glob()`` and ``st_mtime`` work too (one
    listing per directory); other servers report directories as missing.
    Deleting, renaming, creating directories and permissions raise
    ``NotImplementedError``.
    """

    __slots__ = ("_client", "_segments", "_mode")

    _client: TFTPClient
    _segments: List[str]
    _mode: str

    def __init__(self, *segments: Any, client: Optional[TFTPClient] = None, mode: str = "octet") -> None:
        text = ""
        inherited = None
        for segment in segments:
            if isinstance(segment, TFTPPath):
                piece = segment.as_posix()
                inherited = segment
            elif isinstance(segment, Pathname):
                piece = "/".join(segment.segments)
            elif isinstance(segment, str):
                piece = segment
            else:
                raise TypeError("argument should be a str or a Pathname, not %r" % type(segment).__name__)
            piece = piece.replace("\\", "/")
            if piece.startswith("/") or not text:
                text = piece
            elif piece:
                text = "%s/%s" % (text, piece)
        if client is None:
            if inherited is None:
                raise TypeError("TFTPPath needs client= (or a TFTPPath to join onto)")
            client, mode = inherited._client, inherited._mode
        if not isinstance(mode, str):
            raise TypeError("a transfer mode is text, not %s" % type(mode).__name__)
        self._client = check_client(client)
        self._mode = _mode(mode)
        names = [name for name in text.split("/") if name and name != "."]
        if text.startswith("/"):
            self._segments = ["", *names] if names else ["", ""]
        else:
            self._segments = names

    # -- pure path ---------------------------------------------------------------

    @property
    def client(self) -> TFTPClient:
        return self._client

    @property
    def transfer_mode(self) -> str:
        return self._mode

    @property
    def segments(self) -> List[str]:
        return self._segments

    @property
    def parts(self) -> Tuple[Any, ...]:
        return tuple(self._segments)

    @property
    def parent(self) -> "TFTPPath":
        segments = self._segments
        if not segments or segments == ["", ""]:
            return self
        if len(segments) == 2 and segments[0] == "":
            return self.with_segments("", "")
        return self.with_segments(*segments[:-1])

    def with_segments(self, *segments: Any) -> "TFTPPath":
        if all(isinstance(segment, str) for segment in segments):
            segments = ("/".join(segments),)
        return type(self)(*segments, client=self._client, mode=self._mode)

    def with_client(self, client: TFTPClient) -> "TFTPPath":
        return type(self)(self.as_posix(), client=client, mode=self._mode)

    def with_mode(self, mode: str) -> "TFTPPath":
        return type(self)(self.as_posix(), client=self._client, mode=mode)

    def is_absolute(self) -> bool:
        return bool(self._segments) and self._segments[0] == ""

    def relative_to(self, other: Any) -> "TFTPPath":
        base = other.as_posix() if isinstance(other, Pathname) else str(other)
        relative = posixpath.relpath(self.as_posix() or ".", base or ".")
        if relative == ".." or relative.startswith("../"):
            raise ValueError("%r is not in the subpath of %r" % (self.as_posix(), base))
        return self.with_segments(relative)

    def as_posix(self) -> str:
        if self._segments == ["", ""]:
            return "/"
        return "/".join(self._segments)

    def as_uri(self) -> str:
        return str(TFTPURL(str(self._client.host), self._client.port, self.as_posix(), self._mode))

    def __str__(self) -> str:
        return self.as_posix()

    def __repr__(self) -> str:
        return "TFTPPath(%r, server=%s:%s)" % (self.as_posix(), self._client.host, self._client.port)

    def _endpoint(self) -> Tuple[str, int]:
        return (str(self._client.host), int(self._client.port))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TFTPPath):
            return NotImplemented
        return self._segments == other._segments and self._endpoint() == other._endpoint()

    def __hash__(self) -> int:
        return hash((tuple(self._segments), self._endpoint()))

    def _same_filesystem(self, other: Path) -> bool:
        return isinstance(other, TFTPPath) and self._endpoint() == other._endpoint()

    # -- I/O -------------------------------------------------------------------------

    def stat(self, *, follow_symlinks: bool = True) -> FileStat:
        return tftp_stat(self._client, self.as_posix(), self._mode, self)

    def _open(self, mode: str = "r", buffering: int = -1) -> Any:
        return tftp_open(self._client, self.as_posix(), self._mode, mode, self)

    def unlink(self, missing_ok: bool = False) -> None:
        tftp_unlink(self, missing_ok)

    def _scandir(self) -> Iterator[Tuple[str, FileStat]]:
        return tftp_scandir(self._client, self.as_posix(), self)

    def iterdir(self) -> Iterator["TFTPPath"]:
        for name, _ in self._scandir():
            yield self / name
