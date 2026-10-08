"""Seeing TFTP on the wire: packet events, trace hooks, filters, captures.

Three sources of :class:`PacketEvent`:

- ``trace=`` hooks on ``TFTPClient``, ``TFTPServer`` and ``TFTPRelay`` -- what this
  library sent and received, with session ids;
- pcap/pcapng captures, from a file or a live ``tcpdump -w -`` pipe, read by
  pktcap (``pktcap.read_datagrams``) and followed by :class:`FlowTracker`, or by
  :func:`analyze` in one call;
- live capture on Linux (``pktcap.sniff``).

:func:`dissect_tftp` is TFTP as a pktcap layer (:class:`TFTPLayer`), registered by
:func:`register_tftp_dissector` and by nothing else; :func:`pktcap_plugin` is the hook that
lets pktcap load it by name, with the filter keys ``op``, ``file``, ``block``, ``code`` and
``session``. :func:`follow_transfers` gives a transfer's datagrams, which run between ports
chosen for it, the layer and the transfer's session.

:func:`replay_transfers` asks a server the caller names for each transfer a capture holds again.

:func:`trace_to` turns a pktcap writer (``pktcap.PcapWriter``,
``pktcap.PcapngWriter``) into a trace hook, so a run is recorded for Wireshark, and
:func:`combine_hooks` joins several trace hooks into the one ``trace=`` takes.
"""

from __future__ import annotations

from ._analysis import Analysis, analyze
from ._dissector import TFTPLayer, dissect_tftp, register_tftp_dissector
from ._events import PacketEvent, new_session_id, summarize
from ._filters import FILTER_KEYS, EventPredicate, compile_filter
from ._flows import CapturedTransfer, DatagramLike, Endpoint, FlowTracker
from ._follow import follow_transfers
from ._hook import DatagramWriter, combine_hooks, trace_to
from ._plugin import pktcap_plugin
from ._replay import ReplayedTransfers, replay_transfers

__all__ = [
    "PacketEvent",
    "summarize",
    "new_session_id",
    "compile_filter",
    "EventPredicate",
    "Endpoint",
    "FILTER_KEYS",
    "CapturedTransfer",
    "DatagramLike",
    "DatagramWriter",
    "FlowTracker",
    "combine_hooks",
    "trace_to",
    "follow_transfers",
    "TFTPLayer",
    "dissect_tftp",
    "register_tftp_dissector",
    "pktcap_plugin",
    "ReplayedTransfers",
    "replay_transfers",
    "Analysis",
    "analyze",
]
