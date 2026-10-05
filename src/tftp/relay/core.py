"""The transparent relay: forwards TFTP between clients and upstream servers.

There is no standard TFTP relay (nothing like DHCP's); this is an
application-level one in the RTEMS-proxy style:

    client:C --RRQ--> relay:69                (request, forwarded byte for byte)
    relay:U  --RRQ--> upstream:69             (from a fresh upstream-side port)
    upstream:S <-> relay:U                    (the upstream's TID, learned)
    relay:D  <-> client:C                     (a fresh client-side TID)

Datagrams cross unchanged, so options and extensions the library does not
understand still work end to end. To give each side its own block size or
window instead, terminate both sessions with
:class:`tftp.backends.UpstreamHandler`.
"""

from __future__ import annotations

import logging
import selectors
import socket
import sys
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Tuple

if TYPE_CHECKING:
    from netimps import Host, IPAddressLike

from .._sockets import same_host, sockaddr
from ..capture.events import PacketEvent, new_session_id
from ..errors import TftpError
from ..packet import ErrorCode, MalformedPacket, Opcode, Request, decode, encode_error
from ..server.handler import RequestContext
from ..server.listener import Arrival, Listener
from ..server.policy import ServerLimits
from ..server.session import PortRange, bind_transfer
from .routing import Route, Upstream, upstream as to_upstream
from ..server.stats import RELAY_COUNTERS, Stats
from .session import RelaySession, RelaySummary

__all__ = ["Relay"]

log = logging.getLogger("tftp.relay")

_TICK = 0.25  # how often idle/lifetime/linger deadlines are checked
_RECV = 65536
_DRAIN = 64
_RESOLVE_TTL = 60.0


class Relay:
    """Forward TFTP requests to upstream servers, transparently.

    :param route: an upstream (``"host"``, ``"host:port"``, ``(host, port)``,
        :class:`Upstream`) for every request, or a route callable
        ``route(request, context) -> upstream | None`` (see
        :mod:`tftp.relay.routing`). ``None`` refuses with ERROR 2.
    :param host, port: where to listen for requests (as for ``Server``).
    :param idle_timeout: end a transfer after this long without traffic.
        Keep it above the largest timeout a peer may negotiate times its
        retries.
    :param max_lifetime: end any transfer after this long.
    :param linger: after a recognised final DATA/ACK exchange (or an ERROR,
        up to 1 s), keep forwarding this long for retransmissions.
    :param upstream_source: local address to send upstream from (a string,
        an ``ipaddress`` address or a ``netimps.Host``).
    :param limits, max_sessions, ignore_broadcast,
        reply_from_request_address: as for ``Server``.
    :param trace: ``trace(PacketEvent)`` for every datagram received or
        sent, with ``role="relay"`` and ``leg`` ``"client"``/``"upstream"``.
    :param on_session_end: ``on_session_end(RelaySummary)`` when a relayed
        transfer ends.
    :param port_range: ports for both sockets of each relayed transfer (the
        client side and the upstream side), as for ``Server``.
    :param interface: listen on one network adapter, as for ``Server``.
    """

    def __init__(
        self,
        route: Any,
        host: "IPAddressLike | Host | None" = None,
        port: int = 69,
        *,
        idle_timeout: float = 30.0,
        max_lifetime: float = 3600.0,
        linger: float = 2.0,
        upstream_source: "IPAddressLike | Host | None" = None,
        limits: Optional[ServerLimits] = None,
        max_sessions: Optional[int] = None,
        ignore_broadcast: bool = True,
        reply_from_request_address: bool = True,
        trace: Optional[Callable[[PacketEvent], Any]] = None,
        on_session_end: Optional[Callable[[RelaySummary], Any]] = None,
        port_range: Any = None,
        interface: Any = None,
    ) -> None:
        if callable(route):
            self.route: Route = route
        else:
            fixed = to_upstream(route)
            self.route = lambda request, context: fixed
        self.idle_timeout = idle_timeout
        self.max_lifetime = max_lifetime
        self.linger = linger
        self.upstream_source = upstream_source
        self.limits = limits or ServerLimits()
        if sys.platform == "win32":
            if max_sessions is None:
                max_sessions = 250  # two sockets each; select() handles 512
            elif max_sessions > 255:
                raise ValueError(
                    "max_sessions=%d is more than the 255 transfers select() can watch on Windows "
                    "(two sockets each)" % max_sessions
                )
        self.max_sessions = max_sessions
        self.ignore_broadcast = ignore_broadcast
        self.trace = trace
        self.on_session_end = on_session_end
        #: Ports for both of a transfer's sockets (:class:`PortRange`), or ``None``.
        self.port_range = PortRange.of(port_range)
        #: Counters since start (:class:`Stats`).
        self.stats = Stats(*RELAY_COUNTERS)
        self._listener = Listener(host, port, pktinfo=reply_from_request_address, interface=interface)
        self._address = self._listener.sock.getsockname()
        self._selector = selectors.DefaultSelector()
        self._wake_r, self._wake_w = socket.socketpair()
        self._wake_r.setblocking(False)
        self._wake_w.setblocking(False)
        self._selector.register(self._listener.sock, selectors.EVENT_READ, None)
        self._selector.register(self._wake_r, selectors.EVENT_READ, self)
        self._sessions: Dict[Tuple[str, int], RelaySession] = {}
        self._resolved: Dict[str, Tuple[Any, float]] = {}
        self._buf = bytearray(_RECV)
        self._stopping = False
        self._running = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._closed = False

    # -- properties and lifecycle (as for Server) --------------------------------

    @property
    def server_address(self) -> Tuple[Any, ...]:
        return self._address

    @property
    def supports_pktinfo(self) -> bool:
        return self._listener.supports_pktinfo

    @property
    def active_sessions(self) -> int:
        return len(self._sessions)

    def stats_snapshot(self) -> Dict[str, int]:
        """Every counter plus ``active``, for metrics."""
        snapshot = self.stats.snapshot()
        snapshot["active"] = len(self._sessions)
        return snapshot

    def serve_forever(self) -> None:
        if self._closed:
            raise RuntimeError("relay is closed")
        self._stopping = False
        self._running.set()
        clock = time.monotonic
        next_tick = clock() + _TICK
        try:
            while not self._stopping:
                for key, _ in self._selector.select(max(0.0, next_tick - clock())):
                    data = key.data
                    try:
                        if data is None:
                            self._on_request(clock())
                        elif data is self:
                            try:
                                while self._wake_r.recv(64):
                                    pass
                            except OSError:
                                pass
                        else:
                            session, leg = data
                            self._on_datagram(session, leg, clock())
                    except Exception:
                        log.exception("unexpected failure in the event loop (ending that transfer)")
                        if isinstance(data, tuple):
                            self._end(data[0], "error", clock())
                now = clock()
                if now >= next_tick:
                    try:
                        self._sweep(now)
                    except Exception:
                        log.exception("unexpected failure sweeping idle transfers")
                    next_tick = now + _TICK
        finally:
            self._running.clear()
            now = clock()
            for session in list(self._sessions.values()):
                for sock, peer in ((session.down, session.client), (session.up, session.upstream_tid)):
                    if peer is not None:
                        self._send(session, sock, encode_error(0, "relay shutting down"), peer, "out")
                self._end(session, "shutdown", now)

    def shutdown(self) -> None:
        self._stopping = True
        try:
            self._wake_w.send(bytes(1))
        except OSError:
            pass

    def start(self) -> "Relay":
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("relay already running")
        self._thread = threading.Thread(target=self.serve_forever, name="tftp-relay", daemon=True)
        self._thread.start()
        self._running.wait(5)
        return self

    def stop(self, timeout: Optional[float] = 5.0) -> None:
        self.shutdown()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def close(self) -> None:
        if self._closed:
            return
        self.stop()
        self._closed = True
        self._selector.close()
        self._listener.close()
        self._wake_r.close()
        self._wake_w.close()

    def __enter__(self) -> "Relay":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- forwarding ---------------------------------------------------------------

    def _emit(self, session: Optional[RelaySession], sock: socket.socket, data, peer, direction, leg) -> None:
        trace = self.trace
        if trace is None:
            return
        try:
            local = sock.getsockname()
        except OSError:
            local = ()
        try:
            trace(
                PacketEvent(
                    time.time(), direction, local, peer, bytes(data), "relay", session and session.id, leg
                )
            )
        except Exception:
            log.exception("trace hook failed")

    def _send(self, session: RelaySession, sock: socket.socket, data, peer, direction: str = "out") -> None:
        try:
            sock.sendto(data, peer)
        except OSError:
            return  # loss; the peers' own retransmissions recover
        self._emit(session, sock, data, peer, direction, "client" if sock is session.down else "upstream")

    def _resolve(self, target: Upstream) -> Tuple[int, Tuple[Any, ...]]:
        cached = self._resolved.get(target.host)
        now = time.monotonic()
        if cached is not None and cached[1] > now:
            address = cached[0]
        else:
            from netimps import Host

            address = Host(target.host).ip()
            if address is None:
                raise TftpError(ErrorCode.NOT_DEFINED, "upstream unresolvable")
            self._resolved[target.host] = (address, now + _RESOLVE_TTL)
        family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        return family, sockaddr(address, target.port)

    def _on_request(self, now: float) -> None:
        from netimps import unmap

        for _ in range(_DRAIN):
            arrival = self._listener.recv()
            if arrival is None:
                return
            data, sender = arrival.data, arrival.sender
            if len(data) < 2 or data[0] != 0 or data[1] not in (Opcode.RRQ, Opcode.WRQ):
                continue
            if self.ignore_broadcast and self._listener.is_broadcast(arrival):
                continue
            key = (str(unmap(sender[0])), sender[1])
            existing = self._sessions.get(key)
            if existing is not None:
                if existing.upstream_tid is None:  # still unanswered: pass the retry on
                    self._emit(existing, self._listener.sock, data, sender, "in", "client")
                    self._send(existing, existing.up, data, existing.upstream)
                continue
            try:
                self._open(arrival, key, now)
            except Exception:  # pragma: no cover - a bug, not a client error
                log.exception("relaying a request from %s failed", sender[:2])
                self._discard(key)

    def _open(self, arrival: Arrival, key, now: float) -> None:
        self.stats.add("requests")
        if not self._open_session(arrival, key, now):
            self.stats.add("refused")
        else:
            self.stats.add("started")

    def _open_session(self, arrival: Arrival, key, now: float) -> bool:
        data, sender, local, ifindex = arrival[:4]
        if len(data) > self.limits.max_request_size:
            self._listener.reply_error(sender, ErrorCode.ILLEGAL_OPERATION, "request too large")
            return False
        try:
            request = decode(data)
            assert isinstance(request, Request)
            self.limits.check(request)
        except MalformedPacket as exc:
            self._listener.reply_error(sender, ErrorCode.ILLEGAL_OPERATION, str(exc))
            return False
        except TftpError as exc:
            self._listener.reply_error(sender, exc.code, exc.message)
            return False
        if self.max_sessions is not None and len(self._sessions) >= self.max_sessions:
            self._listener.reply_error(sender, ErrorCode.NOT_DEFINED, "relay busy")
            return False
        context = RequestContext(request, sender, local, ifindex)
        context.interface = arrival.interface
        try:
            target = self.route(request, context)
            if target is None:
                raise TftpError(ErrorCode.ACCESS_VIOLATION, "no route")
            family, upstream = self._resolve(to_upstream(target))
        except TftpError as exc:
            log.info("%r refused: %s", context, exc)
            self._listener.reply_error(sender, exc.code, exc.message)
            return False
        except Exception:
            log.exception("route failed for %r", context)
            self._listener.reply_error(sender, ErrorCode.NOT_DEFINED, "relay error")
            return False

        try:
            down, client = self._listener.reply_socket(arrival, self.port_range)
        except OSError as exc:
            log.warning("no transfer socket for %s: %s", sender[:2], exc)
            self._listener.reply_error(sender, ErrorCode.NOT_DEFINED, "relay busy")
            return False
        try:
            source = self.upstream_source or ("::" if family == socket.AF_INET6 else "0.0.0.0")
            up = bind_transfer(source, family, self.port_range)
        except OSError:
            down.close()
            self._listener.reply_error(sender, ErrorCode.NOT_DEFINED, "relay error")
            return False
        session = RelaySession(new_session_id("r"), client, key, down, up, upstream, request, context, now)
        self._sessions[key] = session
        self._selector.register(down, selectors.EVENT_READ, (session, "down"))
        self._selector.register(up, selectors.EVENT_READ, (session, "up"))
        log.debug(
            "[%s] %s %r: %s -> %s",
            session.id,
            "RRQ" if request.is_read else "WRQ",
            request.filename,
            client[:2],
            upstream,
        )
        self._emit(session, self._listener.sock, data, sender, "in", "client")
        self._send(session, up, data, upstream)  # the request, byte for byte
        return True

    def _on_datagram(self, session: RelaySession, leg: str, now: float) -> None:
        if session.closed:
            return
        sock = session.down if leg == "down" else session.up
        buf = self._buf
        for _ in range(_DRAIN):
            try:
                n, addr = sock.recvfrom_into(buf)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                continue
            data = bytes(buf[:n])
            if leg == "up":
                if session.upstream_tid is None:
                    if not same_host(addr, session.upstream):
                        continue  # not the server we asked
                    session.upstream_tid = addr  # RFC 1350 section 4: learn its TID
                if addr[0] != session.upstream_tid[0] or addr[1] != session.upstream_tid[1]:
                    self._send(session, sock, encode_error(ErrorCode.UNKNOWN_TID), addr)
                    continue
                self._emit(session, sock, data, addr, "in", "upstream")
                session.observe(data, False, now, self.linger)
                self._send(session, session.down, data, session.client)
            else:
                if addr[0] != session.client[0] or addr[1] != session.client[1]:
                    self._send(session, sock, encode_error(ErrorCode.UNKNOWN_TID), addr)
                    continue
                self._emit(session, sock, data, addr, "in", "client")
                if session.upstream_tid is None:
                    continue  # nothing to forward to yet
                session.observe(data, True, now, self.linger)
                self._send(session, session.up, data, session.upstream_tid)

    def _discard(self, key) -> None:
        """Drop a session whose opening failed half-way."""
        session = self._sessions.get(key)
        if session is not None:
            self._end(session, "error", time.monotonic())

    def _sweep(self, now: float) -> None:
        for session in list(self._sessions.values()):
            if now >= session.deadline(self.idle_timeout, self.max_lifetime):
                self._end(session, session.expiry_reason(now, self.idle_timeout, self.max_lifetime), now)

    def _end(self, session: RelaySession, reason: str, now: float) -> None:
        if session.closed:
            return
        session.closed = True
        session.reason = reason
        self._sessions.pop(session.key, None)
        for sock in (session.down, session.up):
            try:
                self._selector.unregister(sock)
            except (KeyError, ValueError):
                pass
            sock.close()
        summary = session.summary(now)
        self.stats.add("completed" if reason == "complete" else "failed")
        self.stats.add("bytes_to_clients", summary.bytes_to_client)
        self.stats.add("bytes_from_clients", summary.bytes_from_client)
        log.info(
            "[%s] %s %r %s <-> %s: %s, %d/%d bytes",
            session.id,
            summary.operation,
            summary.filename,
            summary.client[:2],
            summary.upstream[:2],
            reason,
            summary.bytes_to_client,
            summary.bytes_from_client,
        )
        if self.on_session_end is not None:
            try:
                self.on_session_end(summary)
            except Exception:
                log.exception("on_session_end callback failed")
