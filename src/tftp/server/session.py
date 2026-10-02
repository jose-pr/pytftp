"""One server-side transfer: its socket, its stream, and how it starts.

Every transfer gets its own UDP socket -- its transfer ID (RFC 1350
section 4) -- bound to the address the request was sent to when that is
known, so a multi-homed or virtual-IP host answers from the address the
client used.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
from typing import Any, Optional, Tuple

from .._sockets import fit_window, udp_socket, unmap
from ..errors import TftpError
from ..netascii import NetasciiReader, NetasciiWriter, encoded_size
from ..options import ServerOptions, negotiate
from ..packet import ErrorCode, Request, encode_ack, encode_oack
from ..transfer import Receiver, Sender, Transfer, as_readinto, as_write
from .handler import RequestContext

__all__ = ["Session", "reply_socket", "stream_size"]

log = logging.getLogger("tftp.server")


def stream_size(stream: Any) -> Optional[int]:
    """Bytes left in ``stream``: its ``size`` attribute, ``fstat``, or seeking."""
    size = getattr(stream, "size", None)
    if isinstance(size, int) and not isinstance(size, bool):
        return size
    try:
        return os.fstat(stream.fileno()).st_size
    except (AttributeError, OSError, ValueError):
        pass
    try:
        here = stream.tell()
        end = stream.seek(0, os.SEEK_END)
        stream.seek(here)
        return end - here
    except (AttributeError, OSError, ValueError):
        return None


def reply_socket(
    family: int,
    listen_host: Optional[str],
    sender: Tuple[Any, ...],
    local: Optional[str],
    ifindex: int,
) -> Tuple[socket.socket, Tuple[Any, ...]]:
    """A non-blocking socket for one transfer, and the peer to send to.

    A v4 client seen through a dual-stack listener (``::ffff:a.b.c.d``) gets
    a plain ``AF_INET`` socket, so the reply needs no dual-stack support of
    its own. The bind address is tried in order: the request's destination
    (``local``), the listening address, the wildcard -- a destination that
    cannot be bound (a subnet broadcast) falls through to the next.
    """
    host = sender[0]
    peer_v4 = unmap(host)
    if peer_v4 != host:
        family, peer = socket.AF_INET, (peer_v4, sender[1])
        if local is not None:
            local = unmap(local)
        if listen_host is not None:
            listen_host = unmap(listen_host) if "." in listen_host else None
    else:
        peer = sender

    candidates = []
    if local is not None:
        bare = local.split("%", 1)[0]
        try:
            address = ipaddress.ip_address(bare)
        except ValueError:
            address = None
        if address is not None and not (
            address.is_multicast or address.is_unspecified or bare == "255.255.255.255"
        ):
            if family == socket.AF_INET6:
                candidates.append((bare, 0, 0, ifindex if address.is_link_local else 0))
            elif address.version == 4:
                candidates.append((bare, 0))
    if listen_host is not None:
        candidates.append((listen_host, 0))
    candidates.append(("::", 0) if family == socket.AF_INET6 else ("0.0.0.0", 0))
    last: Optional[OSError] = None
    for bind in candidates:
        try:
            sock = udp_socket(family, bind)
        except OSError as exc:
            last = exc
            continue
        sock.setblocking(False)
        return sock, peer
    raise last  # type: ignore[misc]


class Session:
    """A transfer in progress on the server, and the state the loop keeps for it."""

    __slots__ = (
        "sock",
        "peer",
        "host",
        "port",
        "transfer",
        "context",
        "stream",
        "started",
        "timer_at",
        "linger_until",
        "closed",
        "key",
        "local",
    )

    def __init__(
        self, sock: socket.socket, peer: Tuple[Any, ...], context: RequestContext, started: float
    ) -> None:
        self.sock = sock
        self.peer = peer
        self.host = peer[0]
        self.port = peer[1]
        self.context = context
        self.started = started
        self.transfer: Optional[Transfer] = None
        self.stream: Any = None
        self.timer_at: Optional[float] = None
        self.linger_until: Optional[float] = None
        self.closed = False
        self.key: Tuple[str, int] = (unmap(peer[0]), peer[1])
        self.local: Tuple[Any, ...] = sock.getsockname()

    def wakeup(self) -> Optional[float]:
        """When the loop must next look at this session, or ``None``."""
        if self.linger_until is not None:
            return self.linger_until
        return self.transfer.deadline if self.transfer is not None else None

    def send(self, packet) -> None:
        try:
            self.sock.sendto(packet, self.peer)
        except OSError:
            # A full send buffer or a transient route error is loss, which
            # the transfer's timeout already recovers from.
            pass

    def open(self, handler: Any, policy: ServerOptions, timeout: float, retries: int, now: float) -> Transfer:
        """Ask ``handler`` for the stream, negotiate, and build the transfer.

        Raises whatever the handler raises, or :class:`TftpError` for an
        unsupported mode; the caller turns either into an ERROR packet.
        """
        request: Request = self.context.request
        mode = request.mode
        if mode == "mail":
            raise TftpError(ErrorCode.ILLEGAL_OPERATION, "mail mode is not supported")
        if mode not in ("octet", "netascii"):
            raise TftpError(ErrorCode.ILLEGAL_OPERATION, "unknown mode %r" % mode)
        wants_size = "tsize" in request.options and "tsize" in policy.allowed

        if request.is_read:
            stream = handler.open_read(self.context)
            self.stream = stream
            if mode == "netascii":
                size = encoded_size(stream) if wants_size else None
                reader: Any = NetasciiReader(stream)
            else:
                size = stream_size(stream) if wants_size else None
                reader = stream
            negotiated = negotiate(request.options, policy, is_read=True, timeout=timeout, size=size)
            fit_window(self.sock, negotiated.blksize, negotiated.windowsize)
            oack = encode_oack(negotiated.options) if negotiated.options else None
            log.debug("%r: %r", self.context, negotiated)
            return Sender(self.send, as_readinto(reader), negotiated, retries, now, oack=oack)

        negotiated = negotiate(request.options, policy, is_read=False, timeout=timeout)
        fit_window(self.sock, negotiated.blksize, negotiated.windowsize)
        stream = handler.open_write(self.context, negotiated.tsize)
        writer: Any = NetasciiWriter(stream) if mode == "netascii" else stream
        self.stream = writer
        reply = encode_oack(negotiated.options) if negotiated.options else encode_ack(0)
        log.debug("%r: %r", self.context, negotiated)
        return Receiver(
            self.send, as_write(writer), negotiated, retries, now, reply=reply, complete=self.commit
        )

    def commit(self) -> None:
        """Close the upload stream before the final ACK, so a failure is reported."""
        stream, self.stream = self.stream, None
        stream.close()

    def close_stream(self, ok: bool) -> None:
        """Close the stream; a failed upload is aborted when the stream allows."""
        stream, self.stream = self.stream, None
        if stream is None:
            return
        try:
            if not ok and hasattr(stream, "abort"):
                stream.abort()
            else:
                stream.close()
        except Exception:
            log.exception("closing the stream for %r failed", self.context)
