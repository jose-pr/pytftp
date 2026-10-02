import pytest

import tftp
from tftp.options import accept_oack, negotiate, request_options

POLICY = tftp.ServerOptions(max_blksize=8192, max_windowsize=16, allowed=tftp.SUPPORTED_OPTIONS)


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
    assert negotiate({"utimeout": "250000"}, tftp.ServerOptions(), is_read=True, timeout=1).options == {}
    narrow = tftp.ServerOptions(allowed=frozenset({"blksize"}))
    assert negotiate({"tsize": "0", "blksize": "1024"}, narrow, is_read=True, timeout=1, size=5).options == {
        "blksize": "1024"
    }


def test_server_options_validation():
    with pytest.raises(ValueError):
        tftp.ServerOptions(max_blksize=4)
    with pytest.raises(ValueError):
        tftp.ServerOptions(max_windowsize=0)
    with pytest.raises(ValueError):
        tftp.ServerOptions(allowed=frozenset({"multicast"}))


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
    with pytest.raises(tftp.ProtocolError) as info:
        accept_oack(requested, oack, is_read=True, timeout=1.0)
    assert info.value.code == tftp.ErrorCode.OPTION_REFUSED
