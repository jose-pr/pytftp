"""The flags that decide which options a server negotiates, and the policy they build."""

from __future__ import annotations

import typing as _ty

from duho import Choice

from ..options._policy import LISTING_OPTIONS, STANDARD_OPTIONS, TFTPServerOptions
from ..options._profiles import PROFILES
from ._common import PROFILE_NAMES, Base

__all__ = ["Negotiation"]


class Negotiation(Base):
    """What a server grants a client, and the :class:`TFTPServerOptions` for it."""

    compat: _ty.Annotated[_ty.Optional[str], Choice(*PROFILE_NAMES)] = None
    "Negotiate as this compatibility profile (replaces the option flags below)"
    ("--compat",)

    max_blksize: int = 65464
    "Largest blksize granted"
    ("--max-blksize",)

    max_windowsize: int = 64
    "Largest windowsize granted"
    ("--max-windowsize",)

    allow: _ty.List[str] = []
    "Also accept this extension option (blksize2, utimeout, rollover, cookie, mstfwindow); repeatable"
    ("--allow",)

    refuse: _ty.List[str] = []
    "Never acknowledge this option, e.g. windowsize for broken firmware; repeatable"
    ("--refuse",)

    listing: bool = False
    "Answer directory listings and modification times (pytftp's x-list/x-mtime, for 'pytftp ls')"
    ("--listing",)

    fit_mtu: bool = False
    "Lower blksize to fit the arrival interface's MTU (no IP fragments)"
    ("--fit-mtu",)

    def _options(self) -> TFTPServerOptions:
        listing = LISTING_OPTIONS if self.listing else frozenset()
        if self.compat:
            profile = PROFILES[self.compat].server
            return profile.replace(allowed=profile.allowed | listing) if listing else profile
        return TFTPServerOptions(
            max_blksize=self.max_blksize,
            max_windowsize=self.max_windowsize,
            allowed=STANDARD_OPTIONS | set(self.allow) | listing,
            refused=self.refuse,
            fit_mtu=self.fit_mtu,
        )
