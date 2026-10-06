"""``tftp://`` URLs (RFC 3617) with transfer options.

    tftp://host/path/file
    tftp://[2001:db8::1]:6969/file;mode=netascii
    tftp://host/file;blksize=1428;windowsize=16
    tftp://host/file?blksize=1428&windowsize=16

RFC 3617 gives ``tftpURI = "tftp://" host "/" file [ mode ]``, where ``file``
is percent-encoded text and ``mode`` is ``;mode=netascii`` or ``;mode=octet``.
:class:`TFTPURL` reads that, with three additions: an optional ``:port``
(default 69; ``0`` also means the default), ``/`` inside the file name, which
names a path, and transfer options.

Options are this library's extension, in either of two spellings. Whichever of
``?`` and ``;`` comes first after the file name decides how the rest is read:
after ``?``, ``name=value`` pairs separated by ``&``; after ``;``,
``name=value`` pairs separated by ``;``. ``mode`` is a name in both. The other
spelling's delimiter must be percent-encoded inside a value. Another tool
reads the text after ``?`` as part of the file name, and curl looks for
``;mode=`` only, so a URL with options is for this library's own readers.

``blksize``, ``windowsize``, ``timeout``, ``tsize`` and ``rollover`` are read
as the ``TFTPClient`` keyword of that name; any other name is a wire option
requested verbatim. Names are compared without case and stored lower-case.
``str(url)`` writes the ``;`` spelling, ``mode`` first and only when it is not
``octet``, then the options in name order, so a URL with a mode and no options
is exactly RFC 3617's form.

A fragment and userinfo have no place in the grammar and are refused, so a
literal ``#`` in a file name is written ``%23``.

The file name and the options are bytes on the wire, so they are decoded and
encoded with the codec's own encoding and error handler: any octet sequence
survives ``str(TFTPURL.parse(text))``.
"""

from __future__ import annotations

import ipaddress
import os  # the string annotations name os.PathLike, which get_type_hints resolves here
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple
from urllib.parse import quote, quote_from_bytes, unquote, unquote_to_bytes

from ._arguments import check_seconds
from ._result import TransferResult
from .client._core import ProgressFunction, SinkLike, SourceLike
from .client._sync import TFTPClient
from .exceptions import TFTPValueError
from .options._negotiate import request_options
from .packet._codec import FILENAME_ENCODING, _ERRORS

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


def _text(text: str) -> str:
    """``text`` if the codec can carry it and it holds no control character."""
    if _has_control(text):
        raise TFTPValueError("a control character in %r" % text)
    try:
        text.encode(FILENAME_ENCODING, _ERRORS)
    except UnicodeEncodeError:
        raise TFTPValueError("not carried by the %s codec: %r" % (FILENAME_ENCODING, text)) from None
    return text


def _decode(text: str) -> str:
    return unquote_to_bytes(text).decode(FILENAME_ENCODING, _ERRORS)


def _encode(text: str) -> str:
    return quote_from_bytes(text.encode(FILENAME_ENCODING, _ERRORS), safe="")


def _split_parameters(path: str) -> Tuple[str, Optional[str], Dict[str, str]]:
    """``(file, mode, options)`` from the part of a URL after the authority's ``/``.

    ``file`` is returned as written. The first ``?`` or ``;`` starts the
    parameters: after ``?`` they are ``name=value`` pairs split on ``&``, after
    ``;`` split on ``;``, each name and value percent-decoded. ``mode`` is
    ``None`` when absent; the option names are lower-case. A literal ``?`` or
    ``;`` of the other spelling, a repeated name, an empty pair or name and a
    pair with no ``=`` raise :class:`TFTPValueError`.
    """
    cuts = [i for i in (path.find("?"), path.find(";")) if i >= 0]
    if not cuts:
        return path, None, {}
    cut = min(cuts)
    delimiter = path[cut]
    text = path[cut + 1 :]
    separator = "&" if delimiter == "?" else ";"
    refused = "?;" if delimiter == "?" else "?"
    if any(c in text for c in refused):
        raise TFTPValueError(
            "a tftp:// URL list is separated by %r (after '?', pairs split on '&'; after ';', on ';'): "
            "write %s in a value as %s"
            % (separator, " or ".join(refused), " or ".join(quote(c) for c in refused))
        )
    mode: Optional[str] = None
    options: Dict[str, str] = {}
    seen = set()
    for pair in text.split(separator):
        name, equals, value = pair.partition("=")
        if not pair:
            raise TFTPValueError("an empty parameter in a tftp:// URL")
        if not equals:
            raise TFTPValueError("a tftp:// URL parameter needs name=value, not %r" % pair)
        name = _decode(name)
        if not name:
            raise TFTPValueError("a tftp:// URL parameter has no name: %r" % pair)
        key = name.lower()
        if key in seen:
            raise TFTPValueError("the %r parameter is repeated" % key)
        seen.add(key)
        if key == "mode":
            mode = _decode(value)
        else:
            options[key] = _decode(value)
    return path[:cut], mode, options


def _whole(name: str, text: str) -> int:
    if not (text.isascii() and text.isdigit()):
        raise TFTPValueError("option %s is a whole number in ASCII digits, not %r" % (name, text))
    return int(text)


def _refuse_out_of_range(name: str, text: str, **keyword: Any) -> None:
    try:
        request_options(**keyword)
    except ValueError as exc:
        raise TFTPValueError("option %s=%s: %s" % (name, text, exc)) from None


def _read_blksize(name: str, text: str) -> Any:
    if text.lower() == "mtu":
        return "mtu"
    value = _whole(name, text)
    _refuse_out_of_range(name, text, blksize=value)
    return value


def _read_windowsize(name: str, text: str) -> int:
    value = _whole(name, text)
    _refuse_out_of_range(name, text, windowsize=value)
    return value


def _read_rollover(name: str, text: str) -> int:
    value = _whole(name, text)
    _refuse_out_of_range(name, text, rollover=value)
    return value


_SECONDS = re.compile(r"[0-9]+(\.[0-9]+)?\Z", re.ASCII)


def _read_timeout(name: str, text: str) -> float:
    if not _SECONDS.match(text):
        raise TFTPValueError("option %s is a number of seconds in ASCII digits, not %r" % (name, text))
    try:
        return check_seconds("timeout", float(text))
    except ValueError as exc:
        raise TFTPValueError("option %s=%s: %s" % (name, text, exc)) from None


def _read_tsize(name: str, text: str) -> bool:
    flag = {"1": True, "true": True, "0": False, "false": False}.get(text.lower())
    if flag is None:
        raise TFTPValueError("option %s is 1, 0, true or false, not %r" % (name, text))
    return flag


#: The options read as the ``TFTPClient`` keyword of the same name; each reader
#: returns the keyword's value or raises :class:`TFTPValueError`.
_READERS: Dict[str, Callable[[str, str], Any]] = {
    "blksize": _read_blksize,
    "windowsize": _read_windowsize,
    "timeout": _read_timeout,
    "tsize": _read_tsize,
    "rollover": _read_rollover,
}


def _normal_options(options: Any) -> "Mapping[str, str]":
    """A read-only copy of ``options``: lower-case names, text values, each checked."""
    if options is None:
        return MappingProxyType({})
    if not isinstance(options, Mapping):
        raise TypeError("URL options are a mapping of name to text, not %s" % type(options).__name__)
    checked: Dict[str, str] = {}
    for name, value in options.items():
        if not isinstance(name, str):
            raise TypeError("a URL option name is text, not %s" % type(name).__name__)
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise TypeError(
                "the value of URL option %r is text or an int, not %s" % (name, type(value).__name__)
            )
        if not name:
            raise TFTPValueError("a URL option name cannot be empty")
        key = _text(name).lower()
        text = _text(str(value))
        if key == "mode":
            raise TFTPValueError("mode is the URL's mode, not an option")
        if key in checked:
            raise TFTPValueError("the URL option %r is repeated" % key)
        reader = _READERS.get(key)
        if reader is not None:
            reader(key, text)
        checked[key] = text
    return MappingProxyType(checked)


def _normal_mode(mode: str) -> str:
    """``mode`` lower-cased, if it is ``octet`` or ``netascii``."""
    if mode.lower() not in _MODES:
        raise TFTPValueError("unsupported mode %r: use octet or netascii" % mode)
    return mode.lower()


def _client_keywords(options: Mapping[str, str]) -> Dict[str, Any]:
    """The ``TFTPClient`` keywords a URL's ``options`` stand for.

    The known names become the keyword of the same name, with its type; every
    other option goes into ``extra_options``, verbatim.
    """
    keywords: Dict[str, Any] = {}
    extra: Dict[str, str] = {}
    for name, text in options.items():
        reader = _READERS.get(name)
        if reader is None:
            extra[name] = text
        else:
            keywords[name] = reader(name, text)
    if extra:
        keywords["extra_options"] = extra
    return keywords


def _client_keywords_over(options: Mapping[str, str], explicit: Mapping[str, Any]) -> Dict[str, Any]:
    """The keywords of a URL's ``options``, each replaced by ``explicit``'s keyword of the same name.

    ``extra_options`` merge name by name, ``explicit`` winning.
    """
    merged = _client_keywords(options)
    extra = dict(merged.pop("extra_options", {}))
    given = explicit.get("extra_options") or {}
    for name in given:
        extra.pop(str(name).lower(), None)
    extra.update(given)
    merged.update(explicit)
    if extra:
        merged["extra_options"] = extra
    elif "extra_options" in merged:
        del merged["extra_options"]
    return merged


@dataclass(frozen=True)
class TFTPURL:
    """The ``tftp://`` URL of one file: ``host``, ``port``, ``filename``, ``mode``, ``options``.

    Immutable and hashable. The constructor validates and normalises: the
    host is lower-cased (an IPv6 literal in its compressed form, its zone
    kept), the port is an ``int`` in 1..65535 (``0`` is the default, 69), the
    mode is ``"octet"`` or ``"netascii"`` and the file name is non-empty text
    without a NUL. ``options`` is a mapping of option name to text, kept as a
    read-only mapping with lower-case names: ``blksize``, ``windowsize``,
    ``timeout``, ``tsize`` and ``rollover`` must be readable as the
    ``TFTPClient`` keyword of that name, any other name is a wire option, and
    ``mode`` is not an option. A host given as an ``ipaddress`` address or
    interface or a ``netimps.Host`` is reduced to its text. ``str(url)`` is
    the URL, in the ``;`` spelling, and ``TFTPURL.parse(str(url)) == url``.

    :raises TypeError: an argument of the wrong type.
    :raises TFTPValueError: a value no URL can carry.
    """

    host: str
    port: int
    filename: str
    mode: str = "octet"
    options: "Mapping[str, str]" = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.port, bool) or not isinstance(self.port, int):
            raise TypeError("a URL port is an int, not %s" % type(self.port).__name__)
        if not isinstance(self.filename, str):
            raise TypeError("a URL file name is text, not %s" % type(self.filename).__name__)
        if not isinstance(self.mode, str):
            raise TypeError("a URL mode is text, not %s" % type(self.mode).__name__)
        if not 0 <= self.port <= 65535:
            raise TFTPValueError("a URL port is 0 to 65535, not %d" % self.port)
        if not self.filename:
            raise TFTPValueError("a tftp:// URL needs a file name")
        if "\0" in self.filename:
            raise TFTPValueError("a file name cannot hold a NUL: %r" % self.filename)
        mode = _normal_mode(self.mode)
        object.__setattr__(self, "host", _normal_host(self.host))
        object.__setattr__(self, "port", self.port or 69)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "options", _normal_options(self.options))

    def __repr__(self) -> str:
        options = ", options=%r" % dict(self.options) if self.options else ""
        return "TFTPURL(host=%r, port=%r, filename=%r, mode=%r%s)" % (
            self.host,
            self.port,
            self.filename,
            self.mode,
            options,
        )

    def __hash__(self) -> int:
        return hash((self.host, self.port, self.filename, self.mode, frozenset(self.options.items())))

    def __reduce__(self) -> Tuple[Any, ...]:
        return (type(self), (self.host, self.port, self.filename, self.mode, dict(self.options)))

    @classmethod
    def parse(cls, text: str) -> "TFTPURL":
        """The URL ``text`` spells; the inverse of ``str(url)``.

        :raises TypeError: ``text`` is not a ``str``.
        :raises TFTPValueError: not a ``tftp://`` URL, or one with a
            fragment, userinfo, both parameter delimiters unencoded, a
            repeated name, an empty or malformed pair, an option value its
            name cannot read, or a mode other than ``octet`` and ``netascii``.
        """
        if not isinstance(text, str):
            raise TypeError("a URL is parsed from text, not %s" % type(text).__name__)
        if text[:7].lower() != "tftp://":
            raise TFTPValueError("not a tftp:// URL: %r" % text)
        if _has_control(text):
            raise TFTPValueError("a control character in the URL: %r" % text)
        rest = text[7:]
        if "#" in rest:
            raise TFTPValueError(
                "a tftp:// URL has no fragment (write %%23 in a file name or value): %r" % text
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
            if not 0 <= port <= 65535:
                raise TFTPValueError("bad port in %r" % text)
        quoted, mode, options = _split_parameters(path)
        return cls(host, port, _decode(quoted), "octet" if mode is None else mode, options)

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
        parameters: List[str] = [] if self.mode == "octet" else ["mode=" + self.mode]
        parameters.extend(
            "%s=%s" % (_encode(name), _encode(self.options[name])) for name in sorted(self.options)
        )
        return "tftp://%s/%s%s" % (authority, file, "".join(";" + parameter for parameter in parameters))


def download_url(
    url: str, dst: SinkLike, /, *, progress: Optional[ProgressFunction] = None, **client_options: Any
) -> TransferResult:
    """Download the file a ``tftp://`` URL names; ``client_options`` go to ``TFTPClient``.

    The URL's options become ``TFTPClient`` keywords; a keyword in
    ``client_options`` wins over the URL's option of the same name, and
    ``extra_options`` merge name by name.
    """
    target = TFTPURL.parse(url)
    return TFTPClient(
        target.host, target.port, **_client_keywords_over(target.options, client_options)
    ).download(target.filename, dst, mode=target.mode, progress=progress)


def upload_url(
    url: str,
    src: SourceLike,
    /,
    *,
    progress: Optional[ProgressFunction] = None,
    **client_options: Any,
) -> TransferResult:
    """Upload to the file a ``tftp://`` URL names; ``client_options`` go to ``TFTPClient``.

    The URL's options and the precedence are as for :func:`download_url`.
    """
    target = TFTPURL.parse(url)
    return TFTPClient(
        target.host, target.port, **_client_keywords_over(target.options, client_options)
    ).upload(target.filename, src, mode=target.mode, progress=progress)
