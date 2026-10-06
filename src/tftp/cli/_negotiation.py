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
    "Negotiate as this compatibility profile; not with the option flags below except --listing. Default: none"
    ("--compat",)

    max_blksize: _ty.Optional[int] = None
    "Largest blksize granted. Default: 65464"
    ("--max-blksize",)

    max_windowsize: _ty.Optional[int] = None
    "Largest windowsize granted. Default: 64"
    ("--max-windowsize",)

    allow: _ty.List[str] = []
    "Also accept this extension option (blksize2, utimeout, rollover, cookie, mstfwindow); repeatable. Default: the four standard options"
    ("--allow",)

    refuse: _ty.List[str] = []
    "Never acknowledge this option, e.g. windowsize for broken firmware; repeatable. Default: none"
    ("--refuse",)

    listing: bool = False
    "Answer directory listings and modification times (pytftp's x-list/x-mtime, for 'pytftp ls'). Default: off"
    ("--listing",)

    fit_mtu: bool = False
    "Lower blksize to fit the arrival interface's MTU (no IP fragments). Default: off"
    ("--fit-mtu",)

    def _options(self) -> TFTPServerOptions:
        listing = LISTING_OPTIONS if self.listing else frozenset()
        if self.compat:
            given = [
                name
                for name, value in (
                    ("--max-blksize", self.max_blksize is not None),
                    ("--max-windowsize", self.max_windowsize is not None),
                    ("--allow", bool(self.allow)),
                    ("--refuse", bool(self.refuse)),
                    ("--fit-mtu", self.fit_mtu),
                )
                if value
            ]
            if given:
                raise ValueError("--compat replaces the option flags: drop %s" % ", ".join(given))
            profile = PROFILES[self.compat].server
            return profile.replace(allowed=profile.allowed | listing) if listing else profile
        return TFTPServerOptions(
            max_blksize=65464 if self.max_blksize is None else self.max_blksize,
            max_windowsize=64 if self.max_windowsize is None else self.max_windowsize,
            allowed=STANDARD_OPTIONS | set(self.allow) | listing,
            refused=self.refuse,
            fit_mtu=self.fit_mtu,
        )
