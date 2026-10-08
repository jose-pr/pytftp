"""What the client commands (``get``, ``put``, ``ls``) share: the options and one transfer."""

from __future__ import annotations

import json as _json
import socket as _socket
import typing as _ty

from duho import Choice, Meta

from .._result import TransferResult
from .._uri import TFTPURL, _client_keywords
from ..client._sync import TFTPClient
from ..exceptions import TFTPError
from ..options._profiles import PROFILES
from ._common import PROFILE_NAMES, Traced, error, write_line

__all__ = ["ClientCmd", "basename", "is_url"]


def is_url(text: str) -> bool:
    return text.lower().startswith("tftp://")


def basename(remote: str) -> str:
    return remote.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or "download"


class ClientCmd(Traced):
    """Options every client command takes."""

    port: int = 69
    "UDP port of the server"
    ("--port", "-p")

    mode: _ty.Annotated[_ty.Optional[str], Choice("octet", "netascii")] = None
    "Transfer mode. Default: the URL's, else octet"
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
    "Retransmissions of an unanswered packet before giving up"
    ("--retries", "-r")

    no_tsize: bool = False
    "Do not request or announce the transfer size. Default: it is"
    ("--no-tsize",)

    no_options: bool = False
    "Send a plain RFC 1350 request with no options at all. Default: the options above"
    ("--no-options",)

    compat: _ty.Annotated[_ty.Optional[str], Choice(*PROFILE_NAMES)] = None
    "Use this compatibility profile's option settings; not with -b, -w, --no-tsize or --no-options. Default: none"
    ("--compat",)

    ipv4: _ty.Annotated[bool, Meta(conflicts="family")] = False
    "Use IPv4 only. Default: either, as the name resolves"
    ("-4",)

    ipv6: _ty.Annotated[bool, Meta(conflicts="family")] = False
    "Use IPv6 only. Default: either, as the name resolves"
    ("-6",)

    def _check(self) -> None:
        """Refuse what the command would otherwise ignore (``ValueError``: usage error, status 2)."""
        if not self.compat:
            return
        given = [
            name
            for name, value in (
                ("-b/--blksize", self.blksize is not None),
                ("-w/--windowsize", self.windowsize is not None),
                ("--no-tsize", self.no_tsize),
                ("--no-options", self.no_options),
            )
            if value
        ]
        if given:
            raise ValueError("--compat replaces the option flags: drop %s" % ", ".join(given))

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
        is left to :func:`tftp.cli.main`.
        """
        if self._pcap_missing():
            return None, 1
        try:
            client.trace = self._tracer()
            return work(), 0
        except (TFTPError, OSError) as exc:
            error("error: %s" % exc)
            return None, 1
        finally:
            self._close_trace()

    def _report(self, result: TransferResult, *, stdout_is_data: bool = False) -> None:
        """The result: one JSON line, or one text line (on stderr when stdout carries the file)."""
        if self.json_out:
            write_line(_json.dumps(result.to_dict()))
            return
        n = result.negotiated
        self._say(
            "%s %d bytes in %.3fs (%.1f KiB/s), blksize %d, windowsize %d, %d retransmits"
            % (
                "received" if result.operation == "read" else "sent",
                result.bytes,
                result.duration,
                result.throughput / 1024,
                n.blksize,
                n.windowsize,
                result.retransmits,
            ),
            to_stderr=stdout_is_data,
        )
