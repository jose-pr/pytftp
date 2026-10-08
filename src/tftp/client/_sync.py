"""The synchronous client: blocking sockets, one transfer at a time."""

from __future__ import annotations

import errno
import io
import os
import socket
import time
from typing import TYPE_CHECKING, Any, Callable, List, Mapping, Optional, Tuple, Union

from .. import listing
from ..listing import ListEntry
from ..netascii import NetasciiReader, NetasciiWriter, encoded_size
from ..options._handler import DEFAULT_BLKSIZE
from ..packet._enums import TFTPErrorCode, TFTPOpcode
from ..packet._codec import encode_ack
from ..packet._codec import _encode_error
from .._result import TransferResult
from .._sockets import fit_window
from ..transfer._receiver import Receiver
from ..transfer._requester import Requester
from ..transfer._sender import Sender
from ..transfer._engine import Transfer, as_readinto, as_write
from ._core import (
    _RECV_BUFFER,
    LISTING_LIMIT,
    SinkLike,
    SourceLike,
    ProgressFunction,
    RemoteStat,
    _ClientBase,
    _mode,
    _bounded,
    _failure,
    _NotListing,
    _PathSink,
    _source_size,
)

if TYPE_CHECKING:  # netimps and pathlib_next are imported lazily at run time
    from netimps import HostLike

    from ..path._local import TFTPPath

__all__ = ["TFTPClient", "download", "upload"]


class TFTPClient(_ClientBase):
    """A TFTP client bound to one server.

    :param host: server name or address: a string (``"name"``, ``"10.0.0.1"``,
        ``"[v6]:port"``), an ``ipaddress`` address or interface, or a
        ``netimps.Host``. Only a string can carry a port.
    :param port: server port.
    :param timeout: seconds before a retransmission. Also requested from the
        server, as ``timeout`` when whole and as ``utimeout`` when fractional,
        unless ``timeout_option`` is false.
    :param retries: retransmissions of one packet before giving up.
    :param blksize: requested DATA size, 8..65464. ``None`` asks for nothing,
        leaving RFC 1350's 512. The default 1428 fits an Ethernet frame on
        IPv4 and IPv6 without fragmenting. ``"mtu"`` sizes it to the MTU of
        the interface the route to the server uses (falling back to 1428).
    :param windowsize: requested RFC 7440 window. ``None`` asks for nothing
        (one packet per ACK).
    :param tsize: request the transfer size (RRQ) or announce it (WRQ).
    :param rollover: request block-number rollover to 0 or 1. ``None`` asks
        for nothing; wrapping to 0 is assumed and a server wrapping to 1 is
        followed.
    :param family: ``socket.AF_INET`` / ``AF_INET6`` to force one, ``0`` for
        whatever ``host`` resolves to first.
    :param src: ``(address, port)`` to send from; the address as for ``host``.
    :param fallback: when the server answers a request that carries options
        with ERROR 8, 4 or 0 (RFC 2347: it "may" be repeated without them),
        retry once without any.
    :param dally: after acknowledging the last DATA of a download, keep
        answering a retransmitted last DATA for one ``timeout`` (RFC 1350
        section 6). Costs that much time on every download.
    :param backoff: each consecutive retransmission waits this many times
        longer (RFC 1123 4.2.3.2); progress resets it to ``timeout``.
    :param max_timeout: ceiling for the backed-off wait, at least ``timeout``;
        ``None`` is eight times ``timeout``.
    :param deadline: seconds a whole transfer may take, or ``None``. A
        relative duration, unlike the engine's ``deadline`` attribute.
    :param max_size: octets a download may bring, or ``None`` for no bound. A
        server announcing more, or sending more than it announced or than
        this, is sent ERROR 3 and ends the download with
        :class:`TransferTooLargeError` (or :class:`TFTPProtocolError` for
        more than it announced).
    :param utimeout: send a fractional ``timeout`` as tftp-hpa's ``utimeout``
        (otherwise a fractional timeout is not requested at all).
    :param extra_options: further options to request, verbatim (extensions
        such as ``blksize2``/``cookie``, or custom ones). Built-in ones are
        validated in the OACK; custom ones land in ``negotiated.extra``.
    :param registry: the :class:`OptionRegistry` used to validate the OACK.
    :param trace: ``trace(PacketEvent)`` for every datagram this client sends
        or receives (``role="client"``, one ``session`` id per transfer).
    :param on_negotiated: called as ``on_negotiated(negotiated, peer)`` once
        the server has answered the request (OACK, first DATA or ACK 0),
        before any data moves -- the peer is the server's transfer address.
    :param strict_source: the first answer must come from the address the
        request was sent to. ``False`` accepts any address, for multi-homed
        servers that answer from another one (the transfer then locks on to
        whichever answered first).

    A client is not thread-safe; use one per thread.
    """

    # -- public API -------------------------------------------------------

    def download(
        self,
        filename: str,
        dst: SinkLike,
        *,
        mode: str = "octet",
        progress: Optional[ProgressFunction] = None,
        max_size: Optional[int] = None,
    ) -> TransferResult:
        """Fetch ``filename`` into ``dst`` (a path or a writable binary file).

        A path is written to a temporary file beside it, which replaces it when
        the transfer succeeds: a download that fails leaves what was there. A
        path that exists and is not a regular file (a device, a pipe) is
        written in place. ``max_size`` overrides the client's.
        Raises :class:`RemoteError`, :class:`TransferTimeoutError`,
        :class:`TransferTooLargeError` or :class:`TFTPProtocolError`;
        ``OSError`` for local failures.
        """
        mode = _mode(mode)
        limit = self._limit(max_size)
        if isinstance(dst, (str, os.PathLike)):
            target = _PathSink(dst)
            try:
                result = self._download(filename, target.sink, mode, progress, limit)
                target.commit()
            except BaseException:
                target.abort()
                raise
            return result
        return self._download(filename, dst, mode, progress, limit)

    def path(self, *segments: Any, mode: str = "octet") -> TFTPPath:
        """A :class:`tftp.path.TFTPPath` on this server (needs the ``path`` extra)."""
        from ..path._local import TFTPPath

        return TFTPPath(*segments, client=self, mode=mode)

    def get(self, filename: str, *, mode: str = "octet", max_size: Optional[int] = None) -> bytes:
        """Fetch ``filename`` and return its contents."""
        buffer = io.BytesIO()
        self.download(filename, buffer, mode=mode, max_size=max_size)
        return buffer.getvalue()

    def upload(
        self,
        filename: str,
        src: SourceLike,
        *,
        mode: str = "octet",
        progress: Optional[ProgressFunction] = None,
    ) -> TransferResult:
        """Send ``src`` (a path, a readable binary file or bytes) as ``filename``."""
        mode = _mode(mode)
        if isinstance(src, (str, os.PathLike)):
            with open(os.fspath(src), "rb") as fileobj:
                return self._upload(filename, fileobj, mode, progress)
        if isinstance(src, (bytes, bytearray, memoryview)):
            return self._upload(filename, io.BytesIO(bytes(src)), mode, progress)
        return self._upload(filename, src, mode, progress)

    def put(self, filename: str, data: bytes, *, mode: str = "octet") -> TransferResult:
        """Upload ``data`` as ``filename``."""
        return self.upload(filename, data, mode=mode)

    # -- internals --------------------------------------------------------

    def _download(
        self,
        filename: str,
        sink: Any,
        mode: str,
        progress: Optional[ProgressFunction],
        limit: Optional[int],
    ) -> TransferResult:
        writer: Any = NetasciiWriter(sink) if mode == "netascii" else sink
        write = as_write(writer)
        result = self._run(TFTPOpcode.RRQ, filename, mode, None, write, None, progress, limit)
        if mode == "netascii":
            writer.flush()
        return result

    def _upload(
        self, filename: str, source: Any, mode: str, progress: Optional[ProgressFunction]
    ) -> TransferResult:
        size: Optional[int]
        if mode == "netascii":
            size = encoded_size(source)
            reader: Any = NetasciiReader(source)
        else:
            size = _source_size(source)
            reader = source
        read = as_readinto(reader)
        return self._run(TFTPOpcode.WRQ, filename, mode, size, None, read, progress, None)

    def _run(
        self,
        opcode: int,
        filename: str,
        mode: str,
        size: Optional[int],
        write: Optional[Callable[[Union[bytes, memoryview]], object]],
        read: Optional[Callable[[memoryview], int]],
        progress: Optional[ProgressFunction],
        limit: Optional[int],
    ) -> TransferResult:
        family, server, address = self._endpoint()
        options = self._options(opcode == TFTPOpcode.RRQ, size, address)
        with self._socket(family) as sock:
            started, timed = time.monotonic(), time.perf_counter()  # the deadline's clock, the duration's
            expires = self._expires(started)
            opened, emit = self._open(sock, server, opcode, filename, mode, options, expires)
            if opened.retry_without_options:
                # Only a refusal of the request itself: nothing has been read or
                # written yet, so asking again is safe.
                opened, emit = self._open(sock, server, opcode, filename, mode, {}, expires)
            if opened.error is not None:
                raise _failure(opened.error)
            return self._exchange(
                sock, opcode, filename, mode, opened, emit, write, read, progress, timed, expires, limit
            )

    def size(self, filename: str, *, mode: str = "octet") -> Optional[int]:
        """The size of ``filename`` on the server, without transferring it.

        Sends an RRQ asking only for ``tsize`` and abandons the transfer as
        soon as the server answers (ERROR 8 to an OACK, as RFC 2347 lets a
        client refuse one). Returns ``None`` when the server does not report
        sizes -- unless the whole file fits in the first block, whose length
        then is the size. Raises :class:`RemoteError` (``FileNotFound``...)
        like a download would.
        """
        return self._size(filename, mode, self._expires(time.monotonic()))

    def stat(self, filename: str, *, mode: str = "octet") -> RemoteStat:
        """Size, modification time and kind of ``filename``, in one probe.

        Like :meth:`size`, with ``x-mtime`` and ``x-list`` asked for as well:
        a pytftp server allowing them reports the time and recognises a
        directory; any other server ignores them (RFC 2347). A server that
        refuses the request over its options (ERROR 8) is probed again for
        the size alone, when ``fallback`` is on.
        """
        return self._stat(filename, mode, self._expires(time.monotonic()))

    def listdir(self, dirname: str = "", *, max_size: Optional[int] = LISTING_LIMIT) -> List[ListEntry]:
        """The entries of directory ``dirname`` (``""`` is the server's root).

        A listing longer than ``max_size`` octets (16 MiB) raises
        :class:`TransferTooLargeError`.

        Needs a server speaking pytftp's ``x-list`` extension (``tftp.TFTPServer``
        allowing :data:`LISTING_OPTIONS`); there is no standard way to list
        in TFTP. Raises ``NotADirectoryError`` when the name is a file (the
        transfer is abandoned at once), and :class:`FileNotFound` when it does
        not exist -- which is also what a server without listing support
        answers for a directory.
        """
        lister = self._lister()
        sink = io.BytesIO()
        try:
            lister.download(dirname or ".", sink, max_size=max_size)
        except _NotListing:
            raise NotADirectoryError(errno.ENOTDIR, "not a directory", dirname) from None
        return listing.loads(sink.getvalue())

    def _open(
        self,
        sock: socket.socket,
        server: Tuple[Any, ...],
        opcode: int,
        filename: str,
        mode: str,
        options: Mapping[str, str],
        expires: Optional[float],
    ) -> Tuple[Requester, Optional[Callable[[Any, str, Tuple[Any, ...]], None]]]:
        """Send the request until the server answers: the exchange, and the trace emitter of this attempt."""
        fit_window(sock, int(options.get("blksize", DEFAULT_BLKSIZE)), int(options.get("windowsize", 1)))
        emit = self._emitter(sock, server)
        requester = self._requester(
            self._sender(sock, emit), server, opcode, filename, mode, options, time.monotonic(), expires
        )
        return self._await(sock, requester, emit), emit

    def _exchange(
        self,
        sock: socket.socket,
        opcode: int,
        filename: str,
        mode: str,
        opened: Requester,
        emit: Optional[Callable[[Any, str, Tuple[Any, ...]], None]],
        write: Optional[Callable[[Union[bytes, memoryview]], object]],
        read: Optional[Callable[[memoryview], int]],
        progress: Optional[ProgressFunction],
        timed: float,
        expires: Optional[float],
        limit: Optional[int],
    ) -> TransferResult:
        is_read = opcode == TFTPOpcode.RRQ
        # Whatever a datagram's length: Windows reports one longer than the
        # buffer as an error before the sender can be looked at, and a longer
        # DATA than negotiated is the engine's to judge.
        buf = bytearray(_RECV_BUFFER)
        view = memoryview(buf)
        recv_into = sock.recvfrom_into
        clock = time.monotonic
        peer = opened.peer
        negotiated = opened.negotiated
        assert peer is not None and negotiated is not None  # an answer that was accepted

        if emit is None:

            def send(
                packet: Union[bytes, memoryview],
                _sendto: Callable[..., int] = sock.sendto,
                _peer: Tuple[Any, ...] = peer,
            ) -> int:
                return _sendto(packet, _peer)

        else:

            def send(
                packet: Union[bytes, memoryview],
                _sendto: Callable[..., int] = sock.sendto,
                _peer: Tuple[Any, ...] = peer,
            ) -> int:
                result = _sendto(packet, _peer)
                emit(packet, "out", _peer)
                return result

        session: Transfer

        # Before the engine exists: building a Sender already reads the first
        # block, and a caller may need to know the outcome before that.
        self._negotiated(negotiated, peer, send)
        now = clock()
        if is_read:
            self._admit(negotiated, send, limit)
            assert write is not None  # a download is given its sink
            announced = negotiated.tsize if mode == "octet" else None
            if limit is not None or announced is not None:
                write = _bounded(write, limit, announced)
            first_data = opened.first_data
            reply = None if first_data else encode_ack(0)
            session = Receiver(
                send,
                write,
                negotiated,
                self.retries,
                now,
                reply=reply,
                backoff=self.backoff,
                max_timeout=self.max_timeout,
                expires=expires,
            )
            if first_data:
                session.handle(memoryview(first_data), len(first_data), now)
        else:
            assert read is not None  # an upload is given its source
            session = Sender(
                send,
                read,
                negotiated,
                self.retries,
                now,
                backoff=self.backoff,
                max_timeout=self.max_timeout,
                expires=expires,
            )

        total = negotiated.tsize
        reported = -1
        peer_host, peer_port = peer[0], peer[1]

        # An interruption (an exception from a callback, a signal) tells the peer.
        try:
            if progress is not None and session.bytes:  # DATA 1 was handled above
                reported = session.bytes
                progress(reported, total)

            # Transfer phase.
            while not session.is_done:
                if session.is_stalled:
                    # A non-blocking local source/sink had nothing ready: poll it.
                    session.resume(clock())
                    if session.is_stalled:
                        remaining = 0.01
                        if session.deadline is not None and session.deadline <= clock():
                            session.on_timeout(clock())
                            continue
                    else:
                        continue
                elif session.deadline is not None:
                    remaining = session.deadline - clock()
                else:
                    remaining = self.timeout
                if remaining <= 0:
                    session.on_timeout(clock())
                    continue
                sock.settimeout(remaining)
                try:
                    n, addr = recv_into(buf)
                except socket.timeout:
                    if not session.is_stalled:
                        session.on_timeout(clock())
                    continue
                except ConnectionResetError:  # pragma: no cover - connreset is off
                    continue
                if emit is not None:
                    emit(view[:n], "in", addr)
                if addr[1] != peer_port or addr[0] != peer_host:
                    try:
                        stray = _encode_error(TFTPErrorCode.UNKNOWN_TID)
                        sock.sendto(stray, addr)
                        if emit is not None:
                            emit(stray, "out", addr)
                    except OSError:
                        pass
                    continue
                session.handle(view, n, clock())
                if progress is not None and session.bytes != reported:
                    reported = session.bytes
                    progress(reported, total)
        finally:
            if not session.is_done:
                session.abort("cancelled")

        if session.error is not None:
            raise _failure(session.error)

        if is_read and self.dally:
            linger = clock() + negotiated.timeout
            while True:
                remaining = linger - clock()
                if remaining <= 0:
                    break
                sock.settimeout(remaining)
                try:
                    n, addr = recv_into(buf)
                except (socket.timeout, ConnectionResetError):
                    break
                if emit is not None:
                    emit(view[:n], "in", addr)
                if addr[1] == peer_port and addr[0] == peer_host:
                    session.handle(view, n, clock())

        return TransferResult(
            filename,
            "read" if is_read else "write",
            mode,
            peer,
            sock.getsockname(),
            session.bytes,
            session.blocks,
            session.retransmits,
            time.perf_counter() - timed,
            negotiated,
        )


def download(
    host: "HostLike",
    filename: str,
    dst: SinkLike,
    /,
    *,
    port: int = 69,
    mode: str = "octet",
    progress: Optional[ProgressFunction] = None,
    **client_options: Any,
) -> TransferResult:
    """One-shot :meth:`TFTPClient.download`; ``client_options`` go to :class:`TFTPClient`."""
    return TFTPClient(host, port, **client_options).download(filename, dst, mode=mode, progress=progress)


def upload(
    host: "HostLike",
    filename: str,
    src: SourceLike,
    /,
    *,
    port: int = 69,
    mode: str = "octet",
    progress: Optional[ProgressFunction] = None,
    **client_options: Any,
) -> TransferResult:
    """One-shot :meth:`TFTPClient.upload`; ``client_options`` go to :class:`TFTPClient`."""
    return TFTPClient(host, port, **client_options).upload(filename, src, mode=mode, progress=progress)
