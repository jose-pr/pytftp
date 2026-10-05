"""Which options exist: a registry of :class:`OptionHandler` instances."""

from __future__ import annotations

from typing import Dict, FrozenSet, Iterator, Optional

from .base import OptionHandler
from .builtin import BUILTIN_OPTIONS

__all__ = ["OptionRegistry", "DEFAULT_REGISTRY", "register_option"]


class OptionRegistry:
    """Option handlers by name, kept in negotiation order.

    Registration order is negotiation order, so an option whose answer depends
    on another (``windowsize`` on the block size) is registered after it.
    """

    def __init__(self, handlers=BUILTIN_OPTIONS) -> None:
        self._handlers: Dict[str, OptionHandler] = {}
        for handler in handlers:
            self.register(handler)

    def register(self, handler: OptionHandler, *, replace: bool = False) -> OptionHandler:
        """Add ``handler``; an existing name needs ``replace=True``."""
        name = handler.name.lower()
        if not name:
            raise ValueError("an option handler needs a name")
        if name in self._handlers and not replace:
            raise ValueError("option %r is already registered" % name)
        self._handlers[name] = handler
        return handler

    def unregister(self, name: str) -> None:
        self._handlers.pop(name.lower(), None)

    def get(self, name: str) -> Optional[OptionHandler]:
        return self._handlers.get(name.lower())

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name.lower() in self._handlers

    def __iter__(self) -> Iterator[OptionHandler]:
        return iter(list(self._handlers.values()))

    def names(self) -> FrozenSet[str]:
        return frozenset(self._handlers)

    def standard(self) -> FrozenSet[str]:
        """Names of the RFC-defined options (what a server allows by default)."""
        return frozenset(h.name for h in self._handlers.values() if h.standard)

    def __repr__(self) -> str:
        return "OptionRegistry(%s)" % ", ".join(self._handlers)

    def copy(self) -> "OptionRegistry":
        return OptionRegistry(list(self._handlers.values()))


#: The registry every server and client uses unless given another.
DEFAULT_REGISTRY = OptionRegistry()


def register_option(handler: OptionHandler, replace: bool = False) -> OptionHandler:
    """Add ``handler`` to :data:`DEFAULT_REGISTRY` (usable as a class decorator's result)."""
    return DEFAULT_REGISTRY.register(handler, replace=replace)
