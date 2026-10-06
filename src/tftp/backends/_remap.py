"""A handler that rewrites the filenames a request names before another handler sees them."""

from __future__ import annotations

import re
from typing import Iterable, Optional, Pattern, Tuple, Union

from ..exceptions import TFTPValueError
from ..server._handler import TFTPChunkReader, TFTPHandler, TFTPReader, TFTPRequestContext, TFTPWriter

__all__ = ["Remap"]

#: What a rule is: ``"REGEX=REPLACEMENT"`` text, or a ``(pattern, replacement)`` pair.
_RuleLike = Union[str, Tuple[Union[str, "Pattern[str]"], str]]


def _rule(rule: _RuleLike) -> Tuple["Pattern[str]", str]:
    pattern: Union[str, "Pattern[str]"]
    if isinstance(rule, str):
        pattern, sep, replacement = rule.partition("=")
        if not sep or not pattern:
            raise TFTPValueError("a rule is REGEX=REPLACEMENT, not %r" % rule)
    elif isinstance(rule, tuple) and len(rule) == 2 and isinstance(rule[1], str):
        pattern, replacement = rule
    else:
        raise TypeError("a rule is REGEX=REPLACEMENT text or a (pattern, replacement) pair, not %r" % (rule,))
    try:
        return (re.compile(pattern), replacement)
    except re.error as exc:
        raise TFTPValueError("rule %r: %s" % (pattern, exc)) from None
    except TypeError:
        raise TypeError("a rule's pattern is text or a compiled pattern, not %r" % (pattern,)) from None


class Remap:
    """Rewrites each requested filename with the first rule that matches it, then asks ``inner``.

    Only the first rule whose pattern is found in the name applies, and it
    replaces every match in it (:meth:`re.Pattern.sub`, so ``\\1`` names a
    group). ``inner`` sees the rewritten name and decides what it may reach: a
    rewrite cannot take a request out of the directory a
    :class:`FilesystemBackend` serves. Everything else about the request
    (the listing flag, the arrival interface) is carried over.

    :param inner: the handler that serves the rewritten names.
    :param rules: ``"REGEX=REPLACEMENT"`` text (split at the first ``=``) or
        ``(pattern, replacement)`` pairs, tried in order.
    :raises TFTPValueError: a rule without ``=`` or a regular expression that
        does not compile.
    :raises TypeError: a rule of another shape.
    """

    def __init__(self, inner: TFTPHandler, rules: Iterable[_RuleLike]) -> None:
        self.inner = inner
        self.rules: Tuple[Tuple["Pattern[str]", str], ...] = tuple(_rule(rule) for rule in rules)
        self.opens_fast = getattr(inner, "opens_fast", False)

    def rewrite(self, filename: str) -> str:
        """``filename`` as the first matching rule rewrites it, or unchanged."""
        for pattern, replacement in self.rules:
            if pattern.search(filename):
                return pattern.sub(replacement, filename)
        return filename

    def _context(self, context: TFTPRequestContext) -> TFTPRequestContext:
        name = self.rewrite(context.filename)
        return context if name == context.filename else context.with_filename(name)

    def open_read(self, context: TFTPRequestContext) -> Union[TFTPReader, TFTPChunkReader]:
        return self.inner.open_read(self._context(context))

    def open_write(self, context: TFTPRequestContext, size: Optional[int]) -> TFTPWriter:
        return self.inner.open_write(self._context(context), size)

    def __repr__(self) -> str:
        return "Remap(%r, %s)" % (self.inner, ["%s=%s" % (p.pattern, r) for p, r in self.rules])
