"""Reading a whole capture at once: its matching events and every transfer."""

from __future__ import annotations

import os
from typing import BinaryIO, Iterable, List, NamedTuple, Union, cast

from .._extras import require_pktcap
from ._events import PacketEvent
from ._flows import CapturedTransfer, DatagramLike, FlowTracker

__all__ = ["Analysis", "analyze"]


class Analysis(NamedTuple):
    """The TFTP in a capture: its events and every transfer seen."""

    events: List[PacketEvent]
    transfers: List[CapturedTransfer]


def analyze(
    source: Union[str, "os.PathLike[str]", BinaryIO, Iterable[DatagramLike]],
    *,
    ports: Iterable[int] = (69,),
    keep_payloads: bool = True,
) -> Analysis:
    """Read a whole capture (path, stream, or datagrams) and reconstruct its transfers.

    A path or a stream is read by ``pktcap.read_datagrams``, which raises
    ``pktcap.CaptureFormatError`` for a file that is not a capture. Every
    transfer is kept: the result holds what the capture holds.
    """
    if isinstance(source, (str, os.PathLike)) or hasattr(source, "read"):
        require_pktcap()
        from pktcap import read_datagrams

        datagrams: Iterable[DatagramLike] = read_datagrams(
            cast("Union[str, os.PathLike[str], BinaryIO]", source)
        )
    else:
        datagrams = source
    tracker = FlowTracker(ports, keep_payloads=keep_payloads, max_tracked=None)
    return Analysis(list(tracker.feed_all(datagrams)), tracker.transfers)
