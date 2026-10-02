"""``pytftp capture``: decode TFTP from a pcap/pcapng file, a live pipe, or an interface.

pytftp capture boot.pcapng --transfers
tcpdump -i eth0 -U -w - udp | pytftp capture - --filter "op=RRQ,WRQ,ERROR"
sudo pytftp capture -i eth0 --extract recovered/          (Linux)
"""

from __future__ import annotations

import json as _json
import os as _os
import re as _re
import sys as _sys
import typing as _ty

from ..capture import (
    CaptureFormatError,
    FilterError,
    FlowTracker,
    compile_filter,
    live_capture_supported,
    read_datagrams,
    sniff,
)
from .common import Base, error

__all__ = ["CaptureCmd"]

_UNSAFE = _re.compile(r"[^A-Za-z0-9_.-]+")


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
    "Request port to recognise transfers by; repeatable"
    ("--port", "-p")

    filter: _ty.Optional[str] = None
    "Show only matching packets, e.g. 'op=RRQ,ERROR and host=10.0.0.0/8'"
    ("--filter", "-f")

    no_packets: bool = False
    "Do not list packets (use with --transfers or --extract)"
    ("--no-packets",)

    transfers: bool = False
    "Print a summary of every transfer at the end"
    ("--transfers",)

    extract: _ty.Optional[str] = None
    "Write each transfer's file into this directory"
    ("--extract",)

    payload: bool = False
    "Include DATA payloads (hex) in --json output"
    ("--payload",)

    def _datagrams(self) -> _ty.Iterable[_ty.Any]:
        if self.interface:
            if not live_capture_supported():
                raise ValueError(
                    "live capture needs Linux; pipe a capture instead: tcpdump -U -w - udp | pytftp capture -"
                )
            return sniff(self.interface)
        if not self.source:
            raise ValueError("give a capture file, '-' for stdin, or --interface")
        if self.source == "-":
            return read_datagrams(_sys.stdin.buffer)
        if not _os.path.isfile(self.source):
            raise ValueError("no such file: %s" % self.source)
        return read_datagrams(self.source)

    def __call__(self) -> "int | None":
        try:
            wanted = compile_filter(self.filter)
            datagrams = self._datagrams()
        except (ValueError, FilterError) as exc:
            error("error: %s" % exc)
            return 2
        tracker = FlowTracker(self.port, keep_payloads=bool(self.extract))
        try:
            for event in tracker.feed_all(datagrams):
                if self.no_packets or not wanted(event):
                    continue
                if self.json_out:
                    print(_json.dumps(event.to_dict(payload=self.payload)), flush=True)
                else:
                    print(event.format(), flush=True)
        except CaptureFormatError as exc:
            error("error: %s" % exc)
            return 2
        except KeyboardInterrupt:
            pass
        if self.transfers:
            for transfer in tracker.transfers:
                if self.json_out:
                    print(_json.dumps({"transfer": transfer.to_dict()}))
                else:
                    print(transfer)
        if self.extract:
            self._extract(tracker)
        return None

    def _extract(self, tracker: FlowTracker) -> None:
        _os.makedirs(self.extract, exist_ok=True)  # type: ignore[arg-type]
        written = 0
        for transfer in tracker.transfers:
            data = transfer.data()
            if not data and not transfer.complete:
                continue
            name = _UNSAFE.sub("_", transfer.filename.replace("\\", "/").rsplit("/", 1)[-1]) or "file"
            suffix = "" if transfer.complete and not transfer.missing_blocks else ".partial"
            path = _os.path.join(self.extract, "%s-%s%s" % (transfer.session, name, suffix))  # type: ignore[arg-type]
            with open(path, "wb") as handle:
                handle.write(data)
            written += 1
            error("wrote %s (%d bytes%s)" % (path, len(data), ", incomplete" if suffix else ""))
        if not written:
            error("no transfer data to extract")
