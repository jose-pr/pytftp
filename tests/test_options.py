import pytest

import tftp
from tftp.options import accept_oack, negotiate, request_options

POLICY = tftp.TFTPServerOptions(max_blksize=8192, max_windowsize=16, allowed=tftp.SUPPORTED_OPTIONS)


def test_negotiate_accepts_and_clamps():
    result = negotiate(
        {"blksize": "65464", "windowsize": "100", "timeout": "3", "tsize": "0", "rollover": "1"},
        POLICY,
        is_read=True,
        timeout=1.0,
        size=12345,
    )
    assert result.options == {
        "blksize": "8192",
        "windowsize": "16",
        "timeout": "3",
        "tsize": "12345",
        "rollover": "1",
    }
    assert (result.blksize, result.windowsize, result.timeout, result.tsize, result.rollover) == (
        8192,
        16,
        3.0,
        12345,
        1,
    )


def test_negotiate_ignores_bad_and_unknown_values():
    result = negotiate(
        {"blksize": "7", "timeout": "0", "windowsize": "x", "rollover": "2", "multicast": "", "tsize": "-1"},
        POLICY,
        is_read=False,
        timeout=2.0,
    )
    assert result.options == {}
    assert (result.blksize, result.windowsize, result.timeout) == (512, 1, 2.0)


def test_negotiate_tsize_unknown_or_empty_is_not_acked():
    assert negotiate({"tsize": "0"}, POLICY, is_read=True, timeout=1, size=None).options == {}
    # curl refuses an OACK with tsize 0
    assert negotiate({"tsize": "0"}, POLICY, is_read=True, timeout=1, size=0).options == {}


def test_negotiate_write_tsize_echoes():
    result = negotiate({"tsize": "999"}, POLICY, is_read=False, timeout=1)
    assert result.tsize == 999 and result.options == {"tsize": "999"}


def test_negotiate_utimeout_and_allowed():
    result = negotiate({"utimeout": "250000"}, POLICY, is_read=True, timeout=1)
    assert result.timeout == 0.25
    assert negotiate({"utimeout": "250000"}, tftp.TFTPServerOptions(), is_read=True, timeout=1).options == {}
    narrow = tftp.TFTPServerOptions(allowed=frozenset({"blksize"}))
    assert negotiate({"tsize": "0", "blksize": "1024"}, narrow, is_read=True, timeout=1, size=5).options == {
        "blksize": "1024"
    }


def test_server_options_validation():
    with pytest.raises(ValueError):
        tftp.TFTPServerOptions(max_blksize=4)
    with pytest.raises(ValueError):
        tftp.TFTPServerOptions(max_windowsize=0)
    with pytest.raises(ValueError):
        tftp.TFTPServerOptions(allowed=frozenset({"multicast"}))


def test_request_options():
    assert request_options(blksize=1428, windowsize=8, timeout=2, tsize=0, rollover=0) == {
        "blksize": "1428",
        "windowsize": "8",
        "timeout": "2",
        "tsize": "0",
        "rollover": "0",
    }
    assert request_options(timeout=0.25) == {}  # utimeout is an extension: opt in
    assert request_options(timeout=0.25, utimeout=True) == {"utimeout": "250000"}
    assert request_options(timeout=300, utimeout=True) == {"utimeout": "255000000"}
    for bad in ({"blksize": 7}, {"windowsize": 0}, {"rollover": 2}, {"timeout": 0}, {"tsize": -1}):
        with pytest.raises(ValueError):
            request_options(**bad)


def test_accept_oack():
    requested = {"blksize": "1428", "windowsize": "8", "tsize": "0", "timeout": "1"}
    result = accept_oack(requested, {"blksize": "1024", "tsize": "77"}, is_read=True, timeout=1.0)
    assert (result.blksize, result.windowsize, result.tsize) == (1024, 1, 77)


@pytest.mark.parametrize(
    "oack",
    [
        {"blksize": "2000"},  # larger than requested
        {"windowsize": "9"},
        {"multicast": "1"},  # never requested
        {"blksize": "big"},
        {"timeout": "300"},
    ],
)
def test_accept_oack_refuses(oack):
    requested = {"blksize": "1428", "windowsize": "8", "timeout": "1"}
    with pytest.raises(tftp.TFTPProtocolError) as info:
        accept_oack(requested, oack, is_read=True, timeout=1.0)
    assert info.value.code == tftp.TFTPErrorCode.OPTION_REFUSED


# -- the policy classes: equality, repr, and profiles that cannot be changed through a reference --


@pytest.mark.parametrize("make", [tftp.TFTPServerLimits, tftp.TFTPServerOptions, tftp.options.Negotiated])
def test_a_policy_equals_an_identical_one_and_nothing_else(make):
    one, other = make(), make()
    assert one == other and one is not other
    assert one.__eq__(object()) is NotImplemented and one != object() and one != ()
    assert type(one).__hash__ is None  # mutable, so unhashable
    assert repr(one).startswith(type(one).__name__ + "(") and "object at 0x" not in repr(one)


def test_policies_that_differ_are_unequal():
    assert tftp.TFTPServerLimits(max_options=3) != tftp.TFTPServerLimits()
    assert tftp.TFTPServerLimits(max_idle=None) != tftp.TFTPServerLimits()
    assert tftp.TFTPServerOptions(max_blksize=512) != tftp.TFTPServerOptions()
    assert tftp.TFTPServerOptions(refused={"tsize"}) != tftp.TFTPServerOptions()
    assert tftp.options.Negotiated(blksize=1428) != tftp.options.Negotiated()
    assert tftp.options.Negotiated(options={"a": "1"}) != tftp.options.Negotiated()


def test_the_repr_of_a_limit_and_a_registry_names_what_they_hold():
    assert "max_options=3" in repr(tftp.TFTPServerLimits(max_options=3))
    registry = tftp.options.OptionRegistry()
    assert "blksize" in repr(registry) and "object at 0x" not in repr(registry)


def test_a_profiles_server_policy_cannot_be_changed_through_a_reference():
    before = tftp.Profile.PXE.server.max_blksize
    policy = tftp.Profile.PXE.server
    policy.max_blksize = 512  # the copy
    assert tftp.Profile.PXE.server.max_blksize == before != 512
    assert tftp.Profile.PXE.server == tftp.Profile.PXE.server
    assert tftp.Profile.PXE.server is not tftp.Profile.PXE.server
    assert tftp.Profile.PXE.client is not tftp.Profile.PXE.client
    built = tftp.TFTPServerOptions(max_blksize=1024)
    profile = tftp.options.Profile("custom", built, {})
    built.max_blksize = 512
    assert profile.server.max_blksize == 1024
