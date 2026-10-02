"""Named compatibility profiles: one switch for a coherent set of behaviours.

Each profile carries a server policy (``.server``, a :class:`ServerOptions`)
and client settings (``.client``, keyword arguments for ``Client``)::

    tftp.Server("/srv/tftp", options=tftp.PXE.server)
    tftp.Client("192.0.2.1", **tftp.LEGACY.client)

========  ===================================================================
profile   for
========  ===================================================================
strict    RFC behaviour only: no extensions, no retry without options
default   RFC options, plus the interoperability retries (the library default)
pxe       boot ROMs: blksize fitted to the interface MTU (no IP fragments),
          tftp-hpa's ``rollover``/``utimeout`` accepted
hpa       tftp-hpa compatible: every extension accepted and requested where
          it applies
legacy    old or quirky peers: plain RFC 1350 requests, answers accepted
          from any server address, ``windowsize`` refused
========  ===================================================================
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

from .policy import EXTENSION_OPTIONS, STANDARD_OPTIONS, ServerOptions

__all__ = ["Profile", "STRICT", "DEFAULT", "PXE", "HPA", "LEGACY", "PROFILES"]


class Profile:
    """A server policy and client settings that belong together."""

    __slots__ = ("name", "server", "_client")

    def __init__(self, name: str, server: ServerOptions, client: Mapping[str, Any]) -> None:
        self.name = name
        self.server = server
        self._client = dict(client)

    @property
    def client(self) -> Dict[str, Any]:
        """Keyword arguments for ``Client`` (a fresh dict each time)."""
        return dict(self._client)

    def __repr__(self) -> str:
        return "Profile(%r)" % self.name


STRICT = Profile("strict", ServerOptions(), {"fallback": False, "strict_source": True})
DEFAULT = Profile("default", ServerOptions(), {})
PXE = Profile(
    "pxe",
    ServerOptions(allowed=STANDARD_OPTIONS | {"rollover", "utimeout"}, fit_mtu=True),
    {"blksize": 1428},
)
HPA = Profile("hpa", ServerOptions(allowed=STANDARD_OPTIONS | EXTENSION_OPTIONS), {"utimeout": True})
LEGACY = Profile(
    "legacy",
    ServerOptions(allowed=STANDARD_OPTIONS, refused={"windowsize"}),
    {
        "blksize": None,
        "windowsize": None,
        "tsize": False,
        "timeout_option": False,
        "fallback": True,
        "strict_source": False,
    },
)

PROFILES: Dict[str, Profile] = {p.name: p for p in (STRICT, DEFAULT, PXE, HPA, LEGACY)}
