"""Trace hooks: guarding one so that its failure costs one log record, and combining several."""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

__all__ = ["HookGuard", "combine_hooks", "guard"]


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


def combine_hooks(*hooks: Optional[Callable[[Any], Any]]) -> Optional[Callable[[Any], None]]:
    """One trace hook that calls each of ``hooks`` in order; ``None`` entries are skipped.

    Returns ``None`` when no hook is left, and the hook itself when one is. A
    hook that raises does not keep the others from seeing the event: the first
    exception is raised once all have been called, for the guard around the
    combined hook to count.
    """
    present = [hook for hook in hooks if hook is not None]
    if not present:
        return None
    if len(present) == 1:
        return present[0]

    def combined(event: Any) -> None:
        failure: Optional[Exception] = None
        for hook in present:
            try:
                hook(event)
            except Exception as exc:
                failure = failure or exc
        if failure is not None:
            raise failure

    return combined
