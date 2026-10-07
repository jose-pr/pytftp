"""The client's opening exchange: the request, its repeats and the first answer, without any I/O."""

from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional, Tuple, Union

from ..exceptions import (
    RemoteError,
    TFTPDecodeError,
    TFTPError,
    TFTPProtocolError,
    TransferTimeoutError,
)
from ..options._handler import DEFAULT_BLKSIZE, Negotiated
from ..options._negotiate import accept_oack
from ..options._registry import OptionRegistry
from ..packet._codec import (
    ErrorPacket,
    OptionAckPacket,
    _encode_error,
    encode_ack,
    encode_request,
)
from ..packet._enums import TFTPErrorCode, TFTPOpcode

__all__ = ["Requester"]

#: What a server answers a request that carries options with when it cannot
#: read them: RFC 2347's ERROR 8, and the "illegal operation" and "not
#: defined" that a server older than the extension sends for the extra data.
_OPTION_ERRORS = frozenset(
    {TFTPErrorCode.OPTION_REFUSED, TFTPErrorCode.ILLEGAL_OPERATION, TFTPErrorCode.NOT_DEFINED}
)

Address = Tuple[Any, ...]


class Requester:
    """Sends a request, repeats it until the first answer arrives and judges that answer.

    The request goes out from the constructor. The driver feeds it every
    datagram that arrives (:meth:`handle`) and calls :meth:`on_timeout` once
    ``deadline`` has passed; when ``is_done`` is set, ``error`` says whether the
    exchange failed and the outcome attributes say what the answer was. It
    reads no clock and touches no socket.

    :param send: ``send(packet, address)``: the request goes to ``server``, a
        refusal of the answer to the address it came from. May raise ``OSError``:
        the outcome is recorded first, then the error propagates.
    :param server: where the request goes: ``(host, port, ...)``, opaque but
        for its first two items in a message.
    :param opcode: ``RRQ`` or ``WRQ``.
    :param retries: repetitions of the request before giving up.
    :param now: the driver's clock when the request is sent.
    :param mode: ``"octet"`` or ``"netascii"``.
    :param options: the options the request carries, verbatim (``None`` for none).
    :param timeout: seconds before the first repeat, and the timeout of a
        transfer that runs on the defaults.
    :param backoff: each consecutive repeat waits this many times longer.
    :param max_timeout: ceiling for the backed-off wait; ``None`` is eight
        times ``timeout``.
    :param expires: the instant, in the driver's clock, after which the
        exchange fails whatever ``retries`` says, or ``None``.
    :param registry: the :class:`OptionRegistry` an OACK is validated with.
    :param accepts: ``accepts(address)``: whether a datagram from ``address`` may
        be the answer. One that may not is dropped without a reply. ``None``
        takes the first datagram from anywhere.
    :param fallback: allow :attr:`retry_without_options`.
    :param probe: the exchange only learns what the server answers: the answer
        is declined (ERROR 8 to an OACK, ACK 1 or ERROR 0 to DATA 1) and
        :attr:`negotiated` stays ``None``.

    :ivar is_done: the exchange has ended, in an answer or in a failure.
    :ivar error: the failure, or ``None``: the :class:`RemoteError` of the
        server's code, :class:`TFTPProtocolError` for an answer no server may
        send or an OACK that cannot be accepted, :class:`TransferTimeoutError`.
    :ivar deadline: when :meth:`on_timeout` is due, in the driver's clock;
        ``None`` once the exchange has ended.
    :ivar peer: the address of the answer, or ``None`` before one.
    :ivar negotiated: what the answer settles (:class:`Negotiated`), or ``None``
        on a failure and in a probe.
    :ivar first_data: the DATA 1 datagram when it was the answer to a read, else ``None``.
    :ivar oack: the options of the OACK the server sent, as sent, or ``None``.
    :ivar options: the options this request carried.
    :ivar retry_without_options: the answer was ERROR 8, 4 or 0 to a request that
        carried options and ``fallback`` is on: the request may be made again,
        by a new :class:`Requester` with ``options={}``.
    """

    __slots__ = (
        "_send",
        "_server",
        "_is_read",
        "_probe",
        "_retries",
        "_timeout",
        "_registry",
        "_accepts",
        "_fallback",
        "_request",
        "_timer",
        "_expires",
        "is_done",
        "error",
        "deadline",
        "peer",
        "negotiated",
        "first_data",
        "oack",
        "options",
        "retry_without_options",
    )

    def __init__(
        self,
        send: Callable[[bytes, Address], object],
        server: Address,
        opcode: int,
        filename: str,
        retries: int,
        now: float,
        *,
        mode: str = "octet",
        options: Optional[Mapping[str, str]] = None,
        timeout: float = 1.0,
        backoff: float = 2.0,
        max_timeout: Optional[float] = None,
        expires: Optional[float] = None,
        registry: Optional[OptionRegistry] = None,
        accepts: Optional[Callable[[Address], bool]] = None,
        fallback: bool = True,
        probe: bool = False,
    ) -> None:
        from netimps import Backoff  # pure arithmetic: the engine still does no I/O

        self._send = send
        self._server = server
        self._is_read = opcode == TFTPOpcode.RRQ
        self._probe = probe
        self._retries = retries
        self._timeout = timeout
        self._registry = registry
        self._accepts = accepts
        self._fallback = fallback
        self.options: Dict[str, str] = dict(options or {})
        self._request = encode_request(opcode, filename, mode=mode, options=options)
        ceiling = max(timeout, max_timeout if max_timeout is not None else timeout * 8)
        self._timer = Backoff(timeout, multiplier=max(1.0, backoff), max_delay=ceiling)  # RFC 1123 4.2.3.2
        self._expires = expires
        self.is_done = False
        self.error: Optional[TFTPError] = None
        self.deadline: Optional[float] = None
        self.peer: Optional[Address] = None
        self.negotiated: Optional[Negotiated] = None
        self.first_data: Optional[bytes] = None
        self.oack: Optional[Dict[str, str]] = None
        self.retry_without_options = False
        self._arm(now)
        send(self._request, server)

    def _arm(self, now: float) -> None:
        deadline = now + self._timer.delay
        if self._expires is not None and deadline > self._expires:
            deadline = self._expires
        self.deadline = deadline

    def _fail(self, error: TFTPError) -> None:
        self.error = error
        self.is_done = True
        self.deadline = None

    def on_timeout(self, now: float) -> None:
        """Repeat the request, or fail when the time limit or the retries are spent."""
        if self.is_done:
            return
        if self._expires is not None and now >= self._expires:
            self._fail(TransferTimeoutError("transfer exceeded its time limit"))
            return
        timer = self._timer
        if timer.attempt >= self._retries:
            self._fail(TransferTimeoutError("no response from %s:%s" % self._server[:2]))
            return
        timer.advance()
        self._arm(now)
        self._send(self._request, self._server)

    def handle(self, packet: memoryview, n: int, address: Address, now: float) -> None:
        """Take a datagram: it is the first answer, or it is dropped and nothing is sent."""
        if self.is_done or n < 2 or (self._accepts is not None and not self._accepts(address)):
            return
        self.peer = address
        self.deadline = None
        self.is_done = True
        op = packet[1] if packet[0] == 0 else -1
        try:
            if op == TFTPOpcode.ERROR:
                self._refused(ErrorPacket.decode(packet[:n]))
                return
            if op == TFTPOpcode.OACK:
                self._oack(OptionAckPacket.decode(packet[:n]).options)
                return
        except TFTPDecodeError as exc:
            self.error = TFTPProtocolError("malformed answer to the request: %s" % exc)
            self.error.__cause__ = exc
            if op != TFTPOpcode.ERROR:
                self._reply(_encode_error(TFTPErrorCode.ILLEGAL_OPERATION, "malformed answer"))
            return
        if self._is_first_packet(packet, n):
            if self._probe:
                self.first_data = bytes(packet[:n])
                if n - 4 < DEFAULT_BLKSIZE:
                    self._reply(encode_ack(1))  # the whole file: finish politely
                else:
                    self._reply(_encode_error(TFTPErrorCode.NOT_DEFINED, "size probe only"))
                return
            self.negotiated = Negotiated(timeout=self._timeout)
            if self._is_read:
                self.first_data = bytes(packet[:n])
            return
        self.error = TFTPProtocolError("unexpected opcode %d in response to the request" % op)
        self._reply(_encode_error(TFTPErrorCode.ILLEGAL_OPERATION, "unexpected opcode %d" % op))

    def _is_first_packet(self, packet: memoryview, n: int) -> bool:
        """The packet an option-less server starts with: DATA 1 for a read, ACK 0 for a write."""
        if n < 4 or packet[0] != 0:
            return False
        want_op, want_block = (TFTPOpcode.DATA, 1) if self._is_read else (TFTPOpcode.ACK, 0)
        return packet[1] == want_op and packet[2] == 0 and packet[3] == want_block

    def _refused(self, packet: ErrorPacket) -> None:
        self.error = RemoteError.from_code(packet.code, packet.message)
        self.retry_without_options = bool(self.options) and self._fallback and packet.code in _OPTION_ERRORS

    def _oack(self, options: Mapping[str, str]) -> None:
        if self._probe:
            self.oack = dict(options)
            self._reply(_encode_error(TFTPErrorCode.OPTION_REFUSED, "size probe only"))
            return
        try:
            self.negotiated = accept_oack(
                self.options, options, is_read=self._is_read, timeout=self._timeout, registry=self._registry
            )
        except TFTPProtocolError as exc:
            self.error = exc
            self._reply(_encode_error(exc.code, exc.message))
            return
        self.oack = dict(options)

    def _reply(self, packet: Union[bytes, memoryview]) -> None:
        """Say something to the peer of the answer; the outcome is already recorded."""
        assert self.peer is not None
        self._send(bytes(packet), self.peer)
