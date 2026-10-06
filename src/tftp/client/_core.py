"""What both clients share: configuration, request options, the first answer.

:class:`tftp.TFTPClient` and :class:`tftp.AsyncTFTPClient` are siblings over
the base here. It holds no transfer method: a transfer waits on a socket in
two different ways. The probes that learn a size or a listing flag use a
blocking socket; the asynchronous client runs them in the executor.
"""

from __future__ import annotations

import copy
import io
import logging
import os
import socket
import time
from typing import (
    TYPE_CHECKING,
    Any,
    BinaryIO,
    Callable,
    Dict,
    Mapping,
    NamedTuple,
    Optional,
    Tuple,
    TypeVar,
    Union,
)

from .._arguments import check_family, check_int, check_seconds, check_source
from .._sockets import same_host, sockaddr
from ..capture.events import PacketEvent, new_session_id
from ..exceptions import (
    RemoteError,
    TFTPDecodeError,
    TFTPError,
    TFTPProtocolError,
    TransferTimeoutError,
)
from ..listing import LIST_OPTION, MTIME_OPTION
from ..options import (
    DEFAULT_BLKSIZE,
    MAX_BLKSIZE,
    MIN_BLKSIZE,
    Negotiated,
    OptionRegistry,
    accept_oack,
    request_options,
)
from ..options.base import read_decimal
from ..packet import TFTPErrorCode, TFTPOpcode, decode, encode_ack, encode_request
from ..packet.codec import _encode_error

if TYPE_CHECKING:  # netimps is imported lazily at run time
    from netimps import HostLike

__all__ = ["MODES", "RemoteStat"]

#: Receive buffer: longer than any UDP datagram, so a stray one is read whole.
_RECV_BUFFER = 65536

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


class RemoteStat(NamedTuple):
    """What :meth:`TFTPClient.stat` learnt about a name on the server.

    ``size`` is ``None`` when the server reports none (and for a directory);
    ``mtime`` (seconds since the epoch) needs a server speaking ``x-mtime``;
    ``is_dir`` needs one speaking ``x-list`` -- other servers report a
    directory as not found.
    """

    size: Optional[int]
    mtime: Optional[int] = None
    is_dir: bool = False


class _NotListing(TFTPProtocolError):
    """A listing was asked for and a file is arriving instead."""


def _digits(text: Optional[str]) -> Optional[int]:
    return None if text is None else read_decimal(text)


#: What a server answers a request that carries options with when it cannot
#: read them: RFC 2347's ERROR 8, and the "illegal operation" and "not
#: defined" that a server older than the extension sends for the extra data.
_OPTION_ERRORS = frozenset(
    {TFTPErrorCode.OPTION_REFUSED, TFTPErrorCode.ILLEGAL_OPERATION, TFTPErrorCode.NOT_DEFINED}
)


def _is_first_packet(view, n: int, is_read: bool) -> bool:
    """The packet an option-less server starts with: DATA 1 for a read, ACK 0 for a write."""
    if n < 4 or view[0] != 0:
        return False
    want_op, want_block = (TFTPOpcode.DATA, 1) if is_read else (TFTPOpcode.ACK, 0)
    return view[1] == want_op and view[2] == 0 and view[3] == want_block


def _repeats_without_options(exc: RemoteError) -> bool:
    """The request itself was answered with an ERROR that may be about its options."""
    return exc.code in _OPTION_ERRORS and getattr(exc, "_in_request", False)


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
    from netimps import get_interface, get_source_ip, max_udp_payload

    try:
        source = get_source_ip(str(server), ipv6=server.version == 6)
        # cache=True: netimps reuses an adapter listing up to 1 s old, so a run of
        # transfers lists adapters at most once a second.
        iface = get_interface(source, cache=True) if source is not None else None
    except (OSError, ValueError):
        iface = None
    if iface is None or not iface.mtu:
        return 1428
    fits = max_udp_payload(iface.mtu, ipv6=server.version == 6) - 4  # the DATA header
    return max(MIN_BLKSIZE, min(fits, MAX_BLKSIZE))


_Client = TypeVar("_Client", bound="_ClientBase")


class _ClientBase:
    """The arguments of a client and what is decided from them, with no transfer."""

    def __init__(
        self,
        host: "HostLike",
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
        src: Optional[Tuple[Any, ...]] = None,
        fallback: bool = True,
        dally: bool = False,
        backoff: float = 2.0,
        max_timeout: Optional[float] = None,
        deadline: Optional[float] = None,
        strict_source: bool = True,
        utimeout: bool = False,
        extra_options: Optional[Mapping[str, object]] = None,
        registry: Optional[OptionRegistry] = None,
        on_negotiated: Optional[Callable[[Negotiated, Tuple[Any, ...]], Any]] = None,
        trace: Optional[Callable[[PacketEvent], Any]] = None,
    ) -> None:
        check_int("port", port, 1, 65535)
        check_int("retries", retries, 0)
        check_seconds("timeout", timeout)
        if max_timeout is not None:
            check_seconds("max_timeout", max_timeout)
            if max_timeout < timeout:
                raise ValueError("max_timeout must be at least timeout (%s), got %s" % (timeout, max_timeout))
        if deadline is not None:
            check_seconds("deadline", deadline, finite=False)
        check_seconds("backoff", backoff, minimum=1.0)
        check_family(family)
        src = check_source(src)
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
        self.src = src
        self.fallback = fallback
        self.dally = dally
        self.backoff = backoff
        self.max_timeout = max_timeout if max_timeout is not None else timeout * 8
        self.deadline = deadline
        self.strict_source = strict_source
        self.utimeout = utimeout
        self.extra_options = dict(extra_options or {})
        self.registry = registry
        self.on_negotiated = on_negotiated
        self.trace = trace

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

    def _first_response(self, view, n: int, options, is_read: bool, send) -> Tuple[Negotiated, bool]:
        """Interpret the server's answer to the request: ``(negotiated, first_data)``.

        ``first_data`` is true when the answer is DATA 1 itself (an RRQ whose
        options were ignored), to be handed to the receiver. Raises for an
        ERROR (marked as a refusal of the request, for the option fallback),
        an OACK this client cannot accept (after sending ERROR 8), or any
        other answer (after sending ERROR 4): a WRQ is answered by ACK 0 and
        an RRQ by DATA 1, each whole, and a packet that does not decode is no
        answer.
        """
        op = view[1] if n >= 2 and view[0] == 0 else -1
        try:
            if op == TFTPOpcode.ERROR:
                packet = decode(view[:n])
                refused = RemoteError.from_code(packet.code, packet.message)  # type: ignore[union-attr]
                refused._in_request = True  # type: ignore[attr-defined]
                raise refused
            if op == TFTPOpcode.OACK:
                oack = decode(view[:n]).options  # type: ignore[union-attr]
                try:
                    negotiated = accept_oack(
                        options, oack, is_read=is_read, timeout=self.timeout, registry=self.registry
                    )
                except TFTPProtocolError as exc:
                    send(_encode_error(exc.code, exc.message))
                    raise
                return negotiated, False
        except TFTPDecodeError as exc:
            if op != TFTPOpcode.ERROR:
                send(_encode_error(TFTPErrorCode.ILLEGAL_OPERATION, "malformed answer"))
            raise TFTPProtocolError("malformed answer to the request: %s" % exc) from exc
        if _is_first_packet(view, n, is_read):
            return Negotiated(timeout=self.timeout), is_read
        send(_encode_error(TFTPErrorCode.ILLEGAL_OPERATION, "unexpected opcode %d" % op))
        raise TFTPProtocolError("unexpected opcode %d in response to the request" % op)

    def _target(self) -> Tuple[Any, int]:
        """``(host, port)``: a port written in the host (``"h:70"``) overrides ``port``."""
        from netimps import split_host

        host, port = split_host(self.host, default_port=self.port)
        return host, port if port is not None else self.port

    def _endpoint(self) -> Tuple[int, Tuple[Any, ...], Any]:
        """``(family, server sockaddr, server address)`` for this client's host.

        A name that does not resolve raises netimps' ``ResolutionError`` (an
        ``OSError``). It may block on DNS: the asynchronous client calls it in
        the executor.
        """
        from netimps import Host

        host, port = self._target()
        address = Host(host).ip(
            check=True, ipv6={socket.AF_INET6: True, socket.AF_INET: False}.get(self.family)
        )
        family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        return family, sockaddr(address, port), address

    def _lister(self: _Client) -> _Client:
        """A copy of this client whose download is a listing or fails with _NotListing."""
        lister = copy.copy(self)
        lister.extra_options = dict(self.extra_options, **{LIST_OPTION: "1"})
        lister.fallback = False
        outer = self.on_negotiated

        def check(negotiated: Negotiated, peer: Tuple[Any, ...]) -> None:
            if not negotiated.extra.get(LIST_OPTION):
                raise _NotListing("not a directory", TFTPErrorCode.OPTION_REFUSED)
            if outer is not None:
                outer(negotiated, peer)

        lister.on_negotiated = check
        return lister

    def _negotiated(
        self, negotiated: Negotiated, peer: Tuple[Any, ...], send: Callable[[bytes], Any]
    ) -> None:
        """Run ``on_negotiated``; if it raises, tell the server before re-raising."""
        if self.on_negotiated is None:
            return
        try:
            self.on_negotiated(negotiated, peer)
        except BaseException as exc:
            if isinstance(exc, TFTPError):
                packet = _encode_error(exc.code, exc.message)
            else:
                packet = _encode_error(TFTPErrorCode.NOT_DEFINED, "transfer cancelled")
            try:
                send(packet)
            except OSError:
                pass
            raise

    # -- blocking request phase, for the probes and the synchronous transfer ----------

    def _socket(self, family: int) -> socket.socket:
        from netimps import bind

        local_host, local_port = self.src or (("::" if family == socket.AF_INET6 else "0.0.0.0"), 0)
        return bind(local_host, local_port, family=family)

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
        from netimps import Backoff

        timer = Backoff(self.timeout, multiplier=self.backoff, max_delay=self.max_timeout)  # RFC 1123 4.2.3.2
        deadline = clock() + timer.delay
        while True:
            now = clock()
            if expires is not None and now >= expires:
                raise TransferTimeoutError("transfer exceeded its time limit")
            remaining = deadline - now
            if remaining <= 0:
                if timer.attempt >= self.retries:
                    raise TransferTimeoutError("no response from %s:%s" % server[:2])
                sock.sendto(request, server)
                if emit is not None:
                    emit(request, "out", server)
                deadline = clock() + timer.advance()
                continue
            sock.settimeout(remaining if expires is None else min(remaining, expires - now))
            try:
                n, peer = sock.recvfrom_into(buf)
            except socket.timeout:
                continue
            except ConnectionResetError:  # pragma: no cover - connreset is off
                continue
            if emit is not None:
                emit(view[:n], "in", peer)
            if n < 2 or (self.strict_source and not same_host(peer, server)):
                continue  # not the server we asked
            return n, peer

    def _expires(self, started: float) -> Optional[float]:
        """When a transfer begun at ``started`` (``time.monotonic``) must be over, or ``None``."""
        return None if self.deadline is None else started + self.deadline

    def _size(self, filename: str, mode: str, expires: Optional[float] = None) -> Optional[int]:
        oack, small = self._probe(filename, _mode(mode), {"tsize": "0"}, expires)
        return small if oack is None else _digits(oack.get("tsize"))

    def _stat(self, filename: str, mode: str, expires: Optional[float] = None) -> RemoteStat:
        mode = _mode(mode)
        filename = filename or "."  # the root: a request needs a name
        asked = {"tsize": "0", MTIME_OPTION: "0", LIST_OPTION: "1"}
        try:
            oack, small = self._probe(filename, mode, asked, expires)
        except RemoteError as exc:
            if exc.code not in _OPTION_ERRORS or not self.fallback:
                raise
            return RemoteStat(self._size(filename, mode, expires))
        if oack is None:
            return RemoteStat(small)
        is_dir = oack.get(LIST_OPTION, "").strip() == "1"
        size = None if is_dir else _digits(oack.get("tsize"))
        return RemoteStat(size, _digits(oack.get(MTIME_OPTION)), is_dir)

    def _probe(
        self, filename: str, mode: str, options: Mapping[str, str], expires: Optional[float] = None
    ) -> Tuple[Optional[Dict[str, str]], Optional[int]]:
        """Send an RRQ and abandon it at the first answer, before ``expires`` (``time.monotonic``).

        ``(oack, None)`` when the server answered with an OACK (refused with
        ERROR 8 at once); ``(None, size)`` when it ignored the options and
        sent DATA 1 -- ``size`` is that block's length when the whole file
        fits in it, else ``None``.
        """
        family, server, _ = self._endpoint()
        with self._socket(family) as sock:
            request = encode_request(TFTPOpcode.RRQ, filename, mode=mode, options=options)
            buf = bytearray(_RECV_BUFFER)
            view = memoryview(buf)
            emit = self._emitter(sock)
            n, peer = self._request(sock, server, request, buf, view, expires, emit)

            def send(packet) -> None:
                sock.sendto(packet, peer)
                if emit is not None:
                    emit(packet, "out", peer)

            op = view[1] if n >= 2 and view[0] == 0 else -1
            try:
                if op == TFTPOpcode.ERROR:
                    packet = decode(view[:n])
                    raise RemoteError.from_code(packet.code, packet.message)  # type: ignore[union-attr]
                if op == TFTPOpcode.OACK:
                    oack = dict(decode(view[:n]).options)  # type: ignore[union-attr]
                    send(_encode_error(TFTPErrorCode.OPTION_REFUSED, "size probe only"))
                    return oack, None
            except TFTPDecodeError as exc:
                raise TFTPProtocolError("malformed answer to the request: %s" % exc) from exc
            if _is_first_packet(view, n, True):
                size = n - 4
                if size < DEFAULT_BLKSIZE:
                    send(encode_ack(1))  # the whole file: finish politely
                    return None, size
                send(_encode_error(TFTPErrorCode.NOT_DEFINED, "size probe only"))
                return None, None
            send(_encode_error(TFTPErrorCode.ILLEGAL_OPERATION, "unexpected opcode %d" % op))
            raise TFTPProtocolError("unexpected opcode %d in response to the request" % op)
