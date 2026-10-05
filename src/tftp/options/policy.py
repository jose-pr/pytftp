"""What a server is willing to negotiate."""

from __future__ import annotations

from typing import FrozenSet, Iterable, Optional

from .base import MAX_BLKSIZE, MAX_WINDOWSIZE, MIN_BLKSIZE
from .registry import DEFAULT_REGISTRY, OptionRegistry

__all__ = [
    "TFTPServerOptions",
    "STANDARD_OPTIONS",
    "EXTENSION_OPTIONS",
    "LISTING_OPTIONS",
    "SUPPORTED_OPTIONS",
]

#: RFC 2348, 2349, 7440: what a server acknowledges by default.
STANDARD_OPTIONS: FrozenSet[str] = DEFAULT_REGISTRY.standard()
#: Extensions (tftp-hpa's, Microsoft's ``mstfwindow``, pytftp's ``x-list`` and
#: ``x-mtime``), acknowledged only when allowed explicitly.
EXTENSION_OPTIONS: FrozenSet[str] = frozenset(
    {"blksize2", "utimeout", "rollover", "cookie", "mstfwindow", "x-list", "x-mtime"}
)
#: pytftp's listing extensions: a server allowing these serves ``TFTPPath.iterdir()``.
LISTING_OPTIONS: FrozenSet[str] = frozenset({"x-list", "x-mtime"})
#: Everything built in.
SUPPORTED_OPTIONS: FrozenSet[str] = STANDARD_OPTIONS | EXTENSION_OPTIONS


class TFTPServerOptions:
    """A server's negotiation policy.

    :param max_blksize: largest ``blksize`` granted; a larger request is
        answered with this value, which RFC 2348 allows.
    :param max_windowsize: largest ``windowsize`` granted.
    :param max_window_bytes: ``windowsize`` is also lowered so that one
        window of the negotiated block size fits in this many bytes -- the
        memory a sender holds per transfer.
    :param allowed: option names acknowledged at all. Defaults to the RFC
        options (``blksize``, ``timeout``, ``tsize``, ``windowsize``); add
        :data:`EXTENSION_OPTIONS` names to accept extensions.
    :param refused: names never acknowledged even when allowed -- for
        firmware that asks for an option and then mishandles it.
    :param fit_mtu: lower ``blksize`` so a DATA packet fits the MTU of the
        interface the request arrived on (needs pktinfo). Boot ROMs often
        cannot reassemble IP fragments.
    :param registry: where option handlers come from; custom options are
        registered there.
    """

    __slots__ = (
        "max_blksize",
        "max_windowsize",
        "max_window_bytes",
        "allowed",
        "refused",
        "fit_mtu",
        "registry",
    )

    def __init__(
        self,
        max_blksize: int = MAX_BLKSIZE,
        max_windowsize: int = 64,
        max_window_bytes: int = 4 << 20,
        allowed: Optional[Iterable[str]] = None,
        refused: Iterable[str] = (),
        fit_mtu: bool = False,
        registry: Optional[OptionRegistry] = None,
    ) -> None:
        self.registry = registry or DEFAULT_REGISTRY
        if not MIN_BLKSIZE <= max_blksize <= MAX_BLKSIZE:
            raise ValueError("max_blksize must be in %d..%d" % (MIN_BLKSIZE, MAX_BLKSIZE))
        if not 1 <= max_windowsize <= MAX_WINDOWSIZE:
            raise ValueError("max_windowsize must be in 1..%d" % MAX_WINDOWSIZE)
        if max_window_bytes < MIN_BLKSIZE:
            raise ValueError("max_window_bytes is smaller than any block")
        allowed_set = (
            frozenset(n.lower() for n in allowed) if allowed is not None else self.registry.standard()
        )
        unknown = allowed_set - self.registry.names()
        if unknown:
            raise ValueError("unsupported options: %s" % ", ".join(sorted(unknown)))
        self.max_blksize = max_blksize
        self.max_windowsize = max_windowsize
        self.max_window_bytes = max_window_bytes
        self.allowed = allowed_set
        self.refused = frozenset(n.lower() for n in refused)
        self.fit_mtu = fit_mtu

    def accepts(self, name: str) -> bool:
        return name in self.allowed and name not in self.refused

    def __repr__(self) -> str:
        return "TFTPServerOptions(max_blksize=%d, max_windowsize=%d, allowed=%s%s%s)" % (
            self.max_blksize,
            self.max_windowsize,
            sorted(self.allowed),
            ", refused=%s" % sorted(self.refused) if self.refused else "",
            ", fit_mtu=True" if self.fit_mtu else "",
        )
