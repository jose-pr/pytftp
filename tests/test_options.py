import pytest

import tftp
from tftp.options import accept_oack, negotiate, request_options

POLICY = tftp.TFTPServerOptions(max_blksize=8192, max_windowsize=16, allowed=tftp.options.SUPPORTED_OPTIONS)


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


@pytest.mark.parametrize(
    "text,value", [("8", 8), ("0", 0), ("007", 7), ("65464", 65464), ("0" * 40 + "5", 5)]
)
def test_option_numbers_are_ascii_digits(text, value):
    from tftp.options.base import read_decimal

    assert read_decimal(text) == value


@pytest.mark.parametrize(
    "text",
    [
        "",
        " ",
        " 8",
        "8 ",
        " 1024\t",
        "+8",
        "-8",
        "8.0",
        "1_0",
        "0x10",
        "8k",
        "٨",
        "²",
        "1 2",
        "9" * 20,
        "9" * 5000,
        None,
        8,
    ],
)
def test_anything_else_is_not_an_option_number(text):
    from tftp.options.base import read_decimal

    assert read_decimal(text) is None


def test_a_signed_or_non_ascii_blksize_is_not_negotiated():
    for text in ("+1428", "١٤٢٨", "1_428"):
        assert negotiate({"blksize": text}, POLICY, is_read=True, timeout=1.0, size=10).options == {}


@pytest.mark.parametrize("text", [" 1024\t", "+1024", "1_024", "١٠٢٤"])
def test_a_value_that_is_not_ascii_digits_is_not_acknowledged(text):
    """RFC 2348: "The blksize ... specified in ASCII"; whitespace is not a digit."""
    assert negotiate({"blksize": text}, POLICY, is_read=True, timeout=1.0, size=10).options == {}


def test_a_timeout_is_acknowledged_from_the_number_that_was_read():
    """RFC 2349: the OACK carries the value as a decimal number, here 5."""
    assert negotiate({"timeout": "005"}, POLICY, is_read=True, timeout=1.0).options == {"timeout": "5"}
    for text in (" 5", "5 ", "+5", "٥", "0_5"):
        assert negotiate({"timeout": text}, POLICY, is_read=True, timeout=1.0).options == {}


def test_a_timeout_beside_a_different_utimeout_is_not_acknowledged():
    """tftp-hpa acknowledges timeout beside utimeout only when both name the same time."""
    differ = negotiate({"timeout": "5", "utimeout": "250000"}, POLICY, is_read=True, timeout=1.0)
    assert differ.options == {"utimeout": "250000"} and differ.timeout == 0.25
    agree = negotiate({"timeout": "5", "utimeout": "5000000"}, POLICY, is_read=True, timeout=1.0)
    assert agree.options == {"timeout": "5", "utimeout": "5000000"} and agree.timeout == 5.0
    unusable = negotiate({"timeout": "5", "utimeout": "7"}, POLICY, is_read=True, timeout=1.0)
    assert unusable.options == {"timeout": "5"} and unusable.timeout == 5.0
    refused = tftp.TFTPServerOptions(allowed={"timeout", "utimeout"}, refused={"utimeout"})
    only = negotiate({"timeout": "5", "utimeout": "250000"}, refused, is_read=True, timeout=1.0)
    assert only.options == {"timeout": "5"}
    standard = negotiate(
        {"timeout": "5", "utimeout": "250000"}, tftp.TFTPServerOptions(), is_read=True, timeout=1.0
    )
    assert standard.options == {"timeout": "5"}


def test_mstfwindow_is_bounded_in_bytes_like_windowsize():
    policy = tftp.TFTPServerOptions(
        allowed={"blksize", "windowsize", "mstfwindow"}, max_window_bytes=65464, max_windowsize=64
    )
    plain = negotiate({"blksize": "65464", "windowsize": "4"}, policy, is_read=True, timeout=1.0)
    ms = negotiate({"blksize": "65464", "mstfwindow": "31416"}, policy, is_read=True, timeout=1.0)
    assert plain.windowsize == ms.windowsize == 1
    assert (
        negotiate({"blksize": "512", "mstfwindow": "31416"}, policy, is_read=True, timeout=1.0).windowsize
        == 4
    )


def test_a_client_refuses_a_timeout_it_did_not_ask_for():
    """RFC 2349: "The specified timeout value must match the value specified by the client"."""
    for oack in ({"timeout": "255"}, {"timeout": "2"}, {"timeout": "0"}, {"timeout": "٣"}):
        with pytest.raises(tftp.TFTPProtocolError) as info:
            accept_oack({"timeout": "3"}, oack, is_read=True, timeout=3.0)
        assert info.value.code == tftp.TFTPErrorCode.OPTION_REFUSED
    assert accept_oack({"timeout": "3"}, {"timeout": "3"}, is_read=True, timeout=1.0).timeout == 3.0


def test_a_client_refuses_a_utimeout_it_did_not_ask_for():
    for answered in ("1", "1" + "0" * 30, "500001", "0"):
        with pytest.raises(tftp.TFTPProtocolError) as info:
            accept_oack({"utimeout": "500000"}, {"utimeout": answered}, is_read=True, timeout=1.0)
        assert info.value.code == tftp.TFTPErrorCode.OPTION_REFUSED
    answered = accept_oack({"utimeout": "500000"}, {"utimeout": "500000"}, is_read=True, timeout=1.0)
    assert answered.timeout == 0.5


def test_a_client_refuses_a_value_that_is_not_ascii_digits():
    for text in ("+512", "5_12", " 512", "٥١٢"):
        with pytest.raises(tftp.TFTPProtocolError):
            accept_oack({"blksize": "1024"}, {"blksize": text}, is_read=True, timeout=1.0)
