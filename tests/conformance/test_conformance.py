"""Replay what tftp-hpa answered, against this library's server, with no peer installed.

Each directory under ``cases/`` holds ``case.json``, what a client asks and how it
carries on, and ``golden.json``, the exchange tftp-hpa's server had with that
client, recorded by ``record.py``. The replay plays the same client against a
server of this library over the same files and compares what the server said:
an OACK by its options, an ERROR by its code, a DATA or an ACK to the octet.
Message texts and the ports a reply comes from are not compared.

A case named in ``deviations.json`` is one where this library differs from
tftp-hpa on purpose: it is asserted as this library behaves and as the
reference does not, and the README lists it. A case whose ``case.json`` says
``"reference": null`` has no recording, only a deviation that says what is
answered.
"""

from __future__ import annotations

import json
import re

import pytest

import exchange
from tftp import Profile, TFTPServerOptions

HERE = exchange.HERE
README = HERE.parent.parent / "README.md"
DEVIATIONS = {
    entry["case"]: entry for entry in json.loads((HERE / "deviations.json").read_text(encoding="utf-8"))
}


def _case(directory):
    return pytest.param(
        directory,
        id=directory.name,
        marks=[pytest.mark.slow] if exchange.load(directory).get("slow") else [],
    )


RECORDED = [_case(d) for d in exchange.cases() if (d / "golden.json").exists()]
DEVIATING = [_case(d) for d in exchange.cases() if d.name in DEVIATIONS]


def _golden(directory):
    return json.loads((directory / "golden.json").read_text(encoding="utf-8"))


@pytest.fixture
def replay(tmp_path, make_server):
    """Play a case against a server of this library over the case's own files."""

    def play(directory):
        case = exchange.load(directory)
        exchange.prepare(tmp_path, case)
        options = Profile.HPA.server
        if case.get("allow"):
            options = TFTPServerOptions(allowed=options.allowed | set(case["allow"]))
        server = make_server(tmp_path, options=options, writable=True, overwrite=True)
        return exchange.play(case, ("127.0.0.1", server.server_address[1]))

    return play


def test_there_are_recorded_cases():
    assert len(RECORDED) >= 20


@pytest.mark.parametrize("directory", exchange.cases(), ids=lambda d: d.name)
def test_a_case_says_what_it_asks_and_what_it_records(directory):
    case = exchange.load(directory)
    assert case["description"] and case["request"]["op"] in ("RRQ", "WRQ") and case["request"]["file"]
    if case.get("reference", True) is None:
        assert case["reason"] and directory.name in DEVIATIONS and not (directory / "golden.json").exists()
        return
    golden = _golden(directory)
    assert golden["reference"]["tftpd"].startswith("tftp-hpa 5.3") and golden["reference"]["environment"]
    assert golden["exchange"][0]["sender"] == "client"
    assert {entry["sender"] for entry in golden["exchange"]} == {"client", "server"}


@pytest.mark.parametrize("directory", [d for d in RECORDED if d.id not in DEVIATIONS])
def test_the_server_says_what_tftp_hpa_said(directory, replay):
    wanted = [(sender, bytes.fromhex(hexed)) for sender, hexed in _pairs(_golden(directory))]
    got = replay(directory)
    assert [exchange.summary(d) for _, d in got] == [exchange.summary(d) for _, d in wanted]
    for (sender, want), (_, have) in zip(wanted, got):
        assert have == want if sender == "client" else exchange.same(want, have)


@pytest.mark.parametrize("directory", [d for d in DEVIATING])
def test_a_deviation_is_what_the_server_does_and_not_what_tftp_hpa_did(directory, replay):
    entry = DEVIATIONS[directory.name]
    got = [d for sender, d in replay(directory) if sender == "server"]
    assert [exchange.summary(d) for d in got][: len(entry["ours"])] == entry["ours"]
    if (directory / "golden.json").exists():
        wanted = [bytes.fromhex(h) for sender, h in _pairs(_golden(directory)) if sender == "server"]
        assert not (len(wanted) == len(got) and all(exchange.same(w, g) for w, g in zip(wanted, got)))


def _pairs(golden):
    return [(entry["sender"], entry["payload"]) for entry in golden["exchange"]]


def test_every_deviation_has_a_case_and_a_difference():
    names = {d.name for d in exchange.cases()}
    assert set(DEVIATIONS) <= names
    for entry in DEVIATIONS.values():
        assert entry["difference"].endswith(".") and entry["ours"]


def test_the_readme_lists_exactly_the_deviations():
    text = README.read_text(encoding="utf-8")
    (section,) = re.findall(r"\n## Differences from tftp-hpa\n(.*?)(?=\n## |\Z)", text, re.S)
    listed = re.findall(r"^\| `([a-z0-9-]+)` \|", section, re.M)
    assert sorted(listed) == sorted(DEVIATIONS)
    for case, entry in DEVIATIONS.items():
        assert entry["difference"] in section, case


def test_a_golden_holds_only_what_the_case_asked():
    """Loopback or documentation addresses only: no address is in an exchange but the one the case names."""
    for directory in exchange.cases():
        path = directory / "golden.json"
        if path.exists():
            text = path.read_text(encoding="utf-8")
            assert not re.search(r"\d+\.\d+\.\d+\.\d+|[0-9a-f]{2}(:[0-9a-f]{2}){5}", text), directory.name
            assert set(json.loads(text)) == {"reference", "exchange"}


@pytest.mark.parametrize("page", [README, HERE.parent.parent / "docs" / "protocol.md"], ids=lambda p: p.name)
def test_the_pages_count_the_recorded_requests(page):
    text = " ".join(page.read_text(encoding="utf-8").split())
    (count,) = re.findall(r"answered to (\d+) requests", text)
    assert int(count) == len(list(HERE.glob("cases/*/golden.json")))
