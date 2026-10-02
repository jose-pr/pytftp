"""What every command shares: duho imports (or stand-ins), base options, output."""

from __future__ import annotations

import json as _json
import socket as _socket
import sys as _sys
import typing as _ty

if _ty.TYPE_CHECKING:
    from duho import AUTO, Args, Choice, Cmd, LoggingArgs
else:
    try:
        from duho import AUTO, Args, Choice, Cmd, LoggingArgs
    except ImportError:
        # The console script imports this module before it can call
        # anything, so the import must survive a missing extra. The
        # stand-ins only let the classes below be defined; run() refuses
        # before using one.
        AUTO = None

        def Choice(*_args, **_kwargs):  # noqa: N802
            return None

        class Args:
            pass

        class Cmd:
            pass

        class LoggingArgs:
            pass


from ..client import Client
from ..result import TransferResult


def error(text: str) -> None:
    print(text, file=_sys.stderr)


def result_json(result: TransferResult) -> dict:
    negotiated = result.negotiated
    return {
        "ok": result.ok,
        "operation": result.operation,
        "filename": result.filename,
        "mode": result.mode,
        "peer": list(result.peer[:2]),
        "bytes": result.bytes,
        "blocks": result.blocks,
        "retransmits": result.retransmits,
        "duration": round(result.duration, 6),
        "blksize": negotiated.blksize,
        "windowsize": negotiated.windowsize,
        "tsize": negotiated.tsize,
        "options": negotiated.options,
        "error": None if result.error is None else str(result.error),
    }


class Base(LoggingArgs, Cmd):
    """Shared options."""

    _logger_name_ = "tftp"

    json_out: bool = False
    "Emit JSON on stdout instead of human-readable text"
    ("--json",)


class ClientCmd(Base):
    """Options every client command takes."""

    port: int = 69
    "Server port"
    ("--port", "-p")

    mode: _ty.Annotated[str, Choice("octet", "netascii")] = "octet"
    "Transfer mode"
    ("--mode", "-m")

    blksize: int = 1428
    "Block size to request, 8-65464; 0 requests none (512)"
    ("--blksize", "-b")

    windowsize: int = 0
    "RFC 7440 window to request; 0 requests none (1)"
    ("--windowsize", "-w")

    timeout: float = 1.0
    "Seconds before retransmitting"
    ("--timeout", "-t")

    retries: int = 5
    "Retransmissions before giving up"
    ("--retries", "-r")

    no_tsize: bool = False
    "Do not request or announce the transfer size"
    ("--no-tsize",)

    no_options: bool = False
    "Send a plain RFC 1350 request with no options at all"
    ("--no-options",)

    ipv4: bool = False
    "Use IPv4"
    ("-4",)

    ipv6: bool = False
    "Use IPv6"
    ("-6",)

    def _client(self, host: str) -> Client:
        family = 0
        if self.ipv4:
            family = _socket.AF_INET
        elif self.ipv6:
            family = _socket.AF_INET6
        plain = self.no_options
        return Client(
            host,
            self.port,
            timeout=self.timeout,
            retries=self.retries,
            blksize=None if plain or not self.blksize else self.blksize,
            windowsize=None if plain or not self.windowsize else self.windowsize,
            tsize=not (plain or self.no_tsize),
            timeout_option=not plain,
            family=family,
        )

    def _report(self, result: TransferResult) -> None:
        if self.json_out:
            print(_json.dumps(result_json(result), indent=2))
            return
        n = result.negotiated
        error(
            "%s %d bytes in %.3fs (%.1f KiB/s), blksize %d, windowsize %d, %d retransmits"
            % (
                "received" if result.operation == "read" else "sent",
                result.bytes,
                result.duration,
                result.throughput / 1024,
                n.blksize,
                n.windowsize,
                result.retransmits,
            )
        )


__all__ = ["AUTO", "Args", "Base", "ClientCmd", "error", "result_json"]
