"""Every public annotation resolves with ``typing.get_type_hints``, except the ones that
name a netimps or pktcap type (or the optional ``pathlib_next``'s path class).

``netimps`` and ``pktcap`` are imported lazily, so the names ``HostLike``, ``IPAddressLike``,
``IPNetworkLike``, ``Interface`` and the module ``pktcap`` exist only under ``TYPE_CHECKING``; a
signature that names one raises ``NameError`` from ``get_type_hints`` on every Python. The list below is the whole of that exception, with the
name each one cannot resolve: a new signature that names a netimps type, or any other annotation
that does not resolve (``"X | None"`` in a string fails on 3.9), changes the set and fails the test.
"""

import importlib
import inspect
import re
import typing

import pytest

from surface import EXPECTED

pytest.importorskip("pathlib_next")

#: Public callable -> the one name its annotations cannot resolve at run time: a netimps type, the
#: pktcap module, or ``TFTPPath``, which needs the optional ``pathlib_next``.
LAZILY_IMPORTED = {
    "tftp.TFTPClient": "HostLike",
    "tftp.TFTPClient.path": "TFTPPath",
    "tftp.AsyncTFTPClient": "HostLike",
    "tftp.download": "HostLike",
    "tftp.upload": "HostLike",
    "tftp.TFTPServer": "HostLike",
    "tftp.AsyncTFTPServer": "HostLike",
    "tftp.backends.UpstreamBackend": "HostLike",
    "tftp.relay.TFTPRelay": "HostLike",
    "tftp.relay.AsyncTFTPRelay": "HostLike",
    "tftp.relay.Upstream": "HostLike",
    "tftp.relay.Upstream.parse": "HostLike",
    "tftp.relay.RouteTable": "HostLike",
    "tftp.relay.by_subnet": "IPNetworkLike",
    "tftp.relay.by_prefix": "HostLike",
    "tftp.relay.by_interface": "Interface",
    "tftp.capture.dissect_tftp": "pktcap",
    "tftp.capture.register_tftp_dissector": "pktcap",
    "tftp.capture.pktcap_plugin": "pktcap",
    "tftp.capture.follow_transfers": "pktcap",
}


def _unresolved():
    seen = set()
    failures = {}

    def check(qual, fn):
        try:
            typing.get_type_hints(fn)
        except NameError as exc:
            match = re.search(r"name '(\w+)' is not defined", str(exc))
            failures[qual] = match.group(1) if match else str(exc)
        except Exception as exc:  # noqa: BLE001 - the failure is the data
            failures[qual] = "%s: %s" % (type(exc).__name__, exc)

    for modname in sorted(EXPECTED):
        mod = importlib.import_module(modname)
        for name in mod.__all__:
            obj = getattr(mod, name)
            if id(obj) in seen or not (inspect.isclass(obj) or inspect.isroutine(obj)):
                continue
            seen.add(id(obj))
            qual = "%s.%s" % (modname, name)
            if not inspect.isclass(obj):
                check(qual, obj)
                continue
            init = getattr(obj, "__init__", None)
            if inspect.isfunction(init) and init.__module__.startswith("tftp"):
                check(qual, init)
            own = vars(obj).get("__annotations__")
            if own:
                # Only this class's own annotations: a base class from another package is not ours.
                alone = type(obj.__name__, (), {"__annotations__": own, "__module__": obj.__module__})
                check(qual + " (class)", alone)
            for mname, member in vars(obj).items():
                if mname.startswith("_"):
                    continue
                raw = member.__func__ if isinstance(member, (staticmethod, classmethod)) else member
                if isinstance(member, property):
                    raw = member.fget
                if inspect.isfunction(raw):
                    check("%s.%s" % (qual, mname), raw)
    return failures


def test_public_annotations_resolve_except_the_lazily_imported_ones():
    failures = {k.replace(" (class)", ""): v for k, v in _unresolved().items()}
    assert failures == LAZILY_IMPORTED
