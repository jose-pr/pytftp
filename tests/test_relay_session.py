"""One relayed transfer's state: when it ends, and what it reports.

``RelaySession`` reads no clock and owns no socket: time arrives as a number and
addresses are tuples, so these tests need neither.
"""

from __future__ import annotations

import pytest

from tftp import TFTPOpcode, TFTPRequestContext
from tftp.packet import encode_ack, encode_data, encode_error, encode_oack, encode_request
from tftp.packet._codec import RequestPacket, decode
from tftp.relay._session import RelaySession

CLIENT = ("192.0.2.9", 5000)
UPSTREAM_REQUEST = ("192.0.2.1", 69)
UPSTREAM_TID = ("192.0.2.1", 6000)
LINGER = 2.0
IDLE = 30.0
LIFETIME = 3600.0


def make(opcode=TFTPOpcode.RRQ, now=100.0):
    request = decode(encode_request(opcode, "boot.bin"))
    assert isinstance(request, RequestPacket)
    context = TFTPRequestContext(request, CLIENT)
    return RelaySession("r-1", CLIENT, ("192.0.2.9", 5000), UPSTREAM_REQUEST, request, context, now)


def test_a_new_transfer_expires_after_idle_and_after_lifetime():
    session = make(now=100.0)
    assert session.deadline(IDLE, LIFETIME) == 130.0
    assert session.expiry_reason(130.0, IDLE, LIFETIME) == "idle"
    session.observe(encode_ack(0), True, 120.0, LINGER)
    assert session.deadline(IDLE, LIFETIME) == 150.0
    assert session.deadline(IDLE, 40.0) == 140.0  # the lifetime comes first
    assert session.expiry_reason(140.0, IDLE, 40.0) == "lifetime"


def test_a_final_data_and_its_ack_close_after_the_linger_as_complete():
    session = make()
    session.upstream_tid = UPSTREAM_TID
    session.observe(encode_data(1, b"x" * 10), False, 101.0, LINGER)
    assert session.closing_at is None  # not acknowledged yet
    session.observe(encode_ack(1), True, 102.0, LINGER)
    assert session.closing_at == 102.0 + LINGER
    assert session.reason == "complete"
    assert session.deadline(IDLE, LIFETIME) == 102.0 + LINGER
    assert session.expiry_reason(102.0 + LINGER, IDLE, LIFETIME) == "complete"


def test_a_full_block_is_not_final_and_an_ack_of_another_block_does_not_close():
    session = make()
    session.observe(encode_data(1, b"x" * 512), False, 101.0, LINGER)
    session.observe(encode_ack(1), True, 102.0, LINGER)
    assert session.closing_at is None
    session.observe(encode_data(2, b"y"), False, 103.0, LINGER)
    session.observe(encode_ack(1), True, 104.0, LINGER)  # a late ack of block 1
    assert session.closing_at is None


@pytest.mark.parametrize("option", ["blksize", "blksize2"])
def test_an_oack_moves_what_a_final_block_is(option):
    session = make()
    session.observe(encode_oack({option: 1024}), False, 101.0, LINGER)
    session.observe(encode_data(1, b"x" * 600), False, 102.0, LINGER)  # short of 1024: final
    session.observe(encode_ack(1), True, 103.0, LINGER)
    assert session.closing_at == 103.0 + LINGER

    default = make()
    default.observe(encode_data(1, b"x" * 600), False, 102.0, LINGER)  # 600 is over 512: not final
    default.observe(encode_ack(1), True, 103.0, LINGER)
    assert default.closing_at is None


@pytest.mark.parametrize("from_client", [False, True], ids=["from_upstream", "from_client"])
@pytest.mark.parametrize("linger,expected", [(5.0, 1.0), (0.4, 0.4)])
def test_an_error_either_way_closes_within_a_second_or_the_linger(from_client, linger, expected):
    session = make()
    session.observe(encode_error(1, "file not found"), from_client, 110.0, linger)
    assert session.closing_at == pytest.approx(110.0 + expected)
    assert session.reason == "error" and session.error == (1, "file not found")
    assert session.expiry_reason(110.0 + expected, IDLE, LIFETIME) == "error"


def test_the_summary_reports_the_learned_tid_the_counters_and_the_duration():
    session = make(TFTPOpcode.WRQ, now=100.0)
    assert session.summary(101.0).upstream == UPSTREAM_REQUEST  # nothing learned yet
    session.upstream_tid = UPSTREAM_TID
    session.observe(encode_data(1, b"x" * 512), True, 101.0, LINGER)
    session.observe(encode_data(2, b"y" * 5), True, 102.0, LINGER)
    summary = session.summary(103.5)
    assert summary.upstream == UPSTREAM_TID and summary.client == CLIENT
    assert (summary.operation, summary.filename, summary.mode) == ("write", "boot.bin", "octet")
    assert (summary.bytes_from_client, summary.bytes_to_client, summary.packets) == (517, 0, 2)
    assert summary.duration == pytest.approx(3.5)
    assert summary.reason == "idle"  # nothing ended it


def test_a_malformed_datagram_is_counted_and_ignored():
    session = make()
    session.observe(b"\x00", False, 101.0, LINGER)
    session.observe(b"\x01\x03abcd", False, 102.0, LINGER)  # a nonzero first byte
    assert session.packets == 2 and session.closing_at is None
