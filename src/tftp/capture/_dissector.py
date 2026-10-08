"""TFTP as a pktcap layer: the dissector, its record and the call that registers it.

pktcap is imported inside the functions, so importing this module loads none of it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable, List, NamedTuple, Optional, Tuple

from .._extras import require_pktcap
from ..exceptions import TFTPDecodeError
from ..packet._codec import (
    AckPacket,
    DataPacket,
    ErrorPacket,
    RequestPacket,
    decode,
)

if TYPE_CHECKING:
    import pktcap

__all__ = ["TFTPLayer", "dissect_tftp", "register_tftp_dissector"]


class TFTPLayer(NamedTuple):
    """The TFTP packet a datagram carries, as plain values.

    ``opcode`` is the packet's name (``"RRQ"``, ``"WRQ"``, ``"DATA"``, ``"ACK"``, ``"ERROR"``,
    ``"OACK"``). Every other field is ``None`` where that kind of packet has none: ``block``
    (DATA, ACK), ``filename`` and ``mode`` (RRQ, WRQ, ``mode`` lower-cased), ``options`` (RRQ,
    WRQ, OACK: ``(name, value)`` pairs, names lower-cased, in the order sent), ``code`` and
    ``message`` (ERROR). Equal to another ``TFTPLayer`` with the same fields and to nothing else.
    """

    opcode: str
    block: Optional[int] = None
    filename: Optional[str] = None
    mode: Optional[str] = None
    options: Optional[Tuple[Tuple[str, str], ...]] = None
    code: Optional[int] = None
    message: Optional[str] = None

    # A plain tuple of the same fields is not equal: NotImplemented would let ``tuple`` answer.
    def __eq__(self, other: object) -> bool:
        return isinstance(other, TFTPLayer) and tuple.__eq__(self, other) is True

    def __ne__(self, other: object) -> bool:
        return not self == other

    def __hash__(self) -> int:
        return tuple.__hash__(self)


def dissect_tftp(data: bytes) -> "pktcap.Dissected":
    """Read the TFTP packet ``data`` holds: its :class:`TFTPLayer`, and a DATA's octets as the payload.

    The payload of every other packet is empty, and nothing follows a TFTP packet. Raises
    ``pktcap.DissectError`` (a ``ValueError``) for octets that are not a TFTP packet; its text
    holds none of them. Follows the contract ``pktcap.check_dissector`` checks.
    """
    require_pktcap()
    import pktcap

    try:
        packet = decode(data)
    except TFTPDecodeError:
        raise pktcap.DissectError("not a TFTP packet") from None
    payload = b""
    if isinstance(packet, RequestPacket):
        layer = TFTPLayer(
            packet.opcode.name,
            filename=packet.filename,
            mode=packet.mode,
            options=tuple(packet.options.items()),
        )
    elif isinstance(packet, DataPacket):
        layer, payload = TFTPLayer("DATA", block=packet.block), packet.data
    elif isinstance(packet, AckPacket):
        layer = TFTPLayer("ACK", block=packet.block)
    elif isinstance(packet, ErrorPacket):
        layer = TFTPLayer("ERROR", code=int(packet.code), message=packet.message)
    else:
        layer = TFTPLayer("OACK", options=tuple(packet.options.items()))
    return pktcap.Dissected(layer, payload)


def register_tftp_dissector(
    registry: Optional["pktcap.DissectorRegistry"] = None, *, ports: Iterable[int] = (69,)
) -> None:
    """Have ``registry`` (default: pktcap's process-wide one) dissect the datagrams to ``ports``.

    Registers :func:`dissect_tftp` under ``("udp", port)`` for each port. Nothing registers on
    import and nothing outside this call does. Only the request port is a selector: a transfer's
    DATA and ACK run between ports chosen per transfer, which :class:`FlowTracker` follows.
    ``ValueError`` for a port that is not 1 to 65535 or that already has a dissector (pktcap's
    rule), in which case nothing is registered by this call; ``TypeError`` for a port that is
    not an ``int``.
    """
    require_pktcap()
    import pktcap

    wanted: List[Any] = list(ports)
    for port in wanted:
        if isinstance(port, bool) or not isinstance(port, int):
            raise TypeError("a port is an int, not %s" % type(port).__name__)
        if not 0 < port < 65536:
            raise ValueError("a port is 1 to 65535, not %d" % port)
    target = registry if registry is not None else pktcap.default_registry()
    done: List[int] = []
    try:
        for port in wanted:
            target.register("udp", port, dissect_tftp)
            done.append(port)
    except ValueError:
        for port in done:
            target.unregister("udp", port)
        raise
