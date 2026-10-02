"""Counters for metrics: what a server or relay has done since it started."""

from __future__ import annotations

import threading
from typing import Dict

__all__ = ["Stats"]


class Stats:
    """Monotonic counters, safe to read from any thread.

    Server counters: ``requests`` (RRQ/WRQ seen on the listening port),
    ``refused`` (answered with an ERROR before a transfer started, or
    dropped by a limit), ``started``, ``completed``, ``failed``,
    ``bytes_sent``, ``bytes_received``, ``retransmits``. A relay counts
    ``requests``, ``refused``, ``started``, ``completed``, ``failed``,
    ``bytes_to_clients``, ``bytes_from_clients``. ``snapshot()`` returns them
    all, plus ``active`` from the owner.
    """

    def __init__(self, *names: str) -> None:
        self._lock = threading.Lock()
        self._values: Dict[str, int] = {name: 0 for name in names}

    def add(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._values[name] = self._values.get(name, 0) + amount

    def __getitem__(self, name: str) -> int:
        with self._lock:
            return self._values.get(name, 0)

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._values)

    def __repr__(self) -> str:
        return "Stats(%s)" % ", ".join("%s=%d" % item for item in self.snapshot().items())


SERVER_COUNTERS = (
    "requests",
    "refused",
    "started",
    "completed",
    "failed",
    "bytes_sent",
    "bytes_received",
    "retransmits",
)
RELAY_COUNTERS = (
    "requests",
    "refused",
    "started",
    "completed",
    "failed",
    "bytes_to_clients",
    "bytes_from_clients",
)
