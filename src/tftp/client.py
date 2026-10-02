"""TFTP client: download and upload over IPv4 or IPv6."""

from __future__ import annotations

import io
import logging
import os
import socket
import time
from typing import Any, BinaryIO, Callable, Dict, Mapping, Optional, Tuple, Union

from .errors import ProtocolError, RemoteError, TransferTimeout
from .netascii import NetasciiReader, NetasciiWriter, encoded_size
from .options import (
    DEFAULT_BLKSIZE,
    MAX_BLKSIZE,
    MIN_BLKSIZE,
    Negotiated,
    OptionRegistry,
    accept_oack,
    request_options,
)
from .packet import ErrorCode, Opcode, encode_ack, encode_error, encode_request, decode
from .result import TransferResult
from ._sockets import fit_window
from .capture.events import PacketEvent, new_session_id
from .transfer import Receiver, Sender, Transfer, as_readinto, as_write

__all__ = ["Client", "download", "upload", "MODES"]

log = logging.getLogger("tftp.client")

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
    size = getattr(source, "size", None)
    if isinstance(size, int) and not isinstance(size, bool):
        return size
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


def _mtu_blksize(server: Any) -> int:
    """The largest blksize that fits the MTU toward ``server``, or 1428."""
    from netimps import get_source_ip, interface_for

    try:
        source = get_source_ip(str(server), ipv6=server.version == 6)
        iface = interface_for(source) if source is not None else None
    except (OSError, ValueError):
        iface = None
    if iface is None or not iface.mtu:
        return 1428
    # IP header, UDP header (8), TFTP DATA header (4).
    fits = iface.mtu - (40 if server.version == 6 else 20) - 8 - 4
    return max(MIN_BLKSIZE, min(fits, MAX_BLKSIZE))


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
    :param local_address: ``(host, port)`` to send from.
    :param fallback: when the server refuses a request because of its options
        (ERROR 8), retry once without any.
    :param dally: after acknowledging the last DATA of a download, keep
        answering a retransmitted last DATA for one ``timeout`` (RFC 1350
        section 6). Costs that much time on every download.
    :param backoff: each consecutive retransmission waits this many times
        longer (RFC 1123 4.2.3.2); progress resets it to ``timeout``.
    :param max_timeout: ceiling for the backed-off wait; ``None`` is eight
        times ``timeout``.
    :param max_duration: seconds a whole transfer may take, or ``None``.
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

    def __init__(
        self,
        host: str,
        port: int = 69,
        *,
        timeout: float = 1.0,
        retries: int = 5,
        blksize: Union[int, str, None] = 1428,
        windowsize: Optional[int] = None,
        tsize: bool = True,
        rollover: Optional[int] = None,
        timeout_option: bool = True,
        family: int = 0,
        local_address: Optional[Tuple[Any, ...]] = None,
        fallback: bool = True,
        dally: bool = False,
        backoff: float = 2.0,
        max_timeout: Optional[float] = None,
        max_duration: Optional[float] = None,
        strict_source: bool = True,
        utimeout: bool = False,
        extra_options: Optional[Mapping[str, object]] = None,
        registry: Optional[OptionRegistry] = None,
        on_negotiated: Optional[Callable[[Negotiated, Tuple[Any, ...]], Any]] = None,
        trace: Optional[Callable[[PacketEvent], Any]] = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if retries < 0:
            raise ValueError("retries cannot be negative")
        # Validate the options now rather than on the first transfer.
        if blksize == "mtu":
            pass
        elif isinstance(blksize, str):
            raise ValueError("blksize must be an int, None or 'mtu'")
        request_options(
            blksize=blksize if blksize != "mtu" else None,  # type: ignore[arg-type]
            windowsize=windowsize,
            rollover=rollover,
            extra=extra_options,
        )
        self.host = host if isinstance(host, str) else str(host)  # an ipaddress object is fine too
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
        self.backoff = max(1.0, backoff)
        self.max_timeout = max_timeout if max_timeout is not None else timeout * 8
        self.max_duration = max_duration
        self.strict_source = strict_source
        self.utimeout = utimeout
        self.extra_options = dict(extra_options or {})
        self.registry = registry
        self.on_negotiated = on_negotiated
        self.trace = trace

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

    def path(self, *segments: Any, mode: str = "octet") -> Any:
        """A :class:`tftp.path.TftpPath` on this server (needs the ``path`` extra)."""
        from .path import TftpPath

        return TftpPath(*segments, client=self, mode=mode)

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

    def _options(self, is_read: bool, size: Optional[int], server: Any = None) -> Dict[str, str]:
        tsize = None
        if self.tsize:
            tsize = 0 if is_read else size
        blksize = self.blksize
        if blksize == "mtu":
            blksize = _mtu_blksize(server)
        return request_options(
            blksize=blksize,  # type: ignore[arg-type]
            windowsize=self.windowsize,
            timeout=self.timeout if self.timeout_option else None,
            tsize=tsize,
            rollover=self.rollover,
            utimeout=self.utimeout,
            extra=self.extra_options,
        )

    def _download(
        self, filename: str, sink: BinaryIO, mode: str, progress: Optional[Progress]
    ) -> TransferResult:
        writer: Any = NetasciiWriter(sink) if mode == "netascii" else sink
        write = as_write(writer)
        result = self._run(Opcode.RRQ, filename, mode, None, write, None, progress)
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
        return self._run(Opcode.WRQ, filename, mode, size, None, read, progress)

    def _run(self, opcode, filename, mode, size, write, read, progress) -> TransferResult:
        family, server, address = self._endpoint()
        options = self._options(opcode == Opcode.RRQ, size, address)
        with self._socket(family) as sock:
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

    def _first_response(self, view, n: int, options, is_read: bool, send) -> Tuple[Negotiated, bool]:
        """Interpret the server's answer to the request: ``(negotiated, first_data)``.

        ``first_data`` is true when the answer is DATA 1 itself (an RRQ whose
        options were ignored), to be handed to the receiver. Raises for an
        ERROR (marked as a refusal of the request, for the option fallback),
        an OACK this client cannot accept (after sending ERROR 8), or any
        other opcode (after sending ERROR 4).
        """
        op = view[1] if n >= 2 and view[0] == 0 else -1
        if op == Opcode.ERROR:
            packet = decode(view[:n])
            refused = RemoteError.from_code(packet.code, packet.message)  # type: ignore[union-attr]
            refused._in_request = True  # type: ignore[attr-defined]
            raise refused
        if op == Opcode.OACK:
            oack = decode(view[:n]).options  # type: ignore[union-attr]
            try:
                negotiated = accept_oack(
                    options, oack, is_read=is_read, timeout=self.timeout, registry=self.registry
                )
            except ProtocolError as exc:
                send(encode_error(exc.code, exc.message))
                raise
            return negotiated, False
        if (is_read and op == Opcode.DATA) or (
            not is_read and op == Opcode.ACK and view[2] == 0 and view[3] == 0
        ):
            return Negotiated(timeout=self.timeout), is_read
        send(encode_error(ErrorCode.ILLEGAL_OPERATION, "unexpected opcode %d" % op))
        raise ProtocolError("unexpected opcode %d in response to the request" % op)

    def _emitter(self, sock) -> Optional[Callable[[Any, str, Tuple[Any, ...]], None]]:
        """``emit(data, direction, remote)`` reporting to ``trace``, or ``None``."""
        trace = self.trace
        if trace is None:
            return None
        session_id = new_session_id("c")
        local = sock.getsockname()

        def emit(data, direction: str, remote) -> None:
            try:
                trace(PacketEvent(time.time(), direction, local, remote, bytes(data), "client", session_id))
            except Exception:
                log.exception("trace hook failed")

        return emit

    def _request(self, sock, server, request: bytes, buf, view, expires, emit) -> Tuple[int, Tuple[Any, ...]]:
        """Send ``request`` (retrying with backoff) until the server answers: ``(n, peer)``."""
        clock = time.monotonic
        sock.sendto(request, server)
        if emit is not None:
            emit(request, "out", server)
        tries = self.retries
        wait = self.timeout
        deadline = clock() + wait
        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                tries -= 1
                if tries < 0 or (expires is not None and clock() >= expires):
                    raise TransferTimeout("no response from %s:%s" % server[:2])
                sock.sendto(request, server)
                if emit is not None:
                    emit(request, "out", server)
                wait = min(wait * self.backoff, self.max_timeout)  # RFC 1123 4.2.3.2
                deadline = clock() + wait
                continue
            sock.settimeout(remaining)
            try:
                n, peer = sock.recvfrom_into(buf)
            except socket.timeout:
                continue
            except ConnectionResetError:  # pragma: no cover - connreset is off
                continue
            if emit is not None:
                emit(view[:n], "in", peer)
            if n < 2 or (self.strict_source and peer[0] != server[0]):
                continue  # not the server we asked
            return n, peer

    def _endpoint(self) -> Tuple[int, Tuple[Any, ...], Any]:
        """``(family, server sockaddr, server address)`` for this client's host."""
        from netimps import get_ip, normalize_host

        host, port = normalize_host(self.host, self.port)
        address = get_ip(host, ipv6={socket.AF_INET6: True, socket.AF_INET: False}.get(self.family))
        if address is None:
            raise socket.gaierror("cannot resolve %r" % host)
        family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        return family, (str(address), port), address

    def _socket(self, family: int) -> socket.socket:
        from netimps import bind

        local_host, local_port = self.local_address or (("::" if family == socket.AF_INET6 else "0.0.0.0"), 0)
        # connreset=False: Windows would otherwise report an ICMP
        # port-unreachable as ConnectionResetError on our next receive.
        return bind(local_host, local_port, family=family, connreset=False)

    def size(self, filename: str, *, mode: str = "octet") -> Optional[int]:
        """The size of ``filename`` on the server, without transferring it.

        Sends an RRQ asking only for ``tsize`` and abandons the transfer as
        soon as the server answers (ERROR 8 to an OACK, as RFC 2347 lets a
        client refuse one). Returns ``None`` when the server does not report
        sizes -- unless the whole file fits in the first block, whose length
        then is the size. Raises :class:`RemoteError` (``FileNotFound``...)
        like a download would.
        """
        mode = _mode(mode)
        family, server, _ = self._endpoint()
        with self._socket(family) as sock:
            request = encode_request(Opcode.RRQ, filename, mode, {"tsize": "0"})
            buf = bytearray(4 + 65536)
            view = memoryview(buf)
            emit = self._emitter(sock)
            n, peer = self._request(sock, server, request, buf, view, None, emit)

            def send(packet) -> None:
                sock.sendto(packet, peer)
                if emit is not None:
                    emit(packet, "out", peer)

            op = view[1] if view[0] == 0 else -1
            if op == Opcode.ERROR:
                packet = decode(view[:n])
                raise RemoteError.from_code(packet.code, packet.message)  # type: ignore[union-attr]
            if op == Opcode.OACK:
                send(encode_error(ErrorCode.OPTION_REFUSED, "size probe only"))
                text = decode(view[:n]).options.get("tsize", "")  # type: ignore[union-attr]
                return int(text) if text.strip().isdigit() else None
            if op == Opcode.DATA and n >= 4:
                size = n - 4
                if size < DEFAULT_BLKSIZE:
                    send(encode_ack(1))  # the whole file: finish politely
                    return size
                send(encode_error(ErrorCode.NOT_DEFINED, "size probe only"))
                return None
            send(encode_error(ErrorCode.ILLEGAL_OPERATION, "unexpected opcode %d" % op))
            raise ProtocolError("unexpected opcode %d in response to the request" % op)

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

        expires = None if self.max_duration is None else started + self.max_duration
        engine = {"backoff": self.backoff, "max_timeout": self.max_timeout, "expires": expires}

        emit = self._emitter(sock)
        trace = emit
        n, peer = self._request(sock, server, request, buf, view, expires, emit)

        if trace is None:

            def send(packet, _sendto=sock.sendto, _peer=peer):
                return _sendto(packet, _peer)

        else:

            def send(packet, _sendto=sock.sendto, _peer=peer):
                result = _sendto(packet, _peer)
                emit(packet, "out", _peer)  # type: ignore[misc]
                return result

        session: Transfer
        negotiated, first_data = self._first_response(view, n, options, is_read, send)

        # Before the engine exists: building a Sender already reads the first
        # block, and a caller may need to know the outcome before that.
        if self.on_negotiated is not None:
            self.on_negotiated(negotiated, peer)
        now = clock()
        if is_read:
            reply = None if first_data else encode_ack(0)
            session = Receiver(send, write, negotiated, self.retries, now, reply=reply, **engine)
            if first_data:
                session.handle(view, n, now)
        else:
            session = Sender(send, read, negotiated, self.retries, now, **engine)

        if len(buf) < 4 + negotiated.blksize + 1:
            buf = bytearray(4 + negotiated.blksize + 1)
            view = memoryview(buf)
        total = negotiated.tsize
        reported = -1
        peer_host, peer_port = peer[0], peer[1]

        # Transfer phase.
        while not session.done:
            if session.stalled:
                # A non-blocking local source/sink had nothing ready: poll it.
                session.resume(clock())
                if session.stalled:
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
                if not session.stalled:
                    session.on_timeout(clock())
                continue
            except ConnectionResetError:  # pragma: no cover - connreset is off
                continue
            if trace is not None:
                emit(view[:n], "in", addr)
            if addr[1] != peer_port or addr[0] != peer_host:
                try:
                    stray = encode_error(ErrorCode.UNKNOWN_TID)
                    sock.sendto(stray, addr)
                    if trace is not None:
                        emit(stray, "out", addr)
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
                if trace is not None:
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
