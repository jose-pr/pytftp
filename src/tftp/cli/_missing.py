"""The two commands that need pktcap, as they are listed when it is not installed."""

from __future__ import annotations

import typing as _ty

from .._extras import PKTCAP_LINE
from ._common import Base, error

__all__ = ["MissingCapture", "MissingReplay"]


class _Missing(Base):
    def __call__(self) -> _ty.Optional[int]:
        error(PKTCAP_LINE)
        return 1


class MissingCapture(_Missing):
    """Decode a capture (needs the pktcap extra: pip install "tftp[pktcap]")."""

    _parsername_ = "capture"


class MissingReplay(_Missing):
    """Ask a server again for a capture's transfers (needs the pktcap extra: pip install "tftp[pktcap]")."""

    _parsername_ = "replay"
