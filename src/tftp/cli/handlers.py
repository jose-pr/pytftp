"""Handler wrappers behind ``pytftp serve``'s deployment flags.

Each is a few lines on top of the public :class:`Handler` protocol --
deliberately not library API (``docs/serving.md`` shows the same pattern for
applications that want it):

- :class:`Remap` -- rewrite filenames with regular expressions (``--remap``).
- :class:`PerClient` -- serve ``root/<client address>/`` when it exists
  (``--per-client``).
- :class:`CaseInsensitive` -- find files whatever the case of the requested
  name (``--ignore-case``).
"""

from __future__ import annotations

import os
import re
import typing as _ty

from ..errors import TftpError
from ..packet import ErrorCode
from ..server import FileSystemHandler, RequestContext

__all__ = ["Remap", "PerClient", "CaseInsensitive", "parse_rule"]


def parse_rule(text: str) -> _ty.Tuple["re.Pattern[str]", str]:
    """``REGEX=REPLACEMENT`` (split at the first ``=``) for ``--remap``."""
    pattern, sep, replacement = text.partition("=")
    if not sep or not pattern:
        raise ValueError("--remap expects REGEX=REPLACEMENT, got %r" % text)
    try:
        return re.compile(pattern), replacement
    except re.error as exc:
        raise ValueError("--remap %r: %s" % (text, exc)) from None


class Remap:
    """Rewrites each filename with the first rule that matches it.

    Rules are ``(compiled regex, replacement)`` in ``re.sub`` syntax; only
    the first rule whose pattern is found applies, and it replaces every
    match in the name. The wrapped handler sees the rewritten name.
    """

    def __init__(self, inner: _ty.Any, rules: _ty.Sequence[_ty.Tuple["re.Pattern[str]", str]]) -> None:
        self.inner = inner
        self.rules = list(rules)
        self._tftp_fast_open_ = getattr(inner, "_tftp_fast_open_", False)

    def rewrite(self, filename: str) -> str:
        for pattern, replacement in self.rules:
            if pattern.search(filename):
                return pattern.sub(replacement, filename)
        return filename

    def _context(self, context: RequestContext) -> RequestContext:
        name = self.rewrite(context.filename)
        return context if name == context.filename else context.with_filename(name)

    def open_read(self, context: RequestContext) -> _ty.Any:
        return self.inner.open_read(self._context(context))

    def open_write(self, context: RequestContext, size: _ty.Optional[int]) -> _ty.Any:
        return self.inner.open_write(self._context(context), size)


def client_directory(peer: _ty.Tuple[_ty.Any, ...]) -> str:
    """The directory name for a client: its address, ``:`` written ``-``.

    ``-`` keeps IPv6 names valid on Windows; an IPv4 client seen through a
    dual-stack socket is named by its IPv4 address.
    """
    from netimps import split_zone, unmap

    return str(unmap(split_zone(str(peer[0]))[0])).replace(":", "-")


class PerClient:
    """Serves ``root/<client address>/`` to a client when that directory exists.

    Other clients get ``root`` itself, or ERROR 1 with ``fallback=False``.
    ``make(directory)`` builds the handler for a directory (a
    :class:`FileSystemHandler` with the server's write policy).
    """

    _tftp_fast_open_ = True

    def __init__(self, root: str, make: _ty.Callable[[str], _ty.Any], fallback: bool = True) -> None:
        self.root = os.path.realpath(root)
        self.make = make
        self.fallback = fallback
        self._handlers: _ty.Dict[str, _ty.Any] = {}

    def handler_for(self, context: RequestContext) -> _ty.Any:
        directory = os.path.join(self.root, client_directory(context.peer))
        if not os.path.isdir(directory):
            if not self.fallback:
                raise TftpError(ErrorCode.FILE_NOT_FOUND)
            directory = self.root
        handler = self._handlers.get(directory)
        if handler is None:
            handler = self._handlers[directory] = self.make(directory)
        return handler

    def open_read(self, context: RequestContext) -> _ty.Any:
        return self.handler_for(context).open_read(context)

    def open_write(self, context: RequestContext, size: _ty.Optional[int]) -> _ty.Any:
        return self.handler_for(context).open_write(context, size)


class CaseInsensitive(FileSystemHandler):
    """A :class:`FileSystemHandler` that matches names regardless of case.

    The exact name wins when it exists; otherwise each component is looked
    up case-insensitively (firmware asking for ``\\Boot\\BCD`` finds
    ``boot/bcd``). An upload's final component keeps the requested case.
    The corrected name goes through the normal containment checks.
    """

    def resolve(self, filename: str) -> str:
        path = super().resolve(filename)  # refuses .. and escapes first
        if os.path.exists(path):
            return path
        name = filename.replace("\\", "/") if self.backslash else filename
        parts = [part for part in name.split("/") if part not in ("", ".")]
        current, fixed = self.root, []
        for index, part in enumerate(parts):
            try:
                entries = os.listdir(current)
            except OSError:
                return path
            folded = part.casefold()
            match = part if part in entries else None
            if match is None:
                match = next((entry for entry in entries if entry.casefold() == folded), None)
            if match is None:
                if index < len(parts) - 1:
                    return path
                match = part
            fixed.append(match)
            current = os.path.join(current, match)
        return super().resolve("/".join(fixed))
