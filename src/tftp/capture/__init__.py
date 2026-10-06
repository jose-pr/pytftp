"""Seeing TFTP on the wire: packet events, trace hooks, filters, captures.

Three sources of :class:`PacketEvent`:

- ``trace=`` hooks on ``TFTPClient``, ``TFTPServer`` and ``TFTPRelay`` -- what this
  library sent and received, with session ids;
- pcap/pcapng captures, from a file or a live ``tcpdump -w -`` pipe, read by
  pktcap (``pktcap.read_datagrams``) and followed by :class:`FlowTracker`, or by
  :func:`analyze` in one call;
- live capture on Linux (``pktcap.sniff``).

:func:`trace_to` turns a pktcap writer (``pktcap.PcapWriter``,
``pktcap.PcapngWriter``) into a trace hook, so a run is recorded for Wireshark, and
:func:`combine_hooks` joins several trace hooks into the one ``trace=`` takes.
"""

from __future__ import annotations

import os
from typing import BinaryIO, Iterable, List, NamedTuple, Optional, Union, cast

from ._events import PacketEvent, new_session_id, summarize
from ._filters import FILTER_KEYS, CaptureFilterError, EventPredicate, compile_filter
from ._flows import CapturedTransfer, DatagramLike, Endpoint, FlowTracker
from ._hook import DatagramWriter, combine_hooks, trace_to

__all__ = [
    "PacketEvent",
    "summarize",
    "new_session_id",
    "compile_filter",
    "EventPredicate",
    "Endpoint",
    "CaptureFilterError",
    "FILTER_KEYS",
    "CapturedTransfer",
    "DatagramLike",
    "DatagramWriter",
    "FlowTracker",
    "combine_hooks",
    "trace_to",
    "Analysis",
    "analyze",
]


class Analysis(NamedTuple):
    """The TFTP in a capture: matching events and every transfer seen."""

    events: List[PacketEvent]
    transfers: List[CapturedTransfer]


def analyze(
    source: Union[str, "os.PathLike[str]", BinaryIO, Iterable[DatagramLike]],
    *,
    ports: Iterable[int] = (69,),
    filter: Optional[str] = None,
    keep_payloads: bool = True,
) -> Analysis:
    """Read a whole capture (path, stream, or datagrams) and reconstruct its transfers.

    A path or a stream is read by ``pktcap.read_datagrams``, which raises
    ``pktcap.CaptureFormatError`` for a file that is not a capture. ``filter``
    (see :func:`compile_filter`) selects events; transfers are always
    reconstructed from everything, and every one is kept: the result holds what
    the capture holds.
    """
    if isinstance(source, (str, os.PathLike)) or hasattr(source, "read"):
        from pktcap import read_datagrams

        datagrams: Iterable[DatagramLike] = read_datagrams(
            cast("Union[str, os.PathLike[str], BinaryIO]", source)
        )
    else:
        datagrams = source
    tracker = FlowTracker(ports, keep_payloads=keep_payloads, max_tracked=None)
    wanted = compile_filter(filter)
    events = [e for e in tracker.feed_all(datagrams) if wanted(e)]
    return Analysis(events, tracker.transfers)
