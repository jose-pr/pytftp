"""TFTP client: download and upload over IPv4 or IPv6."""

from __future__ import annotations

import io
import os
import socket
import time
from typing import Any, BinaryIO, Callable, Dict, Optional, Tuple, Union

from .errors import ProtocolError, RemoteError, TransferTimeout
from .netascii import NetasciiReader, NetasciiWriter, encoded_size
from .options import DEFAULT_BLKSIZE, Negotiated, accept_oack, request_options
from .packet import ErrorCode, Opcode, encode_ack, encode_error, encode_request, decode
from .result import TransferResult
from ._sockets import fit_window, resolve, udp_socket
from .transfer import Receiver, Sender, Transfer, as_readinto, as_write

__all__ = ["Client", "download", "upload", "MODES"]

#: Transfer modes this library speaks. ``mail`` (obsolete since RFC 1350) is not.
MODES = ("octet", "netascii")

PathOrFile = Union[str, "os.PathLike[str]", BinaryIO]
Progress = Callable[[int, Optional[int]], Any]


def _mode(mode: str) -> str:
    mode = mode.lower()
    if mode == "binary":
        mode = "octet"
    elif mode == "ascii":
        mode = "netascii"
    if mode not in MODES:
        raise ValueError("mode must be one of %s, not %r" % (", ".join(MODES), mode))
    return mode


def _source_size(source: Any) -> Optional[int]:
    if isinstance(source, (bytes, bytearray, memoryview)):
        return len(source)
    try:
        return os.fstat(source.fileno()).st_size - source.tell()
    except (AttributeError, OSError, ValueError, io.UnsupportedOperation):
        pass
    try:
        here = source.tell()
        end = source.seek(0, os.SEEK_END)
        source.seek(here)
        return end - here
    except (AttributeError, OSError, ValueError):
        return None


class Client:
    """A TFTP client bound to one server.

    :param host: server name or address; ``[v6]`` brackets are accepted.
    :param port: server port.
    :param timeout: seconds before a retransmission. Also requested from the
        server, as ``timeout`` when whole and as ``utimeout`` when fractional,
        unless ``timeout_option`` is false.
    :param retries: retransmissions of one packet before giving up.
    :param blksize: requested DATA size, 8..65464. ``None`` asks for nothing,
        leaving RFC 1350's 512. The default 1428 fits an Ethernet frame on
        IPv4 and IPv6 without fragmenting.
    :param windowsize: requested RFC 7440 window. ``None`` asks for nothing
        (one packet per ACK).
    :param tsize: request the transfer size (RRQ) or announce it (WRQ).
    :param rollover: request block-number rollover to 0 or 1. ``None`` asks
        for nothing; wrapping to 0 is assumed and a server wrapping to 1 is
        followed.
    :param family: ``socket.AF_INET`` / ``AF_INET6`` to force one, ``0`` for
        whatever ``host`` resolves to first.
    :param local_address: ``(host, port)`` to send from.
    :param fallback: when the server refuses a request because of its options
        (ERROR 8), retry once without any.
    :param dally: after acknowledging the last DATA of a download, keep
        answering a retransmitted last DATA for one ``timeout`` (RFC 1350
        section 6). Costs that much time on every download.

    A client is not thread-safe; use one per thread.
    """

    def __init__(
        self,
        host: str,
        port: int = 69,
        *,
        timeout: float = 1.0,
        retries: int = 5,
        blksize: Optional[int] = 1428,
        windowsize: Optional[int] = None,
        tsize: bool = True,
        rollover: Optional[int] = None,
        timeout_option: bool = True,
        family: int = 0,
        local_address: Optional[Tuple[Any, ...]] = None,
        fallback: bool = True,
        dally: bool = False,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if retries < 0:
            raise ValueError("retries cannot be negative")
        # Validate the options now rather than on the first transfer.
        request_options(blksize=blksize, windowsize=windowsize, rollover=rollover)
        self.host = host
        self.port = port
        self.timeout = timeout
        self.retries = retries
        self.blksize = blksize
        self.windowsize = windowsize
        self.tsize = tsize
        self.rollover = rollover
        self.timeout_option = timeout_option
        self.family = family
        self.local_address = local_address
        self.fallback = fallback
        self.dally = dally

    # -- public API -------------------------------------------------------

    def download(
        self,
        filename: str,
        dest: PathOrFile,
        *,
        mode: str = "octet",
        progress: Optional[Progress] = None,
    ) -> TransferResult:
        """Fetch ``filename`` into ``dest`` (a path or a writable binary file).

        A path is written in place and removed again if the transfer fails.
        Raises :class:`RemoteError`, :class:`TransferTimeout` or
        :class:`ProtocolError`; ``OSError`` for local failures.
        """
        mode = _mode(mode)
        if isinstance(dest, (str, os.PathLike)):
            path = os.fspath(dest)
            fileobj = open(path, "wb")
            try:
                result = self._download(filename, fileobj, mode, progress)
            except BaseException:
                fileobj.close()
                try:
                    os.unlink(path)
                except OSError:
                    pass
                raise
            fileobj.close()
            return result
        return self._download(filename, dest, mode, progress)

    def get(self, filename: str, *, mode: str = "octet") -> bytes:
        """Fetch ``filename`` and return its contents."""
        buffer = io.BytesIO()
        self.download(filename, buffer, mode=mode)
        return buffer.getvalue()

    def upload(
        self,
        filename: str,
        source: Union[PathOrFile, bytes, bytearray, memoryview],
        *,
        mode: str = "octet",
        progress: Optional[Progress] = None,
    ) -> TransferResult:
        """Send ``source`` (a path, a readable binary file or bytes) as ``filename``."""
        mode = _mode(mode)
        if isinstance(source, (str, os.PathLike)):
            with open(os.fspath(source), "rb") as fileobj:
                return self._upload(filename, fileobj, mode, progress)
        if isinstance(source, (bytes, bytearray, memoryview)):
            return self._upload(filename, io.BytesIO(bytes(source)), mode, progress)
        return self._upload(filename, source, mode, progress)

    def put(self, filename: str, data: bytes, *, mode: str = "octet") -> TransferResult:
        """Upload ``data`` as ``filename``."""
        return self.upload(filename, data, mode=mode)

    # -- internals --------------------------------------------------------

    def _options(self, is_read: bool, size: Optional[int]) -> Dict[str, str]:
        tsize = None
        if self.tsize:
            tsize = 0 if is_read else size
        return request_options(
            blksize=self.blksize,
            windowsize=self.windowsize,
            timeout=self.timeout if self.timeout_option else None,
            tsize=tsize,
            rollover=self.rollover,
        )

    def _download(
        self, filename: str, sink: BinaryIO, mode: str, progress: Optional[Progress]
    ) -> TransferResult:
        writer: Any = NetasciiWriter(sink) if mode == "netascii" else sink
        write = as_write(writer)
        result = self._run(Opcode.RRQ, filename, mode, self._options(True, None), write, None, progress)
        if mode == "netascii":
            writer.flush()
        return result

    def _upload(
        self, filename: str, source: BinaryIO, mode: str, progress: Optional[Progress]
    ) -> TransferResult:
        size: Optional[int]
        if mode == "netascii":
            size = encoded_size(source)
            reader: Any = NetasciiReader(source)
        else:
            size = _source_size(source)
            reader = source
        read = as_readinto(reader)
        return self._run(Opcode.WRQ, filename, mode, self._options(False, size), None, read, progress)

    def _run(self, opcode, filename, mode, options, write, read, progress) -> TransferResult:
        family, server = resolve(self.host, self.port, self.family)
        bind = self.local_address or (("::", 0) if family == socket.AF_INET6 else ("0.0.0.0", 0))
        with udp_socket(family, bind) as sock:
            started = time.monotonic()
            try:
                return self._exchange(
                    sock, server, opcode, filename, mode, options, write, read, progress, started
                )
            except RemoteError as exc:
                # Only a refusal of the request itself: nothing has been
                # read or written yet, so asking again is safe.
                if (
                    exc.code == ErrorCode.OPTION_REFUSED
                    and options
                    and self.fallback
                    and getattr(exc, "_in_request", False)
                ):
                    return self._exchange(
                        sock, server, opcode, filename, mode, {}, write, read, progress, started
                    )
                raise

    def _exchange(
        self, sock, server, opcode, filename, mode, options, write, read, progress, started
    ) -> TransferResult:
        is_read = opcode == Opcode.RRQ
        request = encode_request(opcode, filename, mode, options)
        requested_blksize = int(options.get("blksize", DEFAULT_BLKSIZE))
        buf = bytearray(4 + max(requested_blksize, DEFAULT_BLKSIZE) + 1)
        view = memoryview(buf)
        recv_into = sock.recvfrom_into
        clock = time.monotonic

        fit_window(sock, requested_blksize, int(options.get("windowsize", 1)))

        # Request phase: until the server answers from its transfer ID.
        sock.sendto(request, server)
        tries = self.retries
        deadline = clock() + self.timeout
        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                tries -= 1
                if tries < 0:
                    raise TransferTimeout("no response from %s:%s" % server[:2])
                sock.sendto(request, server)
                deadline = clock() + self.timeout
                continue
            sock.settimeout(remaining)
            try:
                n, peer = recv_into(buf)
            except socket.timeout:
                continue
            except ConnectionResetError:  # pragma: no cover - see _sockets
                continue
            if peer[0] != server[0] or n < 2:
                continue  # not the server we asked
            break

        def send(packet, _sendto=sock.sendto, _peer=peer):
            return _sendto(packet, _peer)

        now = clock()
        op = buf[1] if buf[0] == 0 else -1
        session: Transfer
        if op == Opcode.ERROR:
            packet = decode(view[:n])
            refused = RemoteError(packet.code, packet.message)  # type: ignore[union-attr]
            refused._in_request = True  # type: ignore[attr-defined]
            raise refused
        if op == Opcode.OACK:
            oack = decode(view[:n]).options  # type: ignore[union-attr]
            try:
                negotiated = accept_oack(options, oack, is_read=is_read, timeout=self.timeout)
            except ProtocolError as exc:
                send(encode_error(exc.code, exc.message))
                raise
            if is_read:
                session = Receiver(send, write, negotiated, self.retries, now, reply=encode_ack(0))
            else:
                session = Sender(send, read, negotiated, self.retries, now)
        elif is_read and op == Opcode.DATA:
            negotiated = Negotiated(timeout=self.timeout)
            session = Receiver(send, write, negotiated, self.retries, now)
            session.handle(view, n, now)
        elif not is_read and op == Opcode.ACK and buf[2] == 0 and buf[3] == 0:
            negotiated = Negotiated(timeout=self.timeout)
            session = Sender(send, read, negotiated, self.retries, now)
        else:
            send(encode_error(ErrorCode.ILLEGAL_OPERATION, "unexpected opcode %d" % op))
            raise ProtocolError("unexpected opcode %d in response to the request" % op)

        if len(buf) < 4 + negotiated.blksize + 1:
            buf = bytearray(4 + negotiated.blksize + 1)
            view = memoryview(buf)
        total = negotiated.tsize
        reported = -1
        peer_host, peer_port = peer[0], peer[1]

        # Transfer phase.
        while not session.done:
            remaining = session.deadline - clock() if session.deadline is not None else self.timeout
            if remaining <= 0:
                session.on_timeout(clock())
                continue
            sock.settimeout(remaining)
            try:
                n, addr = recv_into(buf)
            except socket.timeout:
                session.on_timeout(clock())
                continue
            except ConnectionResetError:  # pragma: no cover - see _sockets
                continue
            if addr[1] != peer_port or addr[0] != peer_host:
                try:
                    sock.sendto(encode_error(ErrorCode.UNKNOWN_TID), addr)
                except OSError:
                    pass
                continue
            session.handle(view, n, clock())
            if progress is not None and session.bytes != reported:
                reported = session.bytes
                progress(reported, total)

        if session.error is not None:
            raise session.error

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
            time.monotonic() - started,
            negotiated,
        )


def download(
    host: str,
    filename: str,
    dest: PathOrFile,
    *,
    port: int = 69,
    mode: str = "octet",
    progress: Optional[Progress] = None,
    **client_options: Any,
) -> TransferResult:
    """One-shot :meth:`Client.download`; ``client_options`` go to :class:`Client`."""
    return Client(host, port, **client_options).download(filename, dest, mode=mode, progress=progress)


def upload(
    host: str,
    filename: str,
    source: Union[PathOrFile, bytes],
    *,
    port: int = 69,
    mode: str = "octet",
    progress: Optional[Progress] = None,
    **client_options: Any,
) -> TransferResult:
    """One-shot :meth:`Client.upload`; ``client_options`` go to :class:`Client`."""
    return Client(host, port, **client_options).upload(filename, source, mode=mode, progress=progress)
