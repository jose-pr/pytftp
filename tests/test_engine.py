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

from conftest import Link, neg
from tftp import Receiver, Sender, TFTPError
from tftp.options import Negotiated
from tftp.packet import decode, encode_ack, encode_oack
from tftp.transfer import as_readinto, as_write

DATA, ACK, ERROR, OACK = 3, 4, 5, 6


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
    while not (sender.is_done and receiver.is_done) and steps < max_steps:
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
            deadlines = [t.deadline for t in (sender, receiver) if not t.is_done and t.deadline is not None]
            if not deadlines:
                break
            now = min(deadlines)
            for side in (sender, receiver):
                if not side.is_done and side.deadline is not None and side.deadline <= now:
                    side.on_timeout(now)
    return sink.getvalue(), sender, receiver, link


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


@pytest.mark.parametrize("windowsize", [1, 2, 4, 16])
def test_sorcerers_apprentice_does_not_double_traffic(windowsize):
    """Every ACK delivered twice must not make the sender send DATA twice."""
    data = os.urandom(64 * 200)

    def rule(direction, packet, count):
        return 2 if direction == "r" else 1

    got, sender, _, link = run(data, neg(blksize=64, windowsize=windowsize), rule=rule)
    assert got == data
    sent = blocks_of(link)
    if windowsize == 1:
        assert len(sent) == len(set(sent)) == 201
        assert sender.retransmits == 0
    else:
        # A duplicated ACK may cost one window, and one window per two
        # windows of progress at most: the traffic is bounded, not doubled.
        assert len(sent) <= 1.5 * 201 + windowsize, len(sent)


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
    assert receiver.is_done and receiver.error.code == 3 and receiver.error.message == "disk full"
    assert link.sent["r"] == []


def test_source_failure_is_reported_to_peer():
    class Broken(io.RawIOBase):
        def readinto(self, b):
            raise PermissionError(13, "denied")

    link = Link()
    sender = Sender(link.from_sender, as_readinto(Broken()), neg(), 3, 0.0)
    assert sender.is_done and sender.error.code == 2
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


# -- the stream adapters: a raw stream's short write and its "nothing ready" --------------------------


class ShortWriter(io.RawIOBase):
    """Accepts at most ``limit`` octets per call, as a pipe or a socket may."""

    def __init__(self, limit: int = 100) -> None:
        self.limit = limit
        self.got = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, data) -> int:
        take = bytes(data[: self.limit])
        self.got += take
        return len(take)


def test_a_raw_sink_that_takes_part_of_a_block_is_written_until_it_has_all_of_it():
    """io.RawIOBase.write: "The number of bytes written, which may be less than the length of b"."""
    sink = ShortWriter(100)
    block = os.urandom(512)
    as_write(sink)(memoryview(bytearray(block)))
    assert bytes(sink.got) == block


def test_a_transfer_into_a_raw_sink_that_takes_100_octets_per_call_holds_every_octet():
    data = os.urandom(512 * 20 + 7)
    sink = ShortWriter(100)
    out: List[bytes] = []
    receiver = Receiver(out.append, as_write(sink), neg(), 5, 0.0)
    sent: List[bytes] = []
    sender = Sender(lambda packet: sent.append(bytes(packet)), as_readinto(io.BytesIO(data)), neg(), 5, 0.0)
    while not receiver.is_done:
        packet = sent.pop(0)
        receiver.handle(memoryview(packet), len(packet), 0.0)
        ack = bytes(out.pop(0))
        sender.handle(memoryview(ack), len(ack), 0.0)
    assert receiver.error is None and receiver.bytes == len(data)
    assert bytes(sink.got) == data


def test_a_sink_that_takes_nothing_fails_the_transfer_instead_of_dropping_the_block():
    class Stuck(io.RawIOBase):
        def writable(self):
            return True

        def write(self, data):
            return 0

    out: List[bytes] = []
    receiver = Receiver(out.append, as_write(Stuck()), neg(), 5, 0.0, reply=encode_ack(0))
    packet = struct.pack("!HH", DATA, 1) + b"x" * 512
    receiver.handle(memoryview(packet), len(packet), 0.0)
    assert receiver.is_done and isinstance(receiver.error, TFTPError)
    assert receiver.bytes == 0


def test_a_raw_sink_with_nothing_ready_holds_the_block_until_resume():
    """io.RawIOBase.write returns None when a non-blocking stream cannot take any byte."""

    class Pipe(io.RawIOBase):
        ready = False

        def __init__(self):
            self.got = bytearray()

        def writable(self):
            return True

        def write(self, data):
            if not self.ready:
                return None
            self.got += data
            return len(data)

    sink = Pipe()
    out: List[bytes] = []
    receiver = Receiver(out.append, as_write(sink), neg(), 5, 0.0, reply=encode_ack(0))
    packet = struct.pack("!HH", DATA, 1) + b"x" * 512
    receiver.handle(memoryview(packet), len(packet), 0.0)
    assert receiver.is_stalled and receiver.error is None and bytes(sink.got) == b""
    sink.ready = True
    receiver.resume(0.0)
    assert bytes(sink.got) == b"x" * 512 and not receiver.is_stalled


class NotReadyOnce(io.RawIOBase):
    """A non-blocking raw source: its second readinto() has nothing ready and returns None."""

    def __init__(self, data: bytes) -> None:
        self.source = io.BytesIO(data)
        self.calls = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer):
        self.calls += 1
        if self.calls == 2:
            return None
        return self.source.readinto(buffer)


def test_a_raw_source_with_nothing_ready_pauses_the_sender_and_loses_no_octet():
    """io.RawIOBase.readinto returns None when a non-blocking stream has nothing to read."""
    data = os.urandom(512 * 20 + 7)
    sink = io.BytesIO()
    to_receiver: List[bytes] = []
    to_sender: List[bytes] = []
    receiver = Receiver(lambda p: to_sender.append(bytes(p)), as_write(sink), neg(), 5, 0.0)
    sender = Sender(lambda p: to_receiver.append(bytes(p)), as_readinto(NotReadyOnce(data)), neg(), 5, 0.0)
    stalled = False
    for _ in range(100_000):
        if to_receiver:
            packet = to_receiver.pop(0)
            receiver.handle(memoryview(packet), len(packet), 0.0)
        elif to_sender:
            ack = to_sender.pop(0)
            sender.handle(memoryview(ack), len(ack), 0.0)
        elif sender.is_stalled:
            stalled = True
            sender.resume(0.0)
        else:
            break
    assert stalled and sender.error is None and receiver.error is None
    assert sender.is_done and receiver.is_done
    assert sink.getvalue() == data


def test_a_read_only_source_that_returns_none_is_also_nothing_ready():
    class Chunks:
        def __init__(self):
            self.items = [b"a" * 512, None, b"b" * 100, b""]

        def read(self, n):
            return self.items.pop(0)

    out: List[bytes] = []
    sender = Sender(out.append, as_readinto(Chunks()), neg(), 5, 0.0)
    assert sender.error is None and len(out) == 1
    sender.handle(memoryview(encode_ack(1)), 4, 0.0)
    assert sender.is_stalled
    sender.resume(0.0)
    assert sender.error is None and out[-1] == struct.pack("!HH", DATA, 2) + b"b" * 100


# -- the window and the block-number space ---------------------------------------------------------------


def ack_bytes(block: int) -> bytes:
    return struct.pack("!HH", ACK, block)


def test_a_late_ack_is_not_taken_for_one_that_acknowledges_the_whole_window():
    """A 16-bit sequence space tells old from new only when the window is at most half of it."""
    out: List[bytes] = []
    payload = os.urandom(8 * 70_000)
    sender = Sender(out.append, as_readinto(io.BytesIO(payload)), neg(blksize=8, windowsize=65535), 5, 0.0)
    assert sender.windowsize == 32767
    for block in (10, 11):
        sender.handle(memoryview(ack_bytes(block)), 4, 0.0)
    sent = len(out)
    sender.handle(memoryview(ack_bytes(10)), 4, 0.0)  # the first of them, delivered again
    assert len(out) == sent and not sender.is_done and sender.error is None


def test_a_window_above_half_the_block_space_still_delivers_the_file():
    data = os.urandom(8 * 40_000)
    got, sender, receiver, _ = run(data, neg(blksize=8, windowsize=40_000))
    assert got == data and sender.error is None and receiver.error is None


def test_a_datagram_shorter_than_its_opcode_needs_is_dropped_by_both_sides():
    """RFC 1350: an ACK and a DATA hold opcode and block, an ERROR opcode and code: four octets."""
    for opcode in (DATA, ACK, ERROR, OACK):
        short = struct.pack("!HB", opcode, 0)
        out: List[bytes] = []
        sender = Sender(out.append, as_readinto(io.BytesIO(b"x" * 2000)), neg(), 5, 0.0)
        receiver = Receiver(out.append, as_write(io.BytesIO()), neg(), 5, 0.0, reply=encode_ack(0))
        before = len(out)
        sender.handle(memoryview(short), len(short), 0.0)
        receiver.handle(memoryview(short), len(short), 0.0)
        assert len(out) == before, opcode
        assert (sender.is_done, receiver.is_done) == (False, False), opcode
        assert sender.error is None and receiver.error is None, opcode


@pytest.mark.parametrize("windowsize", [1, 4])
def test_a_receiver_follows_a_sender_that_wraps_to_block_one(windowsize):
    """The sender's block 65536 is sent as block number 1; no rollover was negotiated."""
    blksize = 8
    blocks = [b.to_bytes(blksize, "big") for b in range(1, 65_541)]  # 65535 blocks, then 5 after the wrap
    sink = io.BytesIO()
    out: List[bytes] = []
    receiver = Receiver(out.append, as_write(sink), neg(blksize=blksize, windowsize=windowsize), 5, 0.0)

    def feed(wire: int, payload: bytes) -> None:
        packet = struct.pack("!HH", DATA, wire) + payload
        receiver.handle(memoryview(packet), len(packet), 0.0)

    for number in range(1, 65_536):
        feed(number, blocks[number - 1])
    assert receiver.blocks == 65_535
    # The sender's window from block 65536 is wire 1, 2, 3, 4 (windowsize 4) or wire 1 (lock-step).
    for number in range(65_536, 65_536 + max(windowsize, 1)):
        feed(number - 65_535, blocks[number - 1])
    if windowsize > 1:
        # A receiver told nothing cannot yet tell this from a lost wire 0: it reports its place, and
        # the sender, which has no wire 0, sends its window again from wire 1.
        assert receiver.blocks == 65_535 and out[-1] == ack_bytes(65_535)
        for number in range(65_536, 65_536 + windowsize):
            feed(number - 65_535, blocks[number - 1])
    assert receiver.error is None and receiver.blocks == 65_535 + windowsize
    assert out[-1] == ack_bytes(windowsize)
    tail = sink.getvalue()[65_535 * blksize :]
    assert tail == b"".join(blocks[65_535 : 65_535 + windowsize])


def test_a_lost_wire_zero_block_is_not_taken_for_a_wrap_to_block_one():
    """A sender that wraps to 0 and loses its block 65536 must still be received block for block."""
    blksize, windowsize = 8, 4
    sink = io.BytesIO()
    out: List[bytes] = []
    receiver = Receiver(out.append, as_write(sink), neg(blksize=blksize, windowsize=windowsize), 5, 0.0)
    blocks = [b.to_bytes(blksize, "big") for b in range(1, 65_541)]

    def feed(wire: int, payload: bytes) -> None:
        packet = struct.pack("!HH", DATA, wire) + payload
        receiver.handle(memoryview(packet), len(packet), 0.0)

    for number in range(1, 65_536):
        feed(number, blocks[number - 1])
    for number in (65_537, 65_538, 65_539):  # wire 1, 2, 3: block 65536 (wire 0) was lost
        feed(number - 65_536, blocks[number - 1])
    assert receiver.blocks == 65_535
    for number in range(65_536, 65_540):  # the sender's window again, from wire 0
        feed(number - 65_536, blocks[number - 1])
    assert receiver.error is None and receiver.blocks == 65_539
    assert sink.getvalue()[65_535 * blksize :] == b"".join(blocks[65_535:65_539])


@pytest.mark.slow
@pytest.mark.parametrize("windowsize", [2, 3, 4, 16])
def test_a_windowed_receiver_follows_a_sender_that_wraps_to_one_end_to_end(windowsize):
    blksize = 8
    data = os.urandom(blksize * 66_000)
    sender_neg = neg(blksize=blksize, windowsize=windowsize, rollover=1)
    got, sender, receiver, _ = run(data, sender_neg, receiver_neg=neg(blksize=blksize, windowsize=windowsize))
    assert sender.error is None and receiver.error is None and got == data


# -- an ERROR's text has one reading -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tail,text",
    [
        (b"caf\xe9 not found\x00", "caf\ufffd not found"),
        (b"no terminator", "no terminator"),
        (b"first\x00second\x00", "first"),
    ],
)
def test_an_error_text_reads_the_same_when_it_answers_a_request_and_mid_transfer(tail, text):
    """RFC 1350: the message is a string of octets ended by a zero; it is read for people, so bad UTF-8 becomes U+FFFD."""
    wire = struct.pack("!HH", ERROR, 1) + tail
    assert decode(wire).message == text
    receiver = Receiver(lambda packet: None, as_write(io.BytesIO()), neg(), 5, 0.0)
    receiver.handle(memoryview(wire), len(wire), 0.0)
    assert receiver.is_done and receiver.error.message == text
    sender = Sender(lambda packet: None, as_readinto(io.BytesIO(b"x" * 2000)), neg(), 5, 0.0)
    sender.handle(memoryview(wire), len(wire), 0.0)
    assert sender.is_done and sender.error.message == text


def test_the_engine_does_not_import_the_netascii_writer_to_tell_it_copies():
    """as_write reads the marker every writer declares, NetasciiWriter included."""
    import tftp.transfer._engine as base  # the module that must not import netascii
    from tftp.netascii import NetasciiWriter

    assert NetasciiWriter.copies_writes is True
    assert not hasattr(base, "NetasciiWriter")


# -- traffic after one event, with delay and reordering --------------------------------------

import heapq

LATENCY = 0.005


class TimedLink:
    """A link with a delivery time per packet.

    ``rule(direction, packet, count)`` returns the extra delay of each copy to
    deliver: ``[0.0]`` is normal, ``[]`` is loss, ``[0.0, 0.0]`` a duplicate,
    ``[1.5]`` a late packet, and a delay of a few latencies on one DATA lets
    the next overtake it.
    """

    def __init__(self, rule=None):
        self.rule = rule or (lambda *_: [0.0])
        self.queue: list = []
        self.sent = {"s": [], "r": []}
        self.now = 0.0
        self._seq = 0

    def _put(self, direction, packet):
        packet = bytes(packet)
        log = self.sent[direction]
        log.append(packet)
        for extra in self.rule(direction, packet, len(log) - 1):
            self._seq += 1
            heapq.heappush(self.queue, (self.now + LATENCY + extra, self._seq, direction, packet))

    def from_sender(self, packet):
        self._put("s", packet)

    def from_receiver(self, packet):
        self._put("r", packet)


def run_timed(blocks, windowsize, rule, blksize=64, retries=20):
    """Transfer ``blocks`` full blocks (plus the empty last one); returns the counts."""
    data = os.urandom(blksize * blocks)
    link = TimedLink(rule)
    n = neg(blksize=blksize, windowsize=windowsize)
    sink = io.BytesIO()
    receiver = Receiver(link.from_receiver, as_write(sink), n, retries, 0.0)
    sender = Sender(link.from_sender, as_readinto(io.BytesIO(data)), n, retries, 0.0)
    steps = 0
    while not (sender.is_done and receiver.is_done) and steps < 200_000:
        steps += 1
        deadlines = [t.deadline for t in (sender, receiver) if not t.is_done and t.deadline is not None]
        due = min(deadlines) if deadlines else None
        if link.queue and (due is None or link.queue[0][0] <= due):
            link.now, _, direction, packet = heapq.heappop(link.queue)
            target = receiver if direction == "s" else sender
            target.handle(memoryview(packet), len(packet), link.now)
        elif due is not None:
            link.now = due
            for side in (sender, receiver):
                if not side.is_done and side.deadline is not None and side.deadline <= link.now:
                    side.on_timeout(link.now)
        else:
            break
    assert sender.error is None and receiver.error is None
    assert sink.getvalue() == data
    return {
        "data": len(link.sent["s"]),
        "acks": len(link.sent["r"]),
        "seconds": link.now,
    }


def _event(kind):
    def rule(direction, packet, count):
        if kind == "dup" and direction == "r" and count == 4:
            return [0.0, 0.0]
        if kind == "lost" and direction == "r" and count == 4:
            return []
        if kind == "late" and direction == "r" and count == 4:
            return [1.5]
        if kind == "lostdata" and direction == "s" and count == 10:
            return []
        if kind == "reorder" and direction == "s" and count == 10:
            return [3 * LATENCY]  # DATA 11 overtakes DATA 10 (block 9 is the 10th, offset by the window)
        return [0.0]

    return rule


BLOCKS = 400


@pytest.mark.parametrize("windowsize", [1, 2, 4, 16])
@pytest.mark.parametrize("event", ["dup", "lost", "late", "reorder", "lostdata"])
def test_one_event_costs_at_most_a_window_of_traffic(windowsize, event):
    control = run_timed(BLOCKS, windowsize, _event("none"))
    assert control["data"] == BLOCKS + 1
    got = run_timed(BLOCKS, windowsize, _event(event))
    # Each of these events costs the retransmission of one window and a few
    # ACKs; none may be paid again for every window that follows.
    assert got["data"] <= control["data"] + 2 * windowsize, (event, windowsize, got, control)
    assert got["acks"] <= control["acks"] + 2 * windowsize + 2, (event, windowsize, got, control)
    if event in ("lostdata", "reorder") and windowsize > 1:
        # A hole is repaired from the receiver's report, not from a timeout.
        assert got["seconds"] < control["seconds"] + 0.5, (event, windowsize, got, control)


def test_an_ack_that_advances_the_window_sends_only_the_blocks_that_now_fit():
    sent = []
    sender = Sender(
        sent.append, as_readinto(io.BytesIO(os.urandom(64 * 100))), neg(blksize=64, windowsize=8), 5, 0.0
    )
    assert len(sent) == 8
    for acked in (1, 2, 3):
        before = len(sent)
        sender.handle(memoryview(encode_ack(acked)), 4, 0.1)
        assert len(sent) == before + 1
        assert struct.unpack("!H", sent[-1][2:4])[0] == 8 + acked  # the one block that fits
    assert sender.retransmits == 0
