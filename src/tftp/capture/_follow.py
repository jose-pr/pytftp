"""Giving a transfer's datagrams the TFTP layer pktcap's port-keyed dissection leaves off them."""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Iterator, Type

from .._extras import require_pktcap
from ._dissector import TFTPLayer, dissect_tftp
from ._flows import FlowTracker

if TYPE_CHECKING:
    import pktcap

__all__ = ["follow_transfers"]


def _followed(
    frames: "Iterable[pktcap.DissectedFrame]", tracker: FlowTracker
) -> "Iterator[pktcap.DissectedFrame]":
    import pktcap

    try:
        for frame in frames:
            yield _attributed(frame, tracker, pktcap.DissectError)
    finally:
        close = getattr(frames, "close", None)
        if close is not None:
            close()


def _attributed(
    frame: "pktcap.DissectedFrame", tracker: FlowTracker, not_tftp: "Type[Exception]"
) -> "pktcap.DissectedFrame":
    """``frame``, with the layer of its transfer when ``tracker`` attributes its datagram to one."""
    datagram = frame.datagram()
    if datagram is None:
        return frame
    event = tracker.feed(datagram)
    if event is None or event.session is None or datagram.truncated:
        return frame
    try:
        dissected = dissect_tftp(datagram.payload)
    except not_tftp:
        return frame
    if not isinstance(dissected.layer, TFTPLayer):  # dissect_tftp makes nothing else
        return frame
    layer = dissected.layer._replace(session=event.session)
    index = next((i for i, known in enumerate(frame.layers) if isinstance(known, TFTPLayer)), None)
    if index is None:
        return frame._replace(layers=frame.layers + (layer,), payloads=frame.payloads + (dissected.payload,))
    return frame._replace(layers=frame.layers[:index] + (layer,) + frame.layers[index + 1 :])


def follow_transfers(
    frames: "Iterable[pktcap.DissectedFrame]", tracker: FlowTracker
) -> "Iterator[pktcap.DissectedFrame]":
    """``frames`` with a :class:`TFTPLayer` on each datagram ``tracker`` attributes to a transfer.

    pktcap dissects TFTP by port, so only a datagram to or from the request port has a layer; a
    transfer's DATA, ACK and OACK run between ports chosen for it. Every UDP datagram is fed to
    ``tracker``, and one it attributes to a transfer is yielded with its layer, whose ``session`` is
    the transfer's: a layer the dissector already made is replaced, otherwise the layer is added
    after the UDP layer and the octets that follow it (a DATA's) become the frame's payload. A frame
    with no UDP datagram, one the tracker does not attribute, one a snap length cut (the tracker
    counts it) and one whose octets are not a TFTP packet are yielded as they are. The result is
    lazy and keeps the order; nothing is read from ``frames`` before it is iterated, and closing the
    result closes ``frames`` when it can be closed. ``ImportError`` when pktcap is not installed.
    """
    require_pktcap()
    return _followed(frames, tracker)
