"""``pytftp capture``: decode TFTP from a pcap/pcapng file, a live pipe, or an interface.

pytftp capture boot.pcapng --transfers
tcpdump -i eth0 -U -w - udp | pytftp capture - --filter "op=RRQ,WRQ,ERROR"
sudo pytftp capture -i eth0 --extract recovered/          (Linux)
"""

from __future__ import annotations

import json as _json
import os as _os
import sys as _sys
import typing as _ty

from ..capture._filters import compile_filter
from ..capture._flows import FlowTracker
from ._common import Base, error, write_line

if _ty.TYPE_CHECKING:
    from pktcap import FrameDissector

__all__ = ["CaptureCmd"]

#: Link-type numbers named in the line about frames nothing here reads.
_LISTED_LINKTYPES = 8


def _datagrams_of(frames: _ty.Iterable[_ty.Any], unread: _ty.Dict[int, int]) -> _ty.Iterator[_ty.Any]:
    """The UDP datagrams of dissected frames; ``unread`` counts the frames no dissector took, by link type."""
    for frame in frames:
        if not frame.layers and frame.error is None:
            unread[frame.frame.linktype] = unread.get(frame.frame.linktype, 0) + 1
        datagram = frame.datagram()
        if datagram is not None:
            yield datagram


def _unread_line(stats: _ty.Any, unread: _ty.Dict[int, int]) -> _ty.Optional[str]:
    """The one line about frames that were not dissected, or ``None`` when every frame was."""
    if not (stats.unsupported or stats.malformed):
        return None
    parts = []
    if stats.unsupported:
        numbers = sorted(unread)
        listed = ", ".join(str(number) for number in numbers[:_LISTED_LINKTYPES])
        parts.append(
            "%d of an unsupported link type (%s%s)"
            % (stats.unsupported, listed, ", ..." if len(numbers) > _LISTED_LINKTYPES else "")
        )
    if stats.malformed:
        parts.append("%d malformed" % stats.malformed)
    return "warning: %d of %d frames not read: %s" % (
        stats.unsupported + stats.malformed,
        stats.frames,
        ", ".join(parts),
    )


class CaptureCmd(Base):
    """Show the TFTP in a capture, reconstruct its transfers, extract their files."""

    _parsername_ = "capture"

    source: _ty.Optional[str] = None
    "pcap/pcapng file, or '-' for a live pipe on stdin (tcpdump -U -w -)"
    ("source",)

    interface: _ty.Optional[str] = None
    "Capture live from this interface instead (Linux, needs root/CAP_NET_RAW)"
    ("--interface", "-i")

    port: _ty.List[int] = [69]
    "UDP port a request is sent to, to recognise transfers by; repeatable. Default: 69"
    ("--port", "-p")

    filter: _ty.Optional[str] = None
    "Show only matching packets, e.g. 'op=RRQ,ERROR and host=10.0.0.0/8'. Default: every packet"
    ("--filter", "-f")

    no_packets: bool = False
    "Do not list packets (use with --transfers or --extract). Default: list them"
    ("--no-packets",)

    transfers: bool = False
    "Print a summary of every transfer at the end. Default: off"
    ("--transfers",)

    extract: _ty.Optional[str] = None
    "Write each transfer's file into this directory. Default: write none"
    ("--extract",)

    payload: bool = False
    "Include DATA payloads (hex) in --json output. Default: left out"
    ("--payload",)

    def _datagrams(self, dissector: "FrameDissector", unread: _ty.Dict[int, int]) -> _ty.Iterable[_ty.Any]:
        import pktcap

        if self.interface:
            if not pktcap.has_live_capture():
                raise ValueError(
                    "live capture needs Linux; pipe a capture instead: tcpdump -U -w - udp | pytftp capture -"
                )
            return pktcap.sniff(self.interface, dissector=dissector)
        if not self.source:
            raise ValueError("give a capture file, '-' for stdin, or --interface")
        if self.source == "-":
            return _datagrams_of(pktcap.read_dissected(_sys.stdin.buffer, dissector=dissector), unread)
        if not _os.path.isfile(self.source):
            raise ValueError("no such file: %s" % self.source)
        return _datagrams_of(pktcap.read_dissected(self.source, dissector=dissector), unread)

    def __call__(self) -> _ty.Optional[int]:
        import pktcap

        try:
            wanted = compile_filter(self.filter)
            dissector = pktcap.FrameDissector()
            unread: _ty.Dict[int, int] = {}
            datagrams = self._datagrams(dissector, unread)
        except ValueError as exc:
            error("error: %s" % exc)
            return 2
        # A transfer the tracker lets go of (a live capture holds a bounded number) is
        # summarised and written when it goes, the rest at the end.
        written: _ty.List[_ty.Any] = []
        tracker = FlowTracker(
            self.port,
            keep_payloads=bool(self.extract),
            on_complete=lambda transfer: self._finish(transfer, written),
        )
        try:
            for event in tracker.feed_all(datagrams):
                if self.no_packets or not wanted(event):
                    continue
                if self.json_out:
                    write_line(_json.dumps(event.to_dict(payload=self.payload)))
                else:
                    write_line(str(event))
        except pktcap.CaptureFormatError as exc:
            error("error: %s" % exc)
            return 2
        except KeyboardInterrupt:
            pass
        stats = dissector.stats
        line = _unread_line(stats, unread)
        if line:
            error(line)
        for transfer in list(tracker.transfers):
            self._finish(transfer, written)
        if self.extract and not written:
            error("no transfer data to extract")
        return 2 if stats.frames and stats.unsupported == stats.frames else None

    def _finish(self, transfer: _ty.Any, written: _ty.List[_ty.Any]) -> None:
        """The transfer is complete as far as this capture goes: summarise it, write its file."""
        if self.transfers:
            if self.json_out:
                write_line(_json.dumps({"transfer": transfer.to_dict()}))
            else:
                write_line(repr(transfer))
        if self.extract:
            path = transfer.write_to(self.extract)
            if path is not None:
                written.append(path)
                error(
                    "wrote %s (%d bytes%s)"
                    % (path, _os.path.getsize(path), ", incomplete" if path.endswith(".partial") else "")
                )
