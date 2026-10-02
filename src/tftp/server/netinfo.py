"""What the server needs to know about local interfaces, cached.

Enumeration is a syscall, so it is refreshed at most every
:data:`REFRESH_SECONDS` rather than per request.
"""

from __future__ import annotations

import ipaddress
import threading
import time
from typing import Dict, FrozenSet, Optional, Tuple

__all__ = ["InterfaceInfo", "REFRESH_SECONDS"]

REFRESH_SECONDS = 30.0


class InterfaceInfo:
    """Per-interface-index broadcast addresses and MTU, from netimps."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._at = -REFRESH_SECONDS
        self._broadcast: Dict[int, FrozenSet[str]] = {}
        self._all_broadcast: FrozenSet[str] = frozenset()
        self._mtu: Dict[int, Optional[int]] = {}

    def _refresh(self) -> None:
        now = time.monotonic()
        if now - self._at < REFRESH_SECONDS:
            return
        from netimps import get_interfaces

        broadcast: Dict[int, FrozenSet[str]] = {}
        mtu: Dict[int, Optional[int]] = {}
        for iface in get_interfaces():
            addresses = set()
            for address in iface.ipv4:
                network = address.network
                if network.prefixlen < 31:
                    addresses.add(str(network.broadcast_address))
            broadcast[iface.index] = frozenset(addresses)
            mtu[iface.index] = iface.mtu
        self._broadcast = broadcast
        self._all_broadcast = frozenset(a for group in broadcast.values() for a in group)
        self._mtu = mtu
        self._at = now

    def is_broadcast(self, local: str, ifindex: int = 0) -> bool:
        """``local`` (a request's destination) is a broadcast or multicast address."""
        bare = local.split("%", 1)[0]
        try:
            address = ipaddress.ip_address(bare)
        except ValueError:
            return False
        if address.version == 6 and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
            bare = str(address)
        if address.is_multicast or bare == "255.255.255.255":
            return True
        if address.version != 4:
            return False
        with self._lock:
            self._refresh()
            if ifindex:
                return bare in self._broadcast.get(ifindex, frozenset())
            return bare in self._all_broadcast

    def mtu(self, ifindex: int) -> Optional[int]:
        """The link MTU of interface ``ifindex``, or ``None`` when unknown."""
        if not ifindex:
            return None
        with self._lock:
            self._refresh()
            return self._mtu.get(ifindex)

    def snapshot(self) -> Tuple[Dict[int, FrozenSet[str]], Dict[int, Optional[int]]]:
        with self._lock:
            self._refresh()
            return dict(self._broadcast), dict(self._mtu)
