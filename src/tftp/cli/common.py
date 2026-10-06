"""What every command shares: duho imports (or stand-ins), base options, output."""

from __future__ import annotations

import contextlib as _contextlib
import errno as _errno
import json as _json
import signal as _signal
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


from .._text import escape
from ..client import TFTPClient
from ..exceptions import TFTPError
from ..options import PROFILES
from .._result import TransferResult
from .._uri import TFTPURL, _client_keywords

__all__ = [
    "AUTO",
    "Args",
    "Base",
    "Traced",
    "ClientCmd",
    "error",
    "write_line",
    "result_json",
    "PROFILE_NAMES",
    "bind_failure",
    "shutdown_on_signal",
]

#: ``--compat`` choices.
PROFILE_NAMES = tuple(PROFILES)


@_contextlib.contextmanager
def shutdown_on_signal(server: _ty.Any) -> _ty.Iterator[None]:
    """Stop ``server`` (``TFTPServer`` or ``TFTPRelay``) on Ctrl-C, Ctrl-Break and SIGTERM.

    A signal handler only runs when the main thread executes bytecode, and a
    loop blocked in ``select`` does not, on Windows even for Ctrl-C. The
    signal also writes a byte to the server's wake socket, which wakes the
    wait at once, so an idle server costs no polling. Nothing is installed
    outside the main thread. Handlers and the wake descriptor are restored on
    exit.
    """
    import threading

    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def stop(signum: int, frame: _ty.Any) -> None:
        server.shutdown()

    previous = {}
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        number = getattr(_signal, name, None)
        if number is not None:
            try:
                previous[number] = _signal.signal(number, stop)
            except (ValueError, OSError):
                pass
    try:
        # The server's own wake socket: non-blocking, and drained by its loop.
        wakeup = _signal.set_wakeup_fd(server._wake_w.fileno(), warn_on_full_buffer=False)
    except (ValueError, OSError):
        wakeup = None
    try:
        yield
    finally:
        if wakeup is not None:
            _signal.set_wakeup_fd(wakeup)
        for number, handler in previous.items():
            _signal.signal(number, handler)


def error(text: str) -> None:
    """One diagnostic line on stderr; text a peer chose cannot carry a control character."""
    print(escape(text), file=_sys.stderr)


class _StdoutClosed(Exception):
    """The reader of stdout went away: the command has nothing left to say."""


def write_line(text: str) -> None:
    """One result line on stdout, flushed; :class:`_StdoutClosed` if nobody reads it any more.

    A closed pipe is ``BrokenPipeError`` on POSIX and ``OSError(EINVAL)`` on Windows.
    """
    try:
        print(text, flush=True)
    except OSError as exc:
        if isinstance(exc, BrokenPipeError) or exc.errno in (_errno.EPIPE, _errno.EINVAL):
            raise _StdoutClosed from None
        raise


def port_range(text: _ty.Optional[str]) -> _ty.Any:
    """``LOW:HIGH`` (or ``LOW-HIGH``) for ``--port-range``: a ``PortRange``, or ``ValueError``."""
    if not text:
        return None
    from ..server import PortRange

    try:
        return PortRange.parse(text)
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
        "ok": result.is_ok,
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
        """The hook for ``--trace`` and ``--pcap``, or ``None``.

        The pcap file is created here, so a command calls this last: after its
        arguments are accepted and its sockets bound, never before.
        """
        hooks: _ty.List[_ty.Callable[[_ty.Any], None]] = []
        if self.trace:

            def show(event: _ty.Any) -> None:
                print(str(event), file=_sys.stderr, flush=True)

            hooks.append(show)
        if self.pcap:
            from ..capture import PcapWriter

            self._writer = PcapWriter(self.pcap)
            hooks.append(self._writer)
        if not hooks:
            return None
        if len(hooks) == 1:
            return hooks[0]

        def combined(event: _ty.Any) -> None:
            for hook in hooks:
                hook(event)

        return combined

    def _close_trace(self) -> None:
        writer = getattr(self, "_writer", None)
        if writer is not None:
            writer.close()


class ClientCmd(Traced):
    """Options every client command takes."""

    port: int = 69
    "Server port"
    ("--port", "-p")

    mode: _ty.Annotated[_ty.Optional[str], Choice("octet", "netascii")] = None
    "Transfer mode (default: the URL's, else octet)"
    ("--mode", "-m")

    blksize: _ty.Optional[int] = None
    "Block size to request, 8-65464; 0 requests none (512). Default: the URL's, else 1428"
    ("--blksize", "-b")

    windowsize: _ty.Optional[int] = None
    "RFC 7440 window to request; 0 requests none (1). Default: the URL's, else none"
    ("--windowsize", "-w")

    timeout: _ty.Optional[float] = None
    "Seconds before retransmitting. Default: the URL's, else 1.0"
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

    def _client(
        self, host: str, port: _ty.Optional[int] = None, url: _ty.Optional[TFTPURL] = None
    ) -> TFTPClient:
        """A client for ``host``; ``url``'s options apply under any flag given."""
        family = 0
        if self.ipv4:
            family = _socket.AF_INET
        elif self.ipv6:
            family = _socket.AF_INET6
        settings: _ty.Dict[str, _ty.Any] = {"retries": self.retries, "family": family}
        if url is not None:
            settings.update(_client_keywords(url.options))
        if self.timeout is not None:
            settings["timeout"] = self.timeout
        if self.compat:
            settings.update(PROFILES[self.compat].client)
        else:
            if self.blksize is not None:
                settings["blksize"] = self.blksize or None
            if self.windowsize is not None:
                settings["windowsize"] = self.windowsize or None
            if self.no_tsize:
                settings["tsize"] = False
            if self.no_options:
                settings.update(blksize=None, windowsize=None, tsize=False, timeout_option=False)
                settings.pop("extra_options", None)
        return TFTPClient(host, self.port if port is None else port, **settings)

    def _transfer(self, client: TFTPClient, work: _ty.Callable[[], _ty.Any]) -> "_ty.Tuple[_ty.Any, int]":
        """Run ``work`` (a transfer by ``client``): ``(result, 0)``, or ``(None, 1)`` after one ``error:`` line.

        The trace file is opened here, once the client exists, and closed on the
        way out. A failure of the transfer or of the local side (a file, a name that
        does not resolve) is the operation failing; a usage error (``ValueError``)
        is left to :func:`tftp.cli.run`.
        """
        try:
            client.trace = self._tracer()
            return work(), 0
        except (TFTPError, OSError) as exc:
            error("error: %s" % exc)
            return None, 1
        finally:
            self._close_trace()

    def _report(self, result: TransferResult) -> None:
        if self.json_out:
            write_line(_json.dumps(result_json(result), indent=2))
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
