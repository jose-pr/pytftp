# tftp

[![Python versions](https://img.shields.io/badge/python-3.9%20%7C%203.14-blue.svg)](https://github.com/jose-pr/pytftp)
[![Docs](https://img.shields.io/badge/docs-latest-blue.svg)](https://jose-pr.github.io/pytftp/)
[![CI](https://img.shields.io/github/actions/workflow/status/jose-pr/pytftp/test.yml)](https://github.com/jose-pr/pytftp/actions/workflows/test.yml)

A **pure-Python TFTP client and server for IPv4 and IPv6** that implements the
whole of modern TFTP: RFC 1350, the option extension (RFC 2347) with
`blksize`, `timeout`, `tsize` and `windowsize` (RFC 2348, 2349, 7440), plus
`utimeout`, block-number rollover and netascii. One event-loop thread serves
any number of transfers, each answered from the address the client used.

```python
import tftp

tftp.Client("192.0.2.1").download("pxelinux.0", "pxelinux.0")

with tftp.Server("/srv/tftp") as server:      # IPv6 + IPv4, port 69
    server.serve_forever()
```

## Features

- **Complete option support** — `blksize` up to 65464, RFC 7440 windows,
  `tsize` both ways, `timeout`/`utimeout`, `rollover`. Unlimited file size.
- **Fast** — windowed transfers move hundreds of MiB/s over loopback in pure
  Python (see [benchmarks](benchmarks/README.md)); the hot path reads into
  preallocated buffers and keeps a sent window in memory instead of re-reading.
- **Correct under loss** — Sorcerer's Apprentice fix, gap detection inside a
  window, ERROR 5 for stray packets without disturbing the transfer, dallying
  on the last ACK. The engine is tested over a simulated lossy link.
- **IPv4 and IPv6** — dual-stack listening by default; v4 clients are served
  from plain v4 sockets.
- **Replies from the request's address** — on a multi-homed host or a VIP,
  each transfer answers from the address the request was sent to (pktinfo,
  via [netimps](https://github.com/jose-pr/netimps)); tested on Linux and
  Windows.
- **Safe file serving** — no path escapes `root` (`..`, symlinks, Windows
  device names), uploads are atomic (a failed upload leaves nothing), quotas
  and disk-space checks from `tsize`.
- **Pluggable** — a handler is two methods, so boot menus or images can be
  generated per client; the I/O-free `Sender`/`Receiver` engine can be driven
  by any transport.
- **CLI** — `pytftp get|put|serve` with `--json` output (optional extra).

## Installation

```bash
pip install tftp            # library
pip install "tftp[cli]"     # plus the pytftp command
```

| Extra | Adds | Needed for |
| --- | --- | --- |
| `cli` | `duho` | the `pytftp` command and `python -m tftp` |

Requires Python 3.9+. The one required dependency, `netimps`, has no
dependencies of its own.

## Quick start

Client:

```python
import tftp

client = tftp.Client("boot.example.net", blksize=1428, windowsize=16)
result = client.download("images/vmlinuz", "vmlinuz")
print(result.bytes, result.duration, result.negotiated)

data = client.get("pxelinux.cfg/default")          # into memory
client.upload("logs/boot.txt", b"ok\n")            # needs a writable server
```

Server with uploads, a bounded window and a completion hook:

```python
import tftp

server = tftp.Server(
    "/srv/tftp",
    "::",
    69,
    writable=True,
    options=tftp.ServerOptions(max_blksize=8192, max_windowsize=32),
    on_complete=lambda r: print(r.operation, r.filename, r.peer[0], r.error or "ok"),
)
server.serve_forever()
```

Generated content:

```python
import io
import tftp

class Menu(tftp.FileSystemHandler):
    def open_read(self, context):
        if context.filename == "menu.cfg":
            return io.BytesIO(b"client %s\n" % context.peer[0].encode())
        return super().open_read(context)

tftp.Server(Menu("/srv/tftp")).serve_forever()
```

Command line:

```bash
pytftp get 192.0.2.1 pxelinux.0 -b 1428 -w 16
pytftp put 192.0.2.1 firmware.bin
pytftp serve /srv/tftp --port 6969 --write --json
```

## API overview

| Module | Purpose |
| --- | --- |
| `tftp.client` | `Client`, `download`, `upload` |
| `tftp.server` | `Server`, `FileSystemHandler`, `AtomicWriter`, `RequestContext` |
| `tftp.options` | `ServerOptions`, `Negotiated`, negotiation rules |
| `tftp.packet` | `Opcode`, `ErrorCode`, packet types, `encode_*`/`decode` |
| `tftp.transfer` | I/O-free `Sender`/`Receiver` engine |
| `tftp.netascii` | streaming netascii translation |
| `tftp.errors` | `TftpError`, `RemoteError`, `ProtocolError`, `TransferTimeout` |
| `tftp.cli` | the `pytftp` command (`cli` extra) |

Everything is also importable from `tftp` directly. The complete reference,
with every signature and gotcha, is [`src/tftp/AGENTS.md`](src/tftp/AGENTS.md),
which ships inside the package.

## Development

```bash
py -3.14 -m venv .venv/3.14-nt-arm64          # or python3.14 -m venv .venv/3.14-posix-x86_64
.venv/3.14-nt-arm64/Scripts/pip install -e ".[dev]"
.venv/3.14-nt-arm64/Scripts/python -m pytest -q
.venv/3.14-nt-arm64/Scripts/python -m black --check src tests benchmarks examples
python benchmarks/run.py
```

`pytest -m "not slow"` skips the rollover tests; tests marked `interop` run
against curl's TFTP client when curl is on `PATH`. See
[AGENTS.md](AGENTS.md) for the layout and conventions.

### Releasing

This project follows [Semantic Versioning](https://semver.org/) and keeps a
[`CHANGELOG.md`](CHANGELOG.md). Pushing a tag matching `v*` triggers the release
workflow: test gate → build (checking the tag names the version built) → a strict
docs build as a gate → GitHub release → publish. The release workflow never deploys
the docs site itself: for a final release its last job dispatches the docs workflow
at the tag, which owns every Pages deploy.

## License

No license has been chosen yet; until one is, all rights are reserved and the
package is marked as not for upload.
