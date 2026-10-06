"""Live capture without tcpdump, on Linux (``AF_PACKET``; needs ``CAP_NET_RAW``).

Elsewhere, pipe a capture tool into :func:`tftp.capture.read_datagrams`::

    tcpdump -i eth0 -U -w - udp | pytftp capture -
    dumpcap -i Ethernet -w - -f udp | pytftp capture -      (Windows, Wireshark)
"""

from __future__ import annotations

import socket
import sys
import time
from typing import Callable, Iterator, Optional

from .frames import FrameDecoder, UDPDatagram

__all__ = ["sniff", "live_capture_supported"]

_ETH_P_ALL = 0x0003
_PACKET_OUTGOING = 4
_ARPHRD_NONE = 0xFFFE  # tun and other IP-only interfaces


def live_capture_supported() -> bool:
    return hasattr(socket, "AF_PACKET")


def sniff(
    interface: Optional[str] = None, *, stop: Optional[Callable[[], object]] = None
) -> Iterator[UDPDatagram]:
    """UDP datagrams seen on ``interface`` (all interfaces when ``None``).

    ``stop()``, when given, is checked between datagrams (and at least once a
    second); returning true ends the iteration.
    """
    if sys.platform != "linux" or not live_capture_supported():
        raise OSError("live capture needs Linux AF_PACKET; pipe tcpdump/dumpcap output instead")
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(_ETH_P_ALL))
    try:
        if interface:
            sock.bind((interface, 0))
        sock.settimeout(1.0)
        ethernet = FrameDecoder(1)
        raw = FrameDecoder(101)
        while stop is None or not stop():
            try:
                frame, address = sock.recvfrom(65535)
            except socket.timeout:
                continue
            ifname, _, pkttype, hatype = address[:4]
            if pkttype == _PACKET_OUTGOING and ifname == "lo":
                continue  # loopback shows every packet twice
            decoder = raw if hatype == _ARPHRD_NONE else ethernet
            yield from decoder.decode(time.time(), frame)
    finally:
        sock.close()
