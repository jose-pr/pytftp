"""The library's loggers: one per role, named for the role and not for the module.

A logger name is what a caller filters on, so it must not move when a module
is split or renamed; the names are ``tftp.client``, ``tftp.server``,
``tftp.relay`` and ``tftp.backends``.
"""

from __future__ import annotations

import logging

__all__ = ["BACKENDS", "CLIENT", "RELAY", "SERVER"]

BACKENDS = logging.getLogger("tftp.backends")
CLIENT = logging.getLogger("tftp.client")
RELAY = logging.getLogger("tftp.relay")
SERVER = logging.getLogger("tftp.server")
