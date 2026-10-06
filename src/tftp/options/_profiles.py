"""Named compatibility profiles: one switch for a coherent set of behaviours.

Each profile carries a server policy (``.server``, a :class:`TFTPServerOptions`)
and client settings (``.client``, keyword arguments for ``TFTPClient``)::

    tftp.TFTPServer("/srv/tftp", options=tftp.Profile.PXE.server)
    tftp.TFTPClient("192.0.2.1", **tftp.Profile.LEGACY.client)

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

import copy
from typing import Any, ClassVar, Dict, Mapping

from ._policy import STANDARD_OPTIONS, TFTPServerOptions

__all__ = ["Profile", "PROFILES"]


class Profile:
    """A server policy and client settings that belong together."""

    __slots__ = ("name", "_server", "_client")

    STRICT: ClassVar["Profile"]
    DEFAULT: ClassVar["Profile"]
    PXE: ClassVar["Profile"]
    HPA: ClassVar["Profile"]
    LEGACY: ClassVar["Profile"]

    def __init__(self, name: str, server: TFTPServerOptions, client: Mapping[str, Any]) -> None:
        self.name = name
        self._server = copy.copy(server)
        self._client = dict(client)

    @property
    def server(self) -> TFTPServerOptions:
        """The server policy (a fresh copy each time, so changing it changes no profile)."""
        return copy.copy(self._server)

    @property
    def client(self) -> Dict[str, Any]:
        """Keyword arguments for ``TFTPClient`` (a fresh dict each time)."""
        return dict(self._client)

    def __repr__(self) -> str:
        return "Profile(%r)" % self.name


Profile.STRICT = Profile("strict", TFTPServerOptions(), {"fallback": False, "strict_source": True})
Profile.DEFAULT = Profile("default", TFTPServerOptions(), {})
Profile.PXE = Profile(
    "pxe",
    TFTPServerOptions(allowed=STANDARD_OPTIONS | {"rollover", "utimeout"}, fit_mtu=True),
    {"blksize": 1428},
)
#: tftp-hpa's own extensions.
_HPA_EXTENSIONS = frozenset({"blksize2", "utimeout", "rollover", "cookie"})
Profile.HPA = Profile(
    "hpa", TFTPServerOptions(allowed=STANDARD_OPTIONS | _HPA_EXTENSIONS), {"utimeout": True}
)
Profile.LEGACY = Profile(
    "legacy",
    TFTPServerOptions(allowed=STANDARD_OPTIONS, refused={"windowsize"}),
    {
        "blksize": None,
        "windowsize": None,
        "tsize": False,
        "timeout_option": False,
        "fallback": True,
        "strict_source": False,
    },
)

PROFILES: Dict[str, Profile] = {
    p.name: p for p in (Profile.STRICT, Profile.DEFAULT, Profile.PXE, Profile.HPA, Profile.LEGACY)
}
