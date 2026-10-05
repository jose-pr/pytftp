"""Option registry, tftp-hpa extensions, profiles, MTU fitting, window bounds."""

from __future__ import annotations

import io

import pytest

import tftp
from conftest import client_for
from tftp.options import Blksize2, Cookie, accept_oack, negotiate, request_options

ALL = tftp.ServerOptions(allowed=tftp.SUPPORTED_OPTIONS)


def test_standard_options_are_the_default_policy():
    assert tftp.ServerOptions().allowed == {"blksize", "timeout", "tsize", "windowsize"}
    assert tftp.STANDARD_OPTIONS == {"blksize", "timeout", "tsize", "windowsize"}
    assert tftp.EXTENSION_OPTIONS == {
        "blksize2",
        "utimeout",
        "rollover",
        "cookie",
        "mstfwindow",
        "x-list",
        "x-mtime",
    }
    requested = {
        "blksize": "1024",
        "rollover": "1",
        "cookie": "abc",
        "utimeout": "500000",
        "blksize2": "3000",
        "mstfwindow": "31416",
    }
    assert set(negotiate(requested, tftp.ServerOptions(), is_read=False, timeout=1).options) == {"blksize"}


def test_mstfwindow_server_side():
    result = negotiate({"mstfwindow": "31416"}, ALL, is_read=True, timeout=1, size=10)
    assert result.options == {"mstfwindow": "27182"} and result.windowsize == 4
    assert result.extra["mstfwindow"] is True
    both = negotiate({"windowsize": "16", "mstfwindow": "31416"}, ALL, is_read=True, timeout=1)
    assert both.options == {"windowsize": "16", "mstfwindow": "27182"} and both.windowsize == 16
    small = tftp.ServerOptions(allowed={"mstfwindow"}, max_windowsize=2)
    assert negotiate({"mstfwindow": "31416"}, small, is_read=True, timeout=1).windowsize == 2
    assert negotiate({"mstfwindow": "1"}, ALL, is_read=True, timeout=1).options == {}
    assert "mstfwindow" not in tftp.HPA.server.allowed


def test_mstfwindow_client_side():
    ok = accept_oack({"mstfwindow": "31416"}, {"mstfwindow": "27182"}, is_read=True, timeout=1)
    assert ok.windowsize == 4 and ok.extra["mstfwindow"] is True
    asked = {"mstfwindow": "31416", "windowsize": "8"}
    both = accept_oack(asked, {"mstfwindow": "27182", "windowsize": "8"}, is_read=True, timeout=1)
    assert both.windowsize == 8
    with pytest.raises(tftp.TFTPProtocolError):
        accept_oack({"mstfwindow": "31416"}, {"mstfwindow": "31416"}, is_read=True, timeout=1)


def test_mstfwindow_end_to_end(root, make_server):
    server = make_server(root, options=tftp.ServerOptions(allowed=tftp.STANDARD_OPTIONS | {"mstfwindow"}))
    seen = []
    client = client_for(
        server, extra_options={"mstfwindow": 31416}, on_negotiated=lambda n, peer: seen.append(n)
    )
    assert client.get("big.bin") == (root / "big.bin").read_bytes()
    assert seen[0].windowsize == 4 and seen[0].options["mstfwindow"] == "27182"


def test_refused_overrides_allowed():
    policy = tftp.ServerOptions(refused={"windowsize"})
    result = negotiate({"windowsize": "8", "blksize": "1024"}, policy, is_read=False, timeout=1)
    assert result.options == {"blksize": "1024"} and result.windowsize == 1


@pytest.mark.parametrize(
    "asked,expected", [("3000", "2048"), ("4096", "4096"), ("65464", "32768"), ("8", "8")]
)
def test_blksize2_is_a_power_of_two(asked, expected):
    result = negotiate({"blksize2": asked}, ALL, is_read=True, timeout=1, size=10)
    assert result.options == {"blksize2": expected} and result.blksize == int(expected)


def test_blksize_wins_over_blksize2():
    result = negotiate({"blksize": "1000", "blksize2": "4096"}, ALL, is_read=True, timeout=1, size=10)
    assert result.options == {"blksize": "1000"} and result.blksize == 1000


def test_blksize2_client_validation():
    ok = accept_oack({"blksize2": "4096"}, {"blksize2": "2048"}, is_read=True, timeout=1)
    assert ok.blksize == 2048
    for bad in ("3000", "8192", "4"):
        with pytest.raises(tftp.TFTPProtocolError):
            accept_oack({"blksize2": "4096"}, {"blksize2": bad}, is_read=True, timeout=1)


def test_cookie_is_echoed_and_checked():
    result = negotiate({"cookie": "Opaque-1"}, ALL, is_read=True, timeout=1)
    assert result.options == {"cookie": "Opaque-1"} and result.extra["cookie"] == "Opaque-1"
    assert accept_oack({"cookie": "x"}, {"cookie": "x"}, is_read=True, timeout=1).extra == {"cookie": "x"}
    with pytest.raises(tftp.TFTPProtocolError):
        accept_oack({"cookie": "x"}, {"cookie": "y"}, is_read=True, timeout=1)


def test_window_bounded_by_bytes():
    policy = tftp.ServerOptions(max_windowsize=1000, max_window_bytes=64 * 1024)
    result = negotiate({"blksize": "8192", "windowsize": "1000"}, policy, is_read=True, timeout=1, size=1)
    assert result.windowsize == 8  # 8 x 8192 = 64 KiB
    tiny = negotiate({"blksize": "65464", "windowsize": "5"}, policy, is_read=True, timeout=1, size=1)
    assert tiny.windowsize == 1  # never below one block


@pytest.mark.parametrize("ipv6,expected", [(False, 1468), (True, 1448)])
def test_fit_mtu_lowers_blksize(ipv6, expected):
    policy = tftp.ServerOptions(fit_mtu=True)
    result = negotiate({"blksize": "65464"}, policy, is_read=True, timeout=1, size=1, mtu=1500, ipv6=ipv6)
    assert result.blksize == expected
    unknown = negotiate({"blksize": "65464"}, policy, is_read=True, timeout=1, size=1, mtu=None)
    assert unknown.blksize == 65464


def test_custom_option_through_a_registry(root, make_server):
    class Flavor(tftp.OptionHandler):
        """A vendor option: the server answers with what it will serve."""

        name = "flavor"

        def negotiate(self, value, ctx):
            choice = "vanilla" if value not in ("vanilla", "chocolate") else value
            ctx.result.extra[self.name] = choice
            return choice

    registry = tftp.OptionRegistry()
    registry.register(Flavor())
    with pytest.raises(ValueError):
        registry.register(Flavor())
    policy = tftp.ServerOptions(allowed=tftp.STANDARD_OPTIONS | {"flavor"}, registry=registry)
    with pytest.raises(ValueError):
        tftp.ServerOptions(allowed={"flavor"})  # not in the default registry
    server = make_server(root, options=policy)
    result = client_for(server, extra_options={"flavor": "strawberry"}).download("one.bin", io.BytesIO())
    assert result.negotiated.options["flavor"] == "vanilla"
    assert result.negotiated.extra["flavor"] == "vanilla"


def test_extra_options_cannot_duplicate_builtins():
    with pytest.raises(ValueError):
        request_options(blksize=512, extra={"BLKSIZE": 1024})


def test_registry_basics():
    registry = tftp.OptionRegistry()
    assert "blksize" in registry and "BLKSIZE" in registry and 3 not in registry
    assert [h.name for h in registry][:2] == ["blksize", "blksize2"]
    registry.unregister("cookie")
    assert "cookie" not in registry and "cookie" in tftp.DEFAULT_REGISTRY
    registry.register(Cookie())
    with pytest.raises(ValueError):
        registry.register(tftp.OptionHandler())
    assert isinstance(registry.copy().get("blksize2"), Blksize2)


@pytest.mark.parametrize("name", ["strict", "default", "pxe", "hpa", "legacy"])
def test_profiles_work_end_to_end(root, make_server, name):
    profile = tftp.PROFILES[name]
    server = make_server(root, options=profile.server)
    client = client_for(server, **profile.client)
    assert client.get("big.bin") == (root / "big.bin").read_bytes()


def test_legacy_profile_sends_a_plain_request(root, make_server):
    results = []
    server = make_server(root, on_complete=results.append)
    result = client_for(server, **tftp.LEGACY.client).download("513.bin", io.BytesIO())
    assert result.negotiated.options == {} and result.negotiated.blksize == 512


def test_legacy_server_refuses_windowsize(root, make_server):
    server = make_server(root, options=tftp.LEGACY.server)
    result = client_for(server, windowsize=8).download("big.bin", io.BytesIO())
    assert result.negotiated.windowsize == 1 and "windowsize" not in result.negotiated.options


def test_client_blksize_mtu(root, make_server):
    server = make_server(root)
    result = client_for(server, blksize="mtu").download("big.bin", io.BytesIO())
    assert 8 <= result.negotiated.blksize <= 65464
    with pytest.raises(ValueError):
        tftp.Client("127.0.0.1", blksize="huge")


def test_server_fit_mtu_end_to_end(root, make_server):
    """The arrival interface's MTU bounds the grant (MTU injected: loopback's is huge)."""
    server = make_server(root, options=tftp.ServerOptions(fit_mtu=True))
    server._mtu = lambda session: 1500
    result = client_for(server, blksize=65464).download("big.bin", io.BytesIO())
    assert result.negotiated.blksize == 1468
