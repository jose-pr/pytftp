"""Giving a transfer's datagrams the TFTP layer pktcap's port-keyed dissection leaves off them."""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Iterator

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

    for frame in frames:
        datagram = frame.datagram()
        if datagram is None:
            yield frame
            continue
        event = tracker.feed(datagram)
        if event is None or event.session is None or datagram.truncated:
            yield frame
            continue
        try:
            dissected = dissect_tftp(datagram.payload)
        except pktcap.DissectError:
            yield frame
            continue
        if not isinstance(dissected.layer, TFTPLayer):  # dissect_tftp makes nothing else
            yield frame
            continue
        layer = dissected.layer._replace(session=event.session)
        index = next((i for i, known in enumerate(frame.layers) if isinstance(known, TFTPLayer)), None)
        if index is None:
            yield frame._replace(
                layers=frame.layers + (layer,), payloads=frame.payloads + (dissected.payload,)
            )
        else:
            layers = frame.layers[:index] + (layer,) + frame.layers[index + 1 :]
            yield frame._replace(layers=layers)


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
    lazy and keeps the order; nothing is read from ``frames`` before it is iterated. ``ImportError``
    when pktcap is not installed.
    """
    require_pktcap()
    return _followed(frames, tracker)
