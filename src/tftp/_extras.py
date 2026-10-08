"""The one place that knows pktcap is an optional dependency, and the line that names its extra."""

from __future__ import annotations

__all__ = ["PKTCAP_LINE", "have_pktcap", "require_pktcap"]

PKTCAP_LINE = "captures need the 'pktcap' extra: pip install \"tftp[pktcap]\""


def have_pktcap() -> bool:
    """Whether pktcap can be imported; any other failure of the import is let through."""
    try:
        import pktcap  # noqa: F401
    except ImportError as exc:
        if (exc.name or "").split(".")[0] != "pktcap":
            raise
        return False
    return True


def require_pktcap() -> None:
    """``ImportError`` with :data:`PKTCAP_LINE` when pktcap is not installed."""
    if not have_pktcap():
        raise ImportError(PKTCAP_LINE)
