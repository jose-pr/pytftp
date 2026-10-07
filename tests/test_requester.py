"""The client's opening exchange over a simulated link: the request, its repeats, the first answer.

No sockets and no real time: a scripted server answers each request that crosses the link, and a
virtual clock jumps to the requester's ``deadline`` whenever nothing is in flight. Every count
below is read from the datagrams that crossed the link, never from the object under test.
"""

from __future__ import annotations

import io
import os
from typing import Callable, List, Optional, Tuple

import pytest

from conftest import BAD_FIRST_ANSWERS, neg
from tftp import FileNotFound, Receiver, RemoteError, Sender, TFTPProtocolError, TransferTimeoutError
from tftp.options import Negotiated
from tftp.packet import TFTPOpcode, decode, encode_ack, encode_error, encode_oack, encode_request
from tftp.transfer import Requester, as_readinto, as_write

SERVER = ("192.0.2.1", 69)
PEER = ("192.0.2.1", 40001)
OTHER_HOST = ("192.0.2.99", 69)

RRQ, WRQ = TFTPOpcode.RRQ, TFTPOpcode.WRQ
DATA_1 = b"\x00\x03\x00\x01" + b"abc"
ACK_0 = b"\x00\x04\x00\x00"

#: What a server may answer with: ``(datagram, the address it comes from)``.
Reply = Tuple[bytes, Tuple[str, int]]


class Sim:
    """A link with a virtual clock: what the requester sends is shown to a scripted server.

    ``serve(packet, address, nth, now)`` is called for each datagram the requester sends, ``nth``
    counting those sent to ``SERVER``; the replies it returns reach the requester in order.
    """

    def __init__(self, serve: Callable[[bytes, Tuple[str, int], int, float], List[Reply]]) -> None:
        self.serve = serve
        self.now = 0.0
        self.sent: List[Tuple[float, bytes, Tuple[str, int]]] = []
        self.inbox: List[Reply] = []
        self.lose_answers = 0  # how many of the server's next datagrams to the requester never arrive
        self._requests = 0

    def send(self, packet, address) -> None:
        packet = bytes(packet)
        self.sent.append((self.now, packet, address))
        nth = self._requests
        if address == SERVER:
            self._requests += 1
        before = len(self.inbox)
        self.inbox.extend(self.serve(packet, address, nth, self.now))
        while self.lose_answers and len(self.inbox) > before:
            del self.inbox[before]
            self.lose_answers -= 1

    def to(self, address) -> List[bytes]:
        return [p for _, p, a in self.sent if a == address]

    def times(self, address=SERVER) -> List[float]:
        return [t for t, _, a in self.sent if a == address]


def spin(sim: Sim, deliver, timers, until, max_steps: int = 100_000) -> None:
    """Deliver what is in flight, else jump to the earliest deadline, until ``until()``."""
    for _ in range(max_steps):
        if until():
            return
        if sim.inbox:
            packet, source = sim.inbox.pop(0)
            deliver(packet, source, sim.now)
            continue
        live = [t for t in timers() if not t.is_done and t.deadline is not None]
        if not live:
            return
        sim.now = max(sim.now, min(t.deadline for t in live))
        for t in live:
            if not t.is_done and t.deadline is not None and t.deadline <= sim.now:
                t.on_timeout(sim.now)
    raise AssertionError("the simulation did not end")


def open_exchange(serve, opcode=RRQ, filename="f", retries=3, **kwargs) -> Tuple[Sim, Requester]:
    """Build a requester on a ``Sim`` and run it to its end."""
    sim = Sim(serve)
    requester = Requester(sim.send, SERVER, opcode, filename, retries, 0.0, **kwargs)
    spin(
        sim,
        lambda p, a, now: requester.handle(memoryview(p), len(p), a, now),
        lambda: [requester],
        lambda: requester.is_done,
    )
    return sim, requester


def silent(packet, address, nth, now) -> List[Reply]:
    return []


def answers(*replies: Reply) -> Callable[..., List[Reply]]:
    """A server answering the first request with ``replies``."""

    def serve(packet, address, nth, now):
        return list(replies) if address == SERVER and nth == 0 else []

    return serve


def the_answer(datagram: bytes, source=PEER) -> Callable[..., List[Reply]]:
    return answers((datagram, source))


# -- the request and its repeats ----------------------------------------------------------------


def test_a_request_nobody_answers_is_sent_one_plus_retries_times_and_then_fails():
    options = {"blksize": "1024", "tsize": "0"}
    sim, requester = open_exchange(silent, retries=3, timeout=1.0, backoff=2.0, options=options)
    expected = encode_request(RRQ, "f", mode="octet", options=options)
    assert sim.to(SERVER) == [expected] * 4
    assert sim.times() == [0.0, 1.0, 3.0, 7.0]
    # The last wait is 8 s, and then it gives up.
    assert sim.now == 15.0
    assert isinstance(requester.error, TransferTimeoutError)
    assert "192.0.2.1:69" in requester.error.message
    assert len(sim.sent) == 4
    requester.on_timeout(99.0)
    requester.handle(memoryview(DATA_1), len(DATA_1), PEER, 99.0)
    assert len(sim.sent) == 4


def test_the_wait_between_repeats_is_never_longer_than_max_timeout():
    sim, requester = open_exchange(silent, retries=5, timeout=1.0, backoff=2.0, max_timeout=3.0)
    times = sim.times()
    assert len(times) == 6
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert gaps == [1.0, 2.0, 3.0, 3.0, 3.0]
    assert sim.now == times[-1] + 3.0


def test_a_request_is_sent_to_the_server_in_the_mode_asked_for():
    sim, _ = open_exchange(silent, WRQ, "up", retries=0, mode="netascii")
    assert sim.to(SERVER) == [encode_request(WRQ, "up", mode="netascii", options={})]


def test_a_repeated_request_keeps_the_option_names_as_asked():
    options = {"Blksize": "512", "X-Mixed": "A"}
    sim, _ = open_exchange(silent, retries=1, options=options)
    assert sim.to(SERVER) == [encode_request(RRQ, "f", mode="octet", options=options)] * 2
    assert b"X-Mixed" in sim.to(SERVER)[0]


def _download_after(serve, retries=5, lose_answers=0):
    """Run the opening, then the transfer it opens, against a real ``Sender``; returns the file."""
    data = os.urandom(5000)
    state = {"sender": None}
    sink = io.BytesIO()

    def to_requester(packet):
        sim.inbox.append((bytes(packet), PEER))

    def serve_with_sender(packet, address, nth, now):
        if address == SERVER:
            if state["sender"] is None and serve(nth):
                state["sender"] = Sender(
                    to_requester,
                    as_readinto(io.BytesIO(data)),
                    neg(blksize=512),
                    retries,
                    now,
                    oack=encode_oack({"blksize": "512"}),
                )
        elif state["sender"] is not None:
            state["sender"].handle(memoryview(packet), len(packet), now)
        return []

    sim = Sim(serve_with_sender)
    sim.lose_answers = lose_answers
    requester = Requester(sim.send, SERVER, RRQ, "f", retries, 0.0, options={"blksize": "512"})
    spin(
        sim,
        lambda p, a, now: requester.handle(memoryview(p), len(p), a, now),
        lambda: [requester, state["sender"]] if state["sender"] else [requester],
        lambda: requester.is_done,
    )
    assert requester.error is None
    receiver = Receiver(
        lambda packet: sim.send(packet, requester.peer),
        as_write(sink),
        requester.negotiated,
        retries,
        sim.now,
        reply=encode_ack(0),
    )
    spin(
        sim,
        lambda p, a, now: receiver.handle(memoryview(p), len(p), now),
        lambda: [state["sender"], receiver],
        lambda: state["sender"].is_done and receiver.is_done,
    )
    return data, sink.getvalue(), sim


def test_a_lost_request_is_repeated_and_the_transfer_that_follows_is_whole():
    data, got, sim = _download_after(lambda nth: nth >= 1)
    assert got == data
    assert sim.times()[:2] == [0.0, 1.0]


def test_a_lost_first_answer_is_survived_and_the_transfer_that_follows_is_whole():
    data, got, sim = _download_after(lambda nth: True, lose_answers=1)
    assert got == data
    # The server ignored the repeated request; its own timer repeated the answer.
    assert sim.lose_answers == 0
    assert len(sim.times()) >= 2


# -- which datagram is the answer ---------------------------------------------------------------


def _stray_then_answer(nth_answer):
    def serve(packet, address, nth, now):
        if address != SERVER:
            return []
        if nth == 0:
            return [(DATA_1, OTHER_HOST), (b"\x00", SERVER)]
        return [(DATA_1, nth_answer)] if nth == 1 else []

    return serve


def _same_host(address) -> bool:
    return address[0] == SERVER[0]


def test_a_datagram_from_another_host_and_one_of_one_octet_are_not_the_answer():
    sim, requester = open_exchange(_stray_then_answer(("192.0.2.1", 5555)), accepts=_same_host)
    # Neither stray got a reply, and the request was repeated on time.
    assert [a for _, _, a in sim.sent] == [SERVER, SERVER]
    assert sim.times() == [0.0, 1.0]
    assert requester.peer == ("192.0.2.1", 5555)
    assert requester.first_data == DATA_1
    assert requester.error is None


def test_without_a_rule_the_first_datagram_of_two_octets_or_more_is_the_answer():
    sim, requester = open_exchange(_stray_then_answer(("192.0.2.1", 5555)), accepts=None)
    assert requester.peer == OTHER_HOST
    assert sim.times() == [0.0]


def test_the_peer_is_the_address_the_answer_came_from_whatever_its_port():
    for source in (("192.0.2.1", 1), ("192.0.2.1", 65535), SERVER):
        _, requester = open_exchange(the_answer(DATA_1, source), accepts=_same_host)
        assert requester.peer == source


# -- an OACK -------------------------------------------------------------------------------------


def test_an_oack_leaving_out_an_asked_option_runs_on_the_default():
    _, requester = open_exchange(
        the_answer(encode_oack({"tsize": "100"})), options={"blksize": "1024", "tsize": "0"}
    )
    assert requester.error is None
    assert requester.negotiated.blksize == 512
    assert requester.negotiated.tsize == 100
    assert requester.oack == {"tsize": "100"}
    assert requester.first_data is None


@pytest.mark.parametrize(
    "oack",
    [{"blksize": "2048"}, {"blksize": "1024", "windowsize": "4"}],
    ids=["a larger blksize than asked", "an option not asked"],
)
def test_an_oack_that_is_not_what_was_asked_is_refused_with_error_8(oack):
    sim, requester = open_exchange(
        the_answer(encode_oack(oack), PEER), options={"blksize": "1024"}, accepts=_same_host
    )
    refusal = decode(sim.to(PEER)[0])
    assert (refusal.code, len(sim.to(PEER))) == (8, 1)
    assert [a for _, _, a in sim.sent].count(PEER) == 1
    assert requester.error is not None and requester.error.code == 8
    assert isinstance(requester.error, TFTPProtocolError)
    assert requester.retry_without_options is False
    assert requester.negotiated is None


def _oack_that_cannot_be_read(monkeypatch):
    """The codec reads every OACK of two octets or more, so one that fails is made to."""
    from tftp.exceptions import TFTPDecodeError
    from tftp.packet import OptionAckPacket

    def refuse(data):
        raise TFTPDecodeError("cut")

    monkeypatch.setattr(OptionAckPacket, "decode", staticmethod(refuse))


def test_an_oack_that_does_not_decode_is_refused_with_error_4(monkeypatch):
    _oack_that_cannot_be_read(monkeypatch)
    sim, requester = open_exchange(the_answer(b"\x00\x06blksize"), options={"blksize": "512"})
    assert [decode(p).code for p in sim.to(PEER)] == [4]
    assert isinstance(requester.error, TFTPProtocolError)
    assert requester.retry_without_options is False


# -- an ERROR and the request without options ---------------------------------------------------------


@pytest.mark.parametrize("code", [8, 4, 0])
def test_an_error_for_the_options_asks_for_the_request_to_be_made_without_them(code):
    sim, requester = open_exchange(the_answer(encode_error(code, "no")), options={"blksize": "512"})
    assert requester.retry_without_options is True
    assert isinstance(requester.error, RemoteError) and requester.error.code == code
    assert len(sim.sent) == 1  # an ERROR is never answered
    # The second request leaves for the server, without options, and the same time limit holds.
    again = Requester(
        sim.send, SERVER, RRQ, "f", 3, sim.now, options={}, expires=sim.now + 5.0, fallback=True
    )
    assert sim.to(SERVER)[-1] == encode_request(RRQ, "f", mode="octet", options={})
    assert again.deadline == sim.now + 1.0


@pytest.mark.parametrize(
    "case, kwargs, code",
    [
        ("the client does not fall back", {"options": {"blksize": "512"}, "fallback": False}, 8),
        ("the request carried no options", {"options": {}}, 8),
        ("the request carried no options at all", {}, 4),
        ("another error", {"options": {"blksize": "512"}}, 1),
        ("another error, 2", {"options": {"blksize": "512"}}, 2),
    ],
)
def test_an_error_that_is_not_about_the_options_is_final(case, kwargs, code):
    _, requester = open_exchange(the_answer(encode_error(code, "no")), **kwargs)
    assert requester.retry_without_options is False, case
    assert requester.error.code == code


def test_error_1_is_the_typed_remote_error():
    _, requester = open_exchange(the_answer(encode_error(1, "nope")), options={"blksize": "512"})
    assert isinstance(requester.error, FileNotFound)
    assert requester.error.message == "nope"


def test_a_timeout_does_not_ask_for_the_request_to_be_made_without_options():
    _, requester = open_exchange(silent, retries=1, options={"blksize": "512"})
    assert isinstance(requester.error, TransferTimeoutError)
    assert requester.retry_without_options is False


# -- the time limit ----------------------------------------------------------------------------------


def test_the_time_limit_cuts_the_wait_short_and_ends_the_exchange_with_nothing_more_sent():
    sim, requester = open_exchange(silent, retries=5, timeout=1.0, backoff=2.0, expires=2.5)
    assert sim.times() == [0.0, 1.0]
    assert sim.now == 2.5
    assert isinstance(requester.error, TransferTimeoutError)
    assert "time limit" in requester.error.message
    assert len(sim.sent) == 2


def test_the_time_limit_is_judged_before_the_count_of_retries():
    sim, requester = open_exchange(silent, retries=0, timeout=1.0, expires=1.0)
    assert "time limit" in requester.error.message
    assert len(sim.sent) == 1


def test_the_deadline_is_never_past_the_time_limit():
    sim = Sim(silent)
    requester = Requester(sim.send, SERVER, RRQ, "f", 5, 0.0, timeout=4.0, expires=1.5)
    assert requester.deadline == 1.5
    requester.on_timeout(1.5)
    assert requester.deadline is None


# -- classifying the first answer ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, is_read, answer", [pytest.param(i, r, a, id=i) for i, r, a in BAD_FIRST_ANSWERS]
)
def test_a_first_answer_a_server_may_not_send_is_a_protocol_error(name, is_read, answer):
    sim, requester = open_exchange(the_answer(answer), RRQ if is_read else WRQ)
    assert isinstance(requester.error, TFTPProtocolError)
    assert requester.negotiated is None and requester.first_data is None
    if name == "rrq-error-cut-to-2":
        assert len(sim.sent) == 1  # nothing is said to a peer whose ERROR cannot be read
    else:
        assert [decode(p).code for p in sim.to(PEER)] == [4]
    assert requester.retry_without_options is False


def test_data_1_answers_a_read_on_the_defaults_and_is_kept_whole():
    sim, requester = open_exchange(the_answer(DATA_1), timeout=2.5, options={"blksize": "1024"})
    assert requester.error is None
    assert requester.first_data == DATA_1
    assert requester.negotiated == Negotiated(timeout=2.5)
    assert requester.oack is None
    assert len(sim.sent) == 1


def test_ack_0_answers_a_write_on_the_defaults():
    sim, requester = open_exchange(the_answer(ACK_0), WRQ, timeout=2.5)
    assert requester.error is None
    assert requester.first_data is None
    assert requester.negotiated == Negotiated(timeout=2.5)
    assert len(sim.sent) == 1


def test_the_options_asked_are_kept_as_they_were_asked():
    _, requester = open_exchange(the_answer(DATA_1), options={"blksize": "512"})
    assert requester.options == {"blksize": "512"}


# -- a send the host refuses --------------------------------------------------------------------------


def test_a_send_that_raises_ends_the_exchange_with_the_outcome_recorded():
    def refuse(packet, address):
        raise OSError(90, "message too long")

    with pytest.raises(OSError):
        Requester(refuse, SERVER, RRQ, "f", 3, 0.0)

    calls = []

    def refuse_the_second(packet, address):
        calls.append(packet)
        if len(calls) == 2:
            raise OSError(90, "message too long")

    requester = Requester(refuse_the_second, SERVER, RRQ, "f", 3, 0.0)
    with pytest.raises(OSError):
        requester.on_timeout(1.0)
    assert len(calls) == 2


def test_a_refused_error_is_still_the_outcome():
    sent: List[Tuple[bytes, Tuple[str, int]]] = []

    def refuse(packet, address):
        sent.append((bytes(packet), address))
        if len(sent) > 1:
            raise OSError(90, "message too long")

    requester = Requester(refuse, SERVER, RRQ, "f", 3, 0.0, options={"blksize": "512"})
    answer = encode_oack({"blksize": "4096"})
    with pytest.raises(OSError):
        requester.handle(memoryview(answer), len(answer), PEER, 0.1)
    assert requester.is_done and requester.error is not None and requester.error.code == 8


# -- the size probe ----------------------------------------------------------------------------------


def test_a_probe_answered_by_an_oack_declines_it_with_error_8():
    oack = {"tsize": "1234"}
    sim, requester = open_exchange(the_answer(encode_oack(oack)), probe=True, options={"tsize": "0"})
    assert sim.to(PEER) == [b"\x00\x05\x00\x08size probe only\x00"]
    assert requester.error is None
    assert requester.oack == oack
    assert requester.negotiated is None and requester.first_data is None


def test_a_probe_answered_by_a_short_data_1_acknowledges_it():
    data = b"\x00\x03\x00\x01" + b"x" * 511
    sim, requester = open_exchange(the_answer(data), probe=True, options={"tsize": "0"})
    assert sim.to(PEER) == [b"\x00\x04\x00\x01"]
    assert requester.first_data == data
    assert requester.oack is None and requester.negotiated is None and requester.error is None


def test_a_probe_answered_by_a_full_data_1_declines_it_with_error_0():
    data = b"\x00\x03\x00\x01" + b"x" * 512
    sim, requester = open_exchange(the_answer(data), probe=True, options={"tsize": "0"})
    assert sim.to(PEER) == [b"\x00\x05\x00\x00size probe only\x00"]
    assert requester.first_data == data
    assert requester.negotiated is None and requester.error is None


def test_a_probe_answered_by_an_error_ends_with_that_error_and_says_nothing():
    sim, requester = open_exchange(the_answer(encode_error(1, "no such file")), probe=True)
    assert isinstance(requester.error, FileNotFound)
    assert len(sim.sent) == 1


def test_a_probe_answered_by_an_error_for_its_options_may_be_asked_again_without_them():
    _, requester = open_exchange(the_answer(encode_error(8, "no")), probe=True, options={"tsize": "0"})
    assert requester.retry_without_options is True
    _, declined = open_exchange(
        the_answer(encode_error(8, "no")), probe=True, options={"tsize": "0"}, fallback=False
    )
    assert declined.retry_without_options is False


def test_a_probe_answered_by_an_oack_that_does_not_decode_is_refused_with_error_4(monkeypatch):
    _oack_that_cannot_be_read(monkeypatch)
    sim, requester = open_exchange(the_answer(b"\x00\x06tsize"), probe=True, options={"tsize": "0"})
    assert sim.to(PEER) == [b"\x00\x05\x00\x04malformed answer\x00"]
    assert isinstance(requester.error, TFTPProtocolError)


def test_a_probe_answered_by_something_else_is_a_protocol_error_with_error_4():
    sim, requester = open_exchange(the_answer(b"\x00\x04\x00\x00"), probe=True)
    assert [decode(p).code for p in sim.to(PEER)] == [4]
    assert "unexpected opcode 4" in requester.error.message


def test_a_probe_nobody_answers_times_out_like_any_request():
    sim, requester = open_exchange(silent, probe=True, retries=2)
    assert len(sim.times()) == 3
    assert isinstance(requester.error, TransferTimeoutError)


def test_time_is_the_callers_whatever_its_origin():
    sim = Sim(silent)
    requester: Optional[Requester] = Requester(sim.send, SERVER, RRQ, "f", 1, 1000.0, timeout=1.0)
    assert requester is not None and requester.deadline == 1001.0
