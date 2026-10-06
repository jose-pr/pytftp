"""Seeing TFTP on the wire: packet events, trace hooks, filters, captures.

Three sources of :class:`PacketEvent`:

- ``trace=`` hooks on ``TFTPClient``, ``TFTPServer`` and ``TFTPRelay`` -- what this
  library sent and received, with session ids;
- pcap/pcapng captures (:func:`read_datagrams` + :class:`FlowTracker`, or
  :func:`analyze`), from a file or a live ``tcpdump -w -`` pipe;
- live capture on Linux (:func:`sniff`).

:class:`PcapWriter` turns trace events back into a capture for Wireshark.
"""

from __future__ import annotations

import os
from typing import BinaryIO, Iterable, List, NamedTuple, Optional, Union

from .events import PacketEvent, new_session_id, summarize
from .filters import FILTER_KEYS, CaptureFilterError, compile_filter
from .flows import CapturedTransfer, FlowTracker
from .frames import LINKTYPES, FrameDecoder, UDPDatagram
from .live import live_capture_supported, sniff
from .pcap import CaptureFormatError, PcapWriter, read_datagrams, read_frames

__all__ = [
    "PacketEvent",
    "summarize",
    "new_session_id",
    "compile_filter",
    "CaptureFilterError",
    "FILTER_KEYS",
    "CapturedTransfer",
    "FlowTracker",
    "UDPDatagram",
    "FrameDecoder",
    "LINKTYPES",
    "read_frames",
    "read_datagrams",
    "CaptureFormatError",
    "PcapWriter",
    "sniff",
    "live_capture_supported",
    "Analysis",
    "analyze",
]


class Analysis(NamedTuple):
    """The TFTP in a capture: matching events and every transfer seen."""

    events: List[PacketEvent]
    transfers: List[CapturedTransfer]


def analyze(
    source: Union[str, "os.PathLike[str]", BinaryIO, Iterable[UDPDatagram]],
    *,
    ports: Iterable[int] = (69,),
    filter: Optional[str] = None,
    keep_payloads: bool = True,
) -> Analysis:
    """Read a whole capture (path, stream, or datagrams) and reconstruct its transfers.

    ``filter`` (see :func:`compile_filter`) selects events; transfers are
    always reconstructed from everything, and every one is kept: the result
    holds what the capture holds.
    """
    datagrams = source if not isinstance(source, (str, os.PathLike)) and not hasattr(source, "read") else None
    if datagrams is None:
        datagrams = read_datagrams(source)  # type: ignore[arg-type]
    tracker = FlowTracker(ports, keep_payloads=keep_payloads, max_tracked=None)
    wanted = compile_filter(filter)
    events = [e for e in tracker.feed_all(datagrams) if wanted(e)]  # type: ignore[arg-type]
    return Analysis(events, tracker.transfers)
