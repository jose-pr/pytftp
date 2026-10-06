"""``pytftp replay``: ask a server again for the transfers a capture holds.

pytftp replay boot.pcapng 192.0.2.1
pytftp replay boot.pcapng 192.0.2.1 --speed 10 --json
"""

from __future__ import annotations

import json as _json
import os as _os
import sys as _sys
import typing as _ty

from .._text import escape
from ..capture._replay import replay_transfers
from ._common import Base, error, write_line

__all__ = ["ReplayCmd"]


class ReplayCmd(Base):
    """Ask a server for each transfer a capture holds again; reads only unless told otherwise."""

    _parsername_ = "replay"

    source: str
    "pcap/pcapng file, or '-' for a capture on stdin"
    ("source",)

    host: str
    "Server to ask, the only address anything is sent to: the capture's own addresses are never used"
    ("host",)

    port: int = 69
    "UDP port of the server"
    ("--port", "-p")

    request_port: _ty.List[int] = [69]
    "UDP port a request was sent to in the capture, to recognise transfers by; repeatable. Default: 69"
    ("--request-port",)

    writes: bool = False
    "Also replay captured uploads (WRQ): each one overwrites its file on the server. Default: reads only"
    ("--writes",)

    speed: float = 1.0
    "Wait the recorded gap between two transfers divided by this. Default: 1.0, the recorded pace"
    ("--speed",)

    max_delay: float = 5.0
    "Longest single wait, in seconds, whatever the capture's times say"
    ("--max-delay",)

    limit: _ty.Optional[int] = None
    "Replay at most this many transfers. Default: all"
    ("--limit",)

    timeout: float = 1.0
    "Seconds before a client retransmits"
    ("--timeout", "-t")

    retries: int = 5
    "Retransmissions of an unanswered packet before a transfer fails"
    ("--retries", "-r")

    def __call__(self) -> _ty.Optional[int]:
        if self.source == "-":
            source: _ty.Any = _sys.stdin.buffer
        elif not _os.path.isfile(self.source):
            raise ValueError("no such file: %s" % self.source)
        else:
            source = self.source
        try:
            done = replay_transfers(
                source,
                self.host,
                self.port,
                ports=self.request_port,
                writes=self.writes,
                speed=self.speed,
                max_delay=self.max_delay,
                limit=self.limit,
                timeout=self.timeout,
                retries=self.retries,
            )
        except OSError as exc:
            error("error: %s" % exc)
            return 1
        failed = 0
        for result in done.results:
            failed += result.error is not None
            if self.json_out:
                write_line(_json.dumps(result.to_dict()))
            elif result.error is None:
                write_line(
                    "ok %s %s: %d bytes in %.3fs, %d retransmits"
                    % (
                        result.operation,
                        escape(result.filename),
                        result.bytes,
                        result.duration,
                        result.retransmits,
                    )
                )
            else:
                write_line(
                    "failed %s %s: %s"
                    % (result.operation, escape(result.filename), escape(str(result.error)))
                )
        error("replayed %d transfers, %d failed, %d skipped" % (len(done.results), failed, done.skipped))
        return 1 if failed else None
