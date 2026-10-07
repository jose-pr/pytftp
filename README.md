# tftp

[![Version](https://img.shields.io/pypi/v/tftp.svg)](https://pypi.org/project/tftp/)
[![Python versions](https://img.shields.io/pypi/pyversions/tftp.svg)](https://pypi.org/project/tftp/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/jose-pr/pytftp/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-latest-blue.svg)](https://jose-pr.github.io/pytftp/)
[![CI](https://img.shields.io/github/actions/workflow/status/jose-pr/pytftp/test.yml)](https://github.com/jose-pr/pytftp/actions/workflows/test.yml)

A **pure-Python TFTP client, server, relay and capture decoder for IPv4 and
IPv6** that implements the whole of modern TFTP: RFC 1350, the option
extension (RFC 2347) with `blksize`, `timeout`, `tsize` and `windowsize`
(RFC 2348, 2349, 7440), tftp-hpa's extensions, block-number rollover and
netascii. Built as infrastructure for network boot: it interoperates with
iPXE, tftp-hpa, BusyBox, dnsmasq and curl, serves files, HTTP or another TFTP
server, and shows you what crossed the wire. The documentation is at
<https://jose-pr.github.io/pytftp/>.

```python
import tftp

tftp.TFTPClient("192.0.2.1").download("pxelinux.0", "pxelinux.0")

with tftp.TFTPServer("/srv/tftp") as server:      # IPv6 + IPv4, port 69
    server.serve_forever()
```

## Features

- **Complete option support** — `blksize` up to 65464, RFC 7440 windows,
  `tsize` both ways, `timeout`, tftp-hpa's `utimeout`, `rollover`,
  `blksize2`, `cookie`, and Windows bootmgr's `mstfwindow`. Unlimited file size. A registry for your own options,
  and profiles (`pxe`, `hpa`, `legacy`, `strict`) for quirky peers.
- **Fast** — windowed transfers move hundreds of MiB/s over loopback in pure
  Python (see [benchmarks](https://github.com/jose-pr/pytftp/blob/main/benchmarks/README.md)); the hot path reads into
  preallocated buffers and keeps a sent window in memory instead of re-reading.
- **Correct under loss** — Sorcerer's Apprentice fix (with a window, one
  duplicated, lost or late ACK costs at most one window of DATA, never every
  window after it), exponential backoff, gap detection inside a window, repeated OACKs tolerated, ERROR 5 for stray
  packets without disturbing the transfer, dallying on the last ACK. The
  engine is tested over a simulated lossy link and fuzzed.
- **IPv4 and IPv6** — dual-stack listening by default; v4 clients are served
  from plain v4 sockets.
- **Replies from the request's address** — on a multi-homed host or a VIP,
  each transfer answers from the address the request was sent to (pktinfo,
  via [netimps](https://github.com/jose-pr/netimps)); tested on Linux and
  Windows.
- **Safe file serving** — no path escapes `root` (`..`, symlinks, Windows
  device names), uploads are atomic (a failed upload leaves nothing), quotas
  and disk-space checks from `tsize`.
- **Pluggable backends** — a handler is two methods, so boot menus or images
  can be generated per client; built in: a directory, memory, an HTTP(S)
  gateway, and a terminating proxy to another TFTP server. Slow backends apply
  backpressure instead of blocking other transfers.
- **Transparent relay** — forwards requests byte for byte to upstream servers
  (routed by subnet, filename or interface), so unknown extensions still work; blocking
  or on asyncio, with coroutine routes.
- **Capture and debugging** — trace every datagram from the client, server or
  relay; write Wireshark-readable pcap or pcapng; read pcap/pcapng files or a
  live `tcpdump` pipe (both through [pktcap](https://github.com/jose-pr/pktcap)),
  reconstruct each transfer and extract its file, or ask a server for the transfers a
  capture holds again (`pytftp replay`: reads only, unless told to upload too).
- **Directory listings** — an opt-in extension between pytftp peers: `pytftp ls`,
  `TFTPClient.listdir()`, and `iterdir`/`walk`/`glob` on paths; other servers
  simply ignore it.
- **Deployment** — transfer ports pinned to a range for firewalls; per-client
  roots, case-insensitive names and filename remapping, as handlers
  (`tftp.backends`) and as `pytftp serve` flags.
- **asyncio** — `AsyncTFTPClient` and `AsyncTFTPServer` with async handlers and streams.
- **pathlib** — `client.path("boot/x").read_bytes()`, and `tftp://` URLs in
  [pathlib-next](https://github.com/jose-pr/pathlib-next), so
  `UriPath("tftp://h/x").copy("s3://bucket/x")` just works (optional extra).
- **Bounded** — limits on requests, sessions (500 by default), sessions per
  client, idle time (60 s by default), window memory and transfer time;
  counters for metrics.
- **CLI** — `pytftp get|put|ls|serve|relay|capture|replay` with `--json`, `--trace` and
  `--pcap` (optional extra).

## Installation

```bash
pip install tftp            # library
pip install "tftp[cli]"     # plus the pytftp command
```

| Extra | Adds | Needed for |
| --- | --- | --- |
| `cli` | `duho` | the `pytftp` command and `python -m tftp` |
| `path` | `pathlib-next[uri]` | `TFTPPath`, and `tftp://` URLs in `pathlib_next.uri.UriPath` |

Requires Python 3.9+. The required dependencies are
[`netimps`](https://github.com/jose-pr/netimps), which has none of its own,
and [`pktcap`](https://github.com/jose-pr/pktcap), which needs only `netimps`.

## Quick start

Client:

```python
import tftp

client = tftp.TFTPClient("boot.example.net", blksize=1428, windowsize=16)
result = client.download("images/vmlinuz", "vmlinuz")
print(result.bytes, result.duration, result.negotiated)

data = client.get("pxelinux.cfg/default")          # into memory
client.upload("logs/boot.txt", b"ok\n")            # needs a writable server
```

A `tftp://` URL names a file, and may carry transfer options in either
spelling: `?name=value&name=value`, or `;name=value;name=value` (RFC 3617's own
`;mode=netascii` is the second). Options in a URL are this library's extension:
another tool reads the text after `?` as part of the file name.

```python
import tftp

tftp.download_url("tftp://boot.example.net/images/vmlinuz?blksize=1428&windowsize=16", "vmlinuz")
tftp.download_url("tftp://boot.example.net/images/vmlinuz;blksize=1428;windowsize=16", "vmlinuz")
tftp.download_url("tftp://boot.example.net/images/vmlinuz;blksize=1428", "vmlinuz", blksize=512)  # 512 wins
```

Server with uploads, a bounded window and a completion hook:

```python
import tftp

server = tftp.TFTPServer(
    "/srv/tftp",
    host="::",
    port=69,
    writable=True,
    options=tftp.TFTPServerOptions(max_blksize=8192, max_windowsize=32),
    on_complete=lambda r: print(r.operation, r.filename, r.peer[0], r.error or "ok"),
)
server.serve_forever()
```

Generated content:

```python
import io
import tftp

class Menu(tftp.FilesystemBackend):
    def open_read(self, context):
        if context.filename == "menu.cfg":
            return io.BytesIO(b"client %s\n" % context.peer[0].encode())
        return super().open_read(context)

tftp.TFTPServer(Menu("/srv/tftp")).serve_forever()
```

Relay, capture and asyncio (the commands are under "Command line"):

```python
import tftp
from tftp.relay import TFTPRelay, RouteTable, by_subnet
import pktcap
from tftp.capture import analyze, trace_to

# Forward requests to per-subnet boot servers, recording everything.
with pktcap.PcapWriter("relay.pcap") as pcap:
    route = RouteTable([by_subnet({"10.1.0.0/16": "10.1.0.5"})], default="10.0.0.20")
    TFTPRelay(route, trace=trace_to(pcap)).serve_forever()

# What happened in a capture (yours, or tcpdump's)?
for transfer in analyze("boot.pcapng").transfers:
    print(transfer, transfer.missing_blocks)

# asyncio
from tftp.client import AsyncTFTPClient

async def fetch():
    async for chunk in AsyncTFTPClient("192.0.2.1").stream("vmlinuz"):
        ...
```

## Command line

`pip install "tftp[cli]"` installs the `pytftp` command; `python -m tftp` is the same.
`get`, `put` and `ls` are the client, `serve` and `relay` the servers, `capture` and
`replay` read a capture. The commands, every option and the output formats are in
[the command-line guide](https://jose-pr.github.io/pytftp/cli/).

```bash
pytftp get 192.0.2.1 pxelinux.0 -b 1428 -w 16
pytftp put 192.0.2.1 firmware.bin
pytftp serve /srv/tftp --port 6969 --write --json
pytftp serve /srv/tftp --write --max-upload 1000000 --max-duration 600
pytftp serve /srv/tftp --per-client-only
```

A command exits 0 on success, 1 when the transfer, the peer or a local file
failed (one `error:` line on stderr) and 2 when the invocation was wrong. A
result goes to stdout (`get` and `put` print one line, which `-q` silences; on
stderr when `get HOST FILE -` sends the file to stdout) and `--json` prints one
JSON object per line instead. `--per-client` serves `ROOT/<client address>/` to
a client that has one and `ROOT` to the rest, other clients' directories
included: it is not isolation, and `--per-client-only` refuses a client with no
directory. `--max-upload BYTES` and `--max-duration SECONDS` bound an upload and
a transfer. `pytftp --help` shows each option's default, and `AGENT_HELP=1
pytftp --help` prints the command tree as JSON.

```bash
pytftp serve --http https://images.example.com/pxe/ --compat pxe
pytftp relay 10.0.0.20 --route-prefix windows/=wds.lan --trace
tcpdump -i eth0 -U -w - udp | pytftp capture - --filter "op=RRQ,ERROR"
pytftp replay boot.pcapng 192.0.2.1 --speed 10
```

pktcap's own commands read TFTP too once they are told to load it: `tftp.capture` is a pktcap plugin
that adds the filter keys `op`, `file`, `block` and `code`. Only the datagrams to or from the request
port carry the layer, so a transfer is followed with `pytftp capture`.

```bash
PKTCAP_PLUGINS=tftp.capture pktcap convert -i boot.pcapng -f "op=RRQ and file=*.efi"
```

## API overview

| Module | Purpose |
| --- | --- |
| `tftp.client` | `TFTPClient`, `AsyncTFTPClient`, `download`, `upload` |
| `tftp.server` | `TFTPServer`, `AsyncTFTPServer`, `TFTPServerLimits`, `AtomicWriter`, `TFTPRequestContext` |
| `tftp.backends` | `FilesystemBackend`, `MemoryBackend`, `HTTPBackend`, `UpstreamBackend` (proxy), `Pipe` |
| `tftp.relay` | `TFTPRelay`, `AsyncTFTPRelay` and routing helpers |
| `tftp.capture` | trace events, `trace_to`, `FlowTracker`, `analyze`, filters, `replay_transfers`, and TFTP as a pktcap layer (`TFTPLayer`, `register_tftp_dissector`, `pktcap_plugin`); reading and writing a capture are pktcap's |
| `tftp.options` | `TFTPServerOptions`, option registry, profiles, `Negotiated` |
| `tftp.packet` | `TFTPOpcode`, `TFTPErrorCode`, packet types, `encode_*`/`decode` |
| `tftp.transfer` | I/O-free `Sender`/`Receiver`/`Requester` engine |
| `tftp.listing` | the `x-list` directory listing format: `loads`, `dumps`, `DirectoryListing` |
| `tftp.path` | `TFTPPath`, `TFTPURIPath` (`path` extra) |
| `tftp.netascii` | streaming netascii translation |
| `tftp.exceptions` | every exception: `TFTPError`, the typed `RemoteError` subclasses, `TFTPValueError` and its decode subclass |
| `tftp.cli` | the `pytftp` command (`cli` extra) |

`tftp` itself exports what the common task needs (the clients and servers and
their asyncio twins, `download` and `upload`, the exceptions, `TransferResult`,
`TFTPURL`, the handler contract, the policy classes, the packet types and the
engine's `Sender` and `Receiver`); every other name is imported from the module
above that owns it. The complete reference, with every signature and gotcha and
the module each name lives in, is [`src/tftp/AGENTS.md`](https://github.com/jose-pr/pytftp/blob/main/src/tftp/AGENTS.md)
and the headers beside the code it lists; they ship inside the package.

## Differences from tftp-hpa

The reference for the options only an implementation defines (`blksize2`,
`utimeout`, `rollover`, `cookie`) is **tftp-hpa 5.3**'s server. The suite
replays what it answered to 22 requests, recorded on Linux by
`tests/conformance/record.py` and stored under `tests/conformance/cases/`, so a
run on any system, with no peer installed, checks them. Fidelity is to results:
which options a server answers and with what values, the error codes, the block
numbers and the octets of each block; message texts and the ports a reply comes
from are not compared. The library is tested against tftp-hpa 5.3 (server and
client), BusyBox 1.37, dnsmasq 2.92, curl and iPXE where those can run.

It differs on purpose in these cases, each asserted by the suite as this library
behaves:

| Case | Difference |
| --- | --- |
| `blksize2-below-the-minimum` | `blksize2` below 8 octets: tftp-hpa answers ERROR 8; this library leaves the option out and runs at 512, which RFC 2347 allows for a value a server cannot use. |
| `blksize2-beside-a-smaller-blksize` | `blksize` and `blksize2` together: tftp-hpa answers both, even with different sizes, and sends blocks of the `blksize2` one; this library answers `blksize` alone, one block size to a transfer. |
| `blksize2-beside-an-odd-blksize` | `blksize` and `blksize2` of 1000: tftp-hpa answers `blksize` 1000 and `blksize2` 512 and sends blocks of 512; this library answers `blksize` 1000 alone and sends blocks of 1000. |
| `blksize2-beside-the-same-blksize` | `blksize` and `blksize2` of 1024: tftp-hpa answers both; this library answers `blksize` alone. |
| `cookie-is-echoed` | `cookie` on a read: tftp-hpa 5.3 does not know the option and leaves it out; this library, with it allowed, answers it unchanged, as tftp-hpa's development source does. |
| `cookie-on-a-write` | `cookie` on a write: tftp-hpa 5.3 answers ACK 0; this library, with the option allowed, answers an OACK that carries it unchanged. |
| `cookie-of-300-octets` | A `cookie` of 300 octets: tftp-hpa 5.3 leaves it out; this library refuses the request with ERROR 4, an option value being limited to 255 characters (`max_option_length`). |
| `rollover-not-zero-or-one` | `rollover` of 2: tftp-hpa 5.3 answers it as asked; this library leaves out a value that is not 0 or 1. |
| `tsize-of-an-empty-file` | `tsize` asked of an empty file: tftp-hpa answers `tsize` 0; this library leaves it out, since curl rejects `tsize` 0 in an OACK. |
| `utimeout-above-the-maximum` | `utimeout` over 255 seconds: tftp-hpa answers ERROR 8; this library leaves the option out and uses the default time. |
| `utimeout-below-the-minimum` | `utimeout` of 1 microsecond: tftp-hpa answers ERROR 8; this library leaves the option out and uses the default time. |
| `utimeout-beside-a-different-timeout` | `timeout` beside a `utimeout` of another time: tftp-hpa 5.3 answers both; this library answers `utimeout` alone, as tftp-hpa's development source does. |
| `mstfwindow-a-fixed-window` | `mstfwindow` of 31416, the offer of Windows' boot manager: no recording exists, as that boot manager is the reference and no host here can run it; this library answers 27182 and keeps the window at 4, the one fact Serva documents. |

## Development

```bash
py -3.14 -m venv .venv/3.14-nt-arm64          # or python3.14 -m venv .venv/3.14-posix-x86_64
.venv/3.14-nt-arm64/Scripts/pip install -e ".[dev]"
.venv/3.14-nt-arm64/Scripts/python -m pytest -q
.venv/3.14-nt-arm64/Scripts/python -m black --check src tests benchmarks examples
python benchmarks/run.py
```

The suite, its markers, the peers it uses when they are installed and the traps of running it are in
[tests/AGENTS.md](https://github.com/jose-pr/pytftp/blob/main/tests/AGENTS.md); the layout and the
conventions are in [AGENTS.md](https://github.com/jose-pr/pytftp/blob/main/AGENTS.md).

### Releasing

This project follows [Semantic Versioning](https://semver.org/) and keeps a
[`CHANGELOG.md`](https://github.com/jose-pr/pytftp/blob/main/CHANGELOG.md). Pushing a tag matching `v*` triggers the release
workflow: test gate → build (checking the tag names the version built) → a strict
docs build as a gate → GitHub release → publish. The release workflow never deploys
the docs site itself: for a final release its last job dispatches the docs workflow
at the tag, which owns every Pages deploy.

## License

MIT — see [LICENSE](https://github.com/jose-pr/pytftp/blob/main/LICENSE).
