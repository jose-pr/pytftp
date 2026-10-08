"""``pytftp replay``: pktcap's replay command, asking a server again for the transfers a capture holds.

pytftp replay --input boot.pcapng --to 192.0.2.1
pytftp replay --input boot.pcapng --to 192.0.2.1:6969 --speed 10 --json
"""

from __future__ import annotations

import argparse
import json as _json
import typing as _ty

from pktcap.cli import Replay

from .._text import escape
from ..capture._replay import ReplayedTransfers, replay_transfers
from ._common import error, write_line

__all__ = ["ReplayCmd"]


class ReplayCmd(Replay):
    """Ask a server for each transfer a capture holds again; reads only unless told otherwise.

    No datagram of the capture is sent: a client asks the server --to names for each file again.
    """

    _parsername_ = "replay"
    _logger_name_ = "tftp"
    _plugins_ = ("tftp.capture",)
    _default_port_ = 69

    filter: _ty.Optional[str] = None
    "Replay only the transfers of the packets matching this: op, file, block, code and src, dst, host, sport, dport, port, e.g. 'file=*.efi'. Default: every packet"
    ("--filter", "-f")

    limit: _ty.Optional[int] = None
    "Replay at most this many transfers. Default: all"
    ("--limit",)

    source_port: _ty.Annotated[_ty.Optional[int], argparse.SUPPRESS] = None

    broadcast: _ty.Annotated[bool, argparse.SUPPRESS] = False

    json_out: bool = False
    "Print each transfer as one JSON object on one line, instead of text"
    ("--json",)

    request_port: _ty.List[int] = [69]
    "UDP port a request was sent to in the capture, to recognise transfers by; repeatable. Default: 69"
    ("--request-port",)

    writes: bool = False
    "Also replay captured uploads (WRQ): each one overwrites its file on the server. Default: reads only"
    ("--writes",)

    timeout: float = 1.0
    "Seconds before a client retransmits"
    ("--timeout", "-t")

    retries: int = 5
    "Retransmissions of an unanswered packet before a transfer fails"
    ("--retries", "-r")

    def _replay(self, datagrams: _ty.Any, host: str, port: int) -> ReplayedTransfers:
        return replay_transfers(
            datagrams,
            host,
            port,
            ports=self.request_port,
            writes=self.writes,
            speed=None if self.no_delay else self.speed,
            max_delay=self.max_delay,
            limit=self.limit,
            timeout=self.timeout,
            retries=self.retries,
        )

    def _report(self, result: ReplayedTransfers) -> _ty.Optional[int]:
        failed = 0
        for item in result.results:
            failed += item.error is not None
            if self.json_out:
                write_line(_json.dumps(item.to_dict()))
            elif item.error is None:
                write_line(
                    "ok %s %s: %d bytes in %.3fs, %d retransmits"
                    % (item.operation, escape(item.filename), item.bytes, item.duration, item.retransmits)
                )
            else:
                write_line(
                    "failed %s %s: %s" % (item.operation, escape(item.filename), escape(str(item.error)))
                )
        error("replayed %d transfers, %d failed, %d skipped" % (len(result.results), failed, result.skipped))
        return 1 if failed else None
