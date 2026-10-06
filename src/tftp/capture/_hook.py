"""Calling a trace hook so that its failure costs one log record, not one per datagram."""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

__all__ = ["HookGuard", "guard"]


class HookGuard:
    """``hook`` called so that an exception it raises never reaches the transfer.

    The first exception is logged with its traceback, on ``logger``; later ones
    are counted in :attr:`failures` and not logged, so a hook that always fails
    (a full disk under a pcap writer) costs one record.
    """

    __slots__ = ("hook", "logger", "failures")

    def __init__(self, hook: Callable[..., Any], logger: logging.Logger) -> None:
        self.hook = hook
        self.logger = logger
        self.failures = 0

    def __call__(self, *args: Any) -> None:
        try:
            self.hook(*args)
        except Exception:
            self.failures += 1
            if self.failures == 1:
                self.logger.exception("trace hook failed (later failures of it are counted, not logged)")


def guard(
    hook: Optional[Callable[..., Any]], logger: logging.Logger, current: Optional[HookGuard] = None
) -> Optional[HookGuard]:
    """``current`` when it already wraps ``hook``, else a new guard (``None`` for no hook)."""
    if hook is None:
        return None
    if current is not None and current.hook is hook:
        return current
    return HookGuard(hook, logger)
