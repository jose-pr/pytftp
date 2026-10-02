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
from ..options import PROFILES
from ..result import TransferResult

__all__ = [
    "AUTO",
    "Args",
    "Base",
    "Traced",
    "ClientCmd",
    "error",
    "result_json",
    "PROFILE_NAMES",
    "bind_failure",
]

#: ``--compat`` choices.
PROFILE_NAMES = tuple(PROFILES)


def error(text: str) -> None:
    print(text, file=_sys.stderr)


def port_range(text: _ty.Optional[str]) -> _ty.Any:
    """``LOW:HIGH`` (or ``LOW-HIGH``) for ``--port-range``: a ``PortRange``, or ``ValueError``."""
    if not text:
        return None
    from ..server import PortRange

    low, sep, high = text.replace("-", ":").partition(":")
    try:
        if not sep:
            raise ValueError
        return PortRange(int(low), int(high))
    except ValueError:
        raise ValueError("--port-range expects LOW:HIGH within 1..65535, got %r" % text) from None


def bind_failure(exc: OSError, address: str, port: int) -> int:
    """Report a listening socket that could not be bound; exit status 1."""
    hint = None
    try:
        from netimps import bind_error_hint

        hint = bind_error_hint(exc, port)
    except ImportError:  # pragma: no cover
        pass
    error("error: cannot listen on %s port %d: %s" % (address, port, hint or exc))
    return 1


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


class Traced(Base):
    """Commands that move packets can show them."""

    trace: bool = False
    "Print every datagram sent and received on stderr"
    ("--trace",)

    pcap: _ty.Optional[str] = None
    "Also write every datagram to this pcap file (opens in Wireshark)"
    ("--pcap",)

    def _tracer(self) -> _ty.Optional[_ty.Callable[[_ty.Any], None]]:
        hooks: _ty.List[_ty.Callable[[_ty.Any], None]] = []
        if self.trace:

            def show(event: _ty.Any) -> None:
                print(event.format(), file=_sys.stderr, flush=True)

            hooks.append(show)
        if self.pcap:
            from ..capture import PcapWriter

            self._writer = PcapWriter(self.pcap)
            hooks.append(self._writer)
        if not hooks:
            return None
        if len(hooks) == 1:
            return hooks[0]
        return lambda event: [hook(event) for hook in hooks] and None

    def _close_trace(self) -> None:
        writer = getattr(self, "_writer", None)
        if writer is not None:
            writer.close()


class ClientCmd(Traced):
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

    compat: _ty.Annotated[_ty.Optional[str], Choice(*PROFILE_NAMES)] = None
    "Use a compatibility profile's option settings (replaces the option flags)"
    ("--compat",)

    ipv4: bool = False
    "Use IPv4"
    ("-4",)

    ipv6: bool = False
    "Use IPv6"
    ("-6",)

    def _client(self, host: str, port: _ty.Optional[int] = None) -> Client:
        family = 0
        if self.ipv4:
            family = _socket.AF_INET
        elif self.ipv6:
            family = _socket.AF_INET6
        settings: _ty.Dict[str, _ty.Any] = {
            "timeout": self.timeout,
            "retries": self.retries,
            "family": family,
            "trace": self._tracer(),
        }
        if self.compat:
            settings.update(PROFILES[self.compat].client)
        else:
            plain = self.no_options
            settings.update(
                blksize=None if plain or not self.blksize else self.blksize,
                windowsize=None if plain or not self.windowsize else self.windowsize,
                tsize=not (plain or self.no_tsize),
                timeout_option=not plain,
            )
        return Client(host, self.port if port is None else port, **settings)

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
