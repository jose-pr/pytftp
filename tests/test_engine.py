"""The transfer engine over a simulated link: loss, duplication, rollover.

No sockets and no real time: packets sit in two queues, a rule decides
which ones are lost or duplicated, and a virtual clock jumps to the next
deadline whenever both queues drain.
"""

from __future__ import annotations

import io
import os
import random
import struct
from typing import Callable, List, Optional

import pytest

from tftp import Negotiated, Receiver, Sender, encode_ack, encode_oack
from tftp.transfer import as_readinto, as_write

DATA, ACK, ERROR, OACK = 3, 4, 5, 6


class Link:
    """Two queues between a sender and a receiver, with a loss rule.

    ``rule(direction, packet, count)`` returns how many copies to deliver
    (0 = lost, 2 = duplicated); ``count`` numbers packets per direction.
    """

    def __init__(self, rule: Optional[Callable[[str, bytes, int], int]] = None) -> None:
        self.rule = rule or (lambda *_: 1)
        self.to_receiver: List[bytes] = []
        self.to_sender: List[bytes] = []
        self.sent = {"s": [], "r": []}

    def from_sender(self, packet) -> None:
        packet = bytes(packet)
        self.sent["s"].append(packet)
        for _ in range(self.rule("s", packet, len(self.sent["s"]) - 1)):
            self.to_receiver.append(packet)

    def from_receiver(self, packet) -> None:
        packet = bytes(packet)
        self.sent["r"].append(packet)
        for _ in range(self.rule("r", packet, len(self.sent["r"]) - 1)):
            self.to_sender.append(packet)


def run(
    data: bytes,
    sender_neg: Negotiated,
    receiver_neg: Optional[Negotiated] = None,
    rule=None,
    handshake: bool = False,
    retries: int = 5,
    max_steps: int = 5_000_000,
):
    """Transfer ``data`` across a :class:`Link`; returns (received, sender, receiver, link)."""
    receiver_neg = receiver_neg or sender_neg
    link = Link(rule)
    sink = io.BytesIO()
    now = 0.0
    if handshake:
        # A server answering an RRQ with options: OACK, then ACK 0, then DATA.
        sender = Sender(
            link.from_sender,
            as_readinto(io.BytesIO(data)),
            sender_neg,
            retries,
            now,
            oack=encode_oack({"x": 1}),
        )
        receiver = Receiver(
            link.from_receiver, as_write(sink), receiver_neg, retries, now, reply=encode_ack(0)
        )
    else:
        receiver = Receiver(link.from_receiver, as_write(sink), receiver_neg, retries, now)
        sender = Sender(link.from_sender, as_readinto(io.BytesIO(data)), sender_neg, retries, now)
    steps = 0
    while not (sender.done and receiver.done) and steps < max_steps:
        steps += 1
        if link.to_receiver:
            packet = link.to_receiver.pop(0)
            if packet[1] == OACK:
                continue  # the receiver side already answered with ACK 0
            receiver.handle(memoryview(packet), len(packet), now)
        elif link.to_sender:
            packet = link.to_sender.pop(0)
            sender.handle(memoryview(packet), len(packet), now)
        else:
            deadlines = [t.deadline for t in (sender, receiver) if not t.done and t.deadline is not None]
            if not deadlines:
                break
            now = min(deadlines)
            for side in (sender, receiver):
                if not side.done and side.deadline is not None and side.deadline <= now:
                    side.on_timeout(now)
    return sink.getvalue(), sender, receiver, link


def neg(**kwargs) -> Negotiated:
    kwargs.setdefault("timeout", 1.0)
    return Negotiated(**kwargs)


def blocks_of(link: Link, kind: int = DATA) -> List[int]:
    return [struct.unpack("!H", p[2:4])[0] for p in link.sent["s"] if p[1] == kind]


@pytest.mark.parametrize("size", [0, 1, 7, 8, 9, 512, 4096, 10_000])
@pytest.mark.parametrize("blksize,windowsize", [(8, 1), (512, 1), (8, 4), (512, 8), (100, 3)])
def test_clean_link(size, blksize, windowsize):
    data = os.urandom(size)
    got, sender, receiver, link = run(data, neg(blksize=blksize, windowsize=windowsize))
    assert got == data
    assert sender.error is None and receiver.error is None
    assert sender.retransmits == 0
    assert receiver.bytes == size == sender.bytes
    # A final short (possibly empty) block always ends the stream.
    assert receiver.blocks == size // blksize + 1


@pytest.mark.parametrize("windowsize", [1, 2, 5, 16])
@pytest.mark.parametrize("seed", range(6))
def test_random_loss_and_duplication(windowsize, seed):
    rng = random.Random(seed)

    def rule(direction, packet, count):
        roll = rng.random()
        if roll < 0.2:
            return 0
        if roll < 0.3:
            return 2
        return 1

    data = os.urandom(20_000)
    got, sender, receiver, _ = run(data, neg(blksize=64, windowsize=windowsize), rule=rule, retries=50)
    assert sender.error is None and receiver.error is None
    assert got == data


def test_sorcerers_apprentice_does_not_double_traffic():
    """Every ACK delivered twice must not make the sender send DATA twice."""
    data = os.urandom(64 * 200)

    def rule(direction, packet, count):
        return 2 if direction == "r" else 1

    got, sender, _, link = run(data, neg(blksize=64), rule=rule)
    assert got == data
    sent = blocks_of(link)
    assert len(sent) == len(set(sent)) == 201
    assert sender.retransmits == 0


def test_delayed_first_data_triggers_one_retransmit_only():
    """Classic SAS trigger: DATA 1 is late, so ACK 1 arrives twice."""
    data = os.urandom(64 * 50)

    def rule(direction, packet, count):
        # Drop the first copy of DATA 1; the sender's timeout resends it.
        return 0 if direction == "s" and count == 0 else 1

    got, sender, _, link = run(data, neg(blksize=64), rule=rule)
    assert got == data
    assert sender.retransmits == 1
    assert len(blocks_of(link)) == 51 + 1


def test_window_gap_resends_from_the_hole():
    data = os.urandom(10 * 100)

    def rule(direction, packet, count):
        # Lose DATA 3 the first time only.
        return 0 if direction == "s" and packet[1] == DATA and packet[3] == 3 and count < 5 else 1

    got, sender, receiver, link = run(data, neg(blksize=100, windowsize=5), rule=rule)
    assert got == data
    acks = [struct.unpack("!H", p[2:4])[0] for p in link.sent["r"]]
    assert acks[0] == 2  # the receiver reported the hole immediately
    # Recovery came from the ACK, not from a timeout.
    assert sender.retransmits <= 5


def test_lost_final_ack_retries_then_dally_answers():
    data = os.urandom(150)
    seen = {"final": 0}

    def rule(direction, packet, count):
        if direction == "r" and packet[1] == ACK and packet[3] == 2:
            seen["final"] += 1
            return 0 if seen["final"] == 1 else 1
        return 1

    got, sender, receiver, _ = run(data, neg(blksize=100), rule=rule)
    assert got == data
    assert sender.error is None and receiver.error is None
    assert seen["final"] == 2  # re-sent while dallying


def test_handshake_with_lost_oack_and_ack0():
    data = os.urandom(1000)
    dropped = {"ack0": 0}

    def rule(direction, packet, count):
        if direction == "r" and packet == encode_ack(0) and dropped["ack0"] < 2:
            dropped["ack0"] += 1
            return 0
        return 1

    got, sender, receiver, _ = run(data, neg(blksize=100), rule=rule, handshake=True)
    assert got == data and dropped["ack0"] == 2


def test_gives_up_after_retries():
    def rule(direction, packet, count):
        return 0 if direction == "r" else 1

    _, sender, receiver, _ = run(os.urandom(1000), neg(blksize=100), rule=rule, retries=3)
    assert sender.error is not None
    assert "retries" in sender.error.message


def test_receiver_rejects_oversized_data():
    link = Link()
    receiver = Receiver(link.from_receiver, as_write(io.BytesIO()), neg(blksize=8), 3, 0.0)
    packet = b"\x00\x03\x00\x01" + b"x" * 9
    receiver.handle(memoryview(packet), len(packet), 0.0)
    assert receiver.error is not None and receiver.error.code == 4
    assert link.sent["r"][-1][:4] == b"\x00\x05\x00\x04"


def test_error_packet_ends_transfer_without_reply():
    link = Link()
    receiver = Receiver(link.from_receiver, as_write(io.BytesIO()), neg(), 3, 0.0)
    packet = b"\x00\x05\x00\x03disk full\x00"
    receiver.handle(memoryview(packet), len(packet), 0.0)
    assert receiver.done and receiver.error.code == 3 and receiver.error.message == "disk full"
    assert link.sent["r"] == []


def test_source_failure_is_reported_to_peer():
    class Broken(io.RawIOBase):
        def readinto(self, b):
            raise PermissionError(13, "denied")

    link = Link()
    sender = Sender(link.from_sender, as_readinto(Broken()), neg(), 3, 0.0)
    assert sender.done and sender.error.code == 2
    assert link.sent["s"] == [b"\x00\x05\x00\x02access violation\x00"]


def test_writers_never_keep_the_receive_buffer():
    kept = []

    class Keeper:
        def write(self, data):
            kept.append(data)

    write = as_write(Keeper())
    buffer = bytearray(b"abcd")
    write(memoryview(buffer)[:2])
    buffer[:2] = b"zz"
    assert kept == [b"ab"]


@pytest.mark.slow
@pytest.mark.parametrize("rollover", [0, 1])
def test_block_number_rollover(rollover):
    blksize = 8
    data = os.urandom(blksize * 70_000 + 3)
    n = neg(blksize=blksize, windowsize=32, rollover=rollover, options={"rollover": str(rollover)})
    got, sender, receiver, link = run(data, n)
    assert got == data
    wires = blocks_of(link)
    assert wires[65534] == 65535
    assert wires[65535] == rollover  # the block after 65535 wraps here


@pytest.mark.slow
def test_receiver_follows_a_sender_that_wraps_to_one():
    blksize = 8
    data = os.urandom(blksize * 66_000)
    got, sender, receiver, _ = run(data, neg(blksize=blksize, rollover=1), receiver_neg=neg(blksize=blksize))
    assert receiver.error is None and got == data
