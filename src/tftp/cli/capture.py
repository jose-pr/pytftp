"""``pytftp capture``: pktcap's capture command with TFTP loaded, following each transfer across its ports.

pytftp capture --input boot.pcapng --transfers
tcpdump -i eth0 -U -w - udp | pytftp capture --input - --filter "op=RRQ,WRQ,ERROR"
sudo pytftp capture --interface eth0 --extract recovered/          (Linux)
"""

from __future__ import annotations

import argparse
import os as _os
import sys as _sys
import typing as _ty

from duho import Meta
from pktcap.cli import Capture

from ..capture._flows import CapturedTransfer, FlowTracker
from ..capture._follow import follow_transfers
from ._common import error

if _ty.TYPE_CHECKING:
    from pktcap import DissectedFrame, FrameDissector

__all__ = ["CaptureCmd"]

_NOT_LINUX = (
    "live capture needs Linux (AF_PACKET); pipe a capture tool's output in instead: "
    "tcpdump -U -w - udp | pytftp capture --input -"
)


class CaptureCmd(Capture):
    """Show the TFTP in a capture or on an interface, reconstruct its transfers, extract their files.

    Writes what pktcap's capture command writes, filtered to the TFTP packets: one readable line a packet by default.
    """

    _parsername_ = "capture"
    _logger_name_ = "tftp"
    _plugins_ = ("tftp.capture",)
    _filter_ = "proto=tftp"
    _format_ = "text"

    input: _ty.Annotated[_ty.Optional[str], Meta(conflicts="source")] = None
    "Read this pcap or pcapng file, or '-' for a live pipe on stdin (tcpdump -U -w -), instead of capturing. Excludes --interface"
    ("--input", "-i")

    interface: _ty.Annotated[_ty.Optional[str], Meta(conflicts="source")] = None
    "Capture live from this interface, by name, address or MAC (Linux, needs root or CAP_NET_RAW). Default: every interface. Excludes --input"
    ("--interface",)

    listen: _ty.Annotated[_ty.Optional[_ty.List[str]], argparse.SUPPRESS] = None

    port: _ty.List[int] = [69]
    "UDP port a request is sent to, to recognise transfers by; repeatable. Default: 69"
    ("--port", "-p")

    filter: _ty.Optional[str] = None
    "Keep only matching packets: op, file, block, code and session (tftp.session=c3) beside pktcap's src, dst, host, sport, dport, port; e.g. 'op=RRQ,ERROR and host=10.0.0.0/8'. Default: every TFTP packet"
    ("--filter", "-f")

    no_packets: bool = False
    "Write no packets (use with --transfers or --extract). Default: write them"
    ("--no-packets",)

    transfers: bool = False
    "Print a line for every transfer on stderr at the end. Default: off"
    ("--transfers",)

    extract: _ty.Optional[str] = None
    "Write each transfer's file into this directory. Default: write none"
    ("--extract",)

    _tracker: _ty.Optional[FlowTracker] = None
    _written: _ty.Optional[_ty.List[str]] = None

    def _read(self, dissector: "FrameDissector") -> "_ty.Iterator[DissectedFrame]":
        import pktcap

        assert self.input is not None
        name = "standard input" if self.input == "-" else self.input
        source: _ty.Any = self.input
        if self.input == "-":
            source = getattr(_sys.stdin, "buffer", None)
            if source is None:
                raise ValueError("--input - needs a standard input: name a file")
        try:
            yield from pktcap.read_dissected(source, dissector=dissector)
        except pktcap.CaptureFormatError as exc:
            raise ValueError("%s: %s" % (name, exc)) from exc

    def _frames(self, dissector: "FrameDissector") -> "_ty.Iterator[DissectedFrame]":
        """The file, or the interface, with every transfer followed across its ports."""
        import pktcap

        if self.input is None and not pktcap.has_live_capture():
            raise pktcap.LiveCaptureError(_NOT_LINUX)
        self._written = []
        # A transfer the tracker lets go of (a live capture holds a bounded number) is
        # summarised and written when it goes, the rest at the end.
        self._tracker = FlowTracker(self.port, keep_payloads=bool(self.extract), on_complete=self._finish)
        source = self._read(dissector) if self.input is not None else super()._frames(dissector)
        return follow_transfers(source, self._tracker)

    def _select(self, registry: _ty.Any) -> "_ty.Callable[[DissectedFrame], bool]":
        wanted = super()._select(registry)  # compiled either way: a bad expression is an error
        if self.no_packets:
            return lambda frame: False
        return wanted

    def _report(self, result: _ty.Any, dissector: "FrameDissector") -> int:
        if self._tracker is not None:
            for transfer in list(self._tracker.transfers):
                self._finish(transfer)
            if self.extract and not self._written:
                error("no transfer data to extract")
        return super()._report(result, dissector)

    def _finish(self, transfer: CapturedTransfer) -> None:
        """The transfer is complete as far as this capture goes: summarise it, write its file."""
        if self.transfers:
            error(repr(transfer))
        if self.extract and self._written is not None:
            path = transfer.write_to(self.extract)
            if path is not None:
                self._written.append(path)
                error(
                    "wrote %s (%d bytes%s)"
                    % (path, _os.path.getsize(path), ", incomplete" if path.endswith(".partial") else "")
                )
