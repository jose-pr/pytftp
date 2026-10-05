"""``tftp://`` URLs (RFC 3617).

    tftp://host/path/file
    tftp://[2001:db8::1]:6969/file;mode=netascii

RFC 3617 gives ``tftpURI = "tftp://" host "/" file [ mode ]``, where ``file``
is percent-encoded text and ``mode`` is ``;mode=netascii`` or ``;mode=octet``.
:class:`TFTPURL` reads that, with two additions that are universal in
practice: an optional ``:port`` (default 69) and ``/`` inside the file name,
which names a path. There is no place in the grammar for a query, a fragment
or userinfo, so a URL carrying one is refused instead of read as another
file: a literal ``?`` or ``#`` in a file name is written ``%3F`` or ``%23``.

The file name is bytes on the wire, so it is decoded and encoded with the
codec's own encoding and error handler: any octet sequence survives
``str(TFTPURL.parse(text))``.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any, Optional, Tuple
from urllib.parse import quote, quote_from_bytes, unquote, unquote_to_bytes

from .exceptions import TFTPValueError
from .packet.codec import FILENAME_ENCODING, _ERRORS

__all__ = ["TFTPURL", "download_url", "upload_url"]

_MODES = ("octet", "netascii")
_HOST_FORBIDDEN = frozenset("/?#@[]\\")


def _has_control(text: str) -> bool:
    return any(ord(c) < 0x20 or ord(c) == 0x7F for c in text)


def _host_text(host: object) -> str:
    """The text of a host given as a string or as an address or host object."""
    if isinstance(host, str):
        return host
    if isinstance(host, (ipaddress.IPv4Interface, ipaddress.IPv6Interface)):
        return str(host.ip)
    if host is None or isinstance(host, (bool, int, float, bytes, bytearray, memoryview, tuple, list, dict)):
        raise TypeError("a URL host is text or an address object, not %s" % type(host).__name__)
    return str(host)


def _normal_host(host: object) -> str:
    text = _host_text(host)
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    if not text:
        raise TFTPValueError("a tftp:// URL needs a host")
    if _has_control(text) or any(c in _HOST_FORBIDDEN or c.isspace() for c in text):
        raise TFTPValueError("not a host: %r" % text)
    if ":" not in text:
        if "%" in text:
            raise TFTPValueError("not a host: %r" % text)
        return text.lower()
    address, percent, zone = text.partition("%")
    try:
        address = str(ipaddress.IPv6Address(address))
    except ValueError:
        raise TFTPValueError("not an IPv6 address: %r" % text) from None
    if percent and not zone:
        raise TFTPValueError("an empty IPv6 zone: %r" % text)
    return address + percent + zone


def _split_parameters(path: str) -> Tuple[str, Optional[str]]:
    """``(file, mode)`` from the part of a URL after the authority's ``/``.

    The first ``;`` starts the parameters; ``mode`` is the one parameter
    RFC 3617 defines. ``None`` when it is absent. A repeated, empty or unknown
    parameter raises :class:`TFTPValueError`.
    """
    file, semicolon, params = path.partition(";")
    if not semicolon:
        return file, None
    mode: Optional[str] = None
    for param in params.split(";"):
        key, equals, value = param.partition("=")
        if key.lower() != "mode" or not equals:
            raise TFTPValueError("unknown tftp:// parameter %r" % param)
        if mode is not None:
            raise TFTPValueError("the mode parameter is repeated")
        mode = value
    return file, mode


@dataclass(frozen=True)
class TFTPURL:
    """The ``tftp://`` URL of one file: ``host``, ``port``, ``filename``, ``mode``.

    Immutable and hashable. The constructor validates and normalises: the
    host is lower-cased (an IPv6 literal in its compressed form, its zone
    kept), the port is an ``int`` in 1..65535, the mode is ``"octet"`` or
    ``"netascii"`` and the file name is non-empty text without a NUL. A host
    given as an ``ipaddress`` address or interface or a ``netimps.Host`` is
    reduced to its text. ``str(url)`` is the URL, and ``TFTPURL.parse(str(url))
    == url``.

    :raises TypeError: an argument of the wrong type.
    :raises TFTPValueError: a value no URL can carry.
    """

    host: str
    port: int
    filename: str
    mode: str = "octet"

    def __post_init__(self) -> None:
        if isinstance(self.port, bool) or not isinstance(self.port, int):
            raise TypeError("a URL port is an int, not %s" % type(self.port).__name__)
        if not isinstance(self.filename, str):
            raise TypeError("a URL file name is text, not %s" % type(self.filename).__name__)
        if not isinstance(self.mode, str):
            raise TypeError("a URL mode is text, not %s" % type(self.mode).__name__)
        if not 1 <= self.port <= 65535:
            raise TFTPValueError("a URL port is 1 to 65535, not %d" % self.port)
        if not self.filename:
            raise TFTPValueError("a tftp:// URL needs a file name")
        if "\0" in self.filename:
            raise TFTPValueError("a file name cannot hold a NUL: %r" % self.filename)
        mode = self.mode.lower()
        if mode not in _MODES:
            raise TFTPValueError("unsupported mode %r: use octet or netascii" % self.mode)
        object.__setattr__(self, "host", _normal_host(self.host))
        object.__setattr__(self, "mode", mode)

    @classmethod
    def parse(cls, text: str) -> "TFTPURL":
        """The URL ``text`` spells; the inverse of ``str(url)``.

        :raises TypeError: ``text`` is not a ``str``.
        :raises TFTPValueError: not a ``tftp://`` URL, or one with a query, a
            fragment, userinfo, port 0, a repeated or unknown parameter or a
            mode other than ``octet`` and ``netascii``.
        """
        if not isinstance(text, str):
            raise TypeError("a URL is parsed from text, not %s" % type(text).__name__)
        if text[:7].lower() != "tftp://":
            raise TFTPValueError("not a tftp:// URL: %r" % text)
        if _has_control(text):
            raise TFTPValueError("a control character in the URL: %r" % text)
        rest = text[7:]
        if "?" in rest or "#" in rest:
            raise TFTPValueError(
                "a tftp:// URL has no query or fragment (write %%3F or %%23 in a file name): %r" % text
            )
        authority, slash, path = rest.partition("/")
        if not slash:
            raise TFTPValueError("tftp:// URL without a file name: %r" % text)
        if "@" in authority:
            raise TFTPValueError("a tftp:// URL has no userinfo: %r" % text)
        if authority.startswith("["):
            end = authority.find("]")
            if end < 0:
                raise TFTPValueError("unclosed '[' in %r" % text)
            host, tail = authority[1:end], authority[end + 1 :]
            if tail and not tail.startswith(":"):
                raise TFTPValueError("bad authority in %r" % text)
            port_text = tail[1:]
            if ":" not in host:
                raise TFTPValueError("brackets hold an IPv6 address only: %r" % text)
            address, percent, zone = host.partition("%")
            if percent:
                # RFC 6874 writes the zone delimiter as "%25"; a bare "%" is read too.
                zone = unquote(zone[2:] if zone.startswith("25") and len(zone) > 2 else zone)
                host = address + "%" + zone
        else:
            host, colon, port_text = authority.partition(":")
            if ":" in port_text:
                raise TFTPValueError("bad authority in %r (an IPv6 address needs brackets)" % text)
        if not host:
            raise TFTPValueError("tftp:// URL without a host: %r" % text)
        port = 69
        if port_text:
            if not (port_text.isascii() and port_text.isdigit()):
                raise TFTPValueError("bad port in %r" % text)
            port = int(port_text)
            if not 1 <= port <= 65535:
                raise TFTPValueError("bad port in %r" % text)
        quoted, mode = _split_parameters(path)
        filename = unquote_to_bytes(quoted).decode(FILENAME_ENCODING, _ERRORS)
        return cls(host, port, filename, "octet" if mode is None else mode)

    @classmethod
    def try_parse(cls, text: str, default: Optional["TFTPURL"] = None) -> Optional["TFTPURL"]:
        """:meth:`parse`, or ``default`` for text that is not a ``tftp://`` URL.

        Still raises :class:`TypeError` when ``text`` is not a ``str``.
        """
        try:
            return cls.parse(text)
        except TFTPValueError:
            return default

    def __str__(self) -> str:
        host = self.host
        if ":" in host:
            address, percent, zone = host.partition("%")
            host = "[%s%s]" % (address, "%25" + quote(zone, safe="") if percent else "")
        authority = host if self.port == 69 else "%s:%d" % (host, self.port)
        file = quote_from_bytes(self.filename.encode(FILENAME_ENCODING, _ERRORS), safe="/")
        return "tftp://%s/%s%s" % (authority, file, "" if self.mode == "octet" else ";mode=" + self.mode)


def download_url(url: str, dst: Any, /, *, progress: Optional[Any] = None, **client_options: Any):
    """Download the file a ``tftp://`` URL names; ``client_options`` go to ``TFTPClient``."""
    from .client import TFTPClient

    target = TFTPURL.parse(url)
    return TFTPClient(target.host, target.port, **client_options).download(
        target.filename, dst, mode=target.mode, progress=progress
    )


def upload_url(url: str, src: Any, /, *, progress: Optional[Any] = None, **client_options: Any):
    """Upload to the file a ``tftp://`` URL names; ``client_options`` go to ``TFTPClient``."""
    from .client import TFTPClient

    target = TFTPURL.parse(url)
    return TFTPClient(target.host, target.port, **client_options).upload(
        target.filename, src, mode=target.mode, progress=progress
    )
