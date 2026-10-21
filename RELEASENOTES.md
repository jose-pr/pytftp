# Release notes

The detailed companion to [CHANGELOG.md](CHANGELOG.md): benchmark history,
caveats and the evidence behind each release.

## [0.2.1] - 2026-10-22

A dependency release: the `cli` extra requires `duho>=0.7.8,<0.8`, and the command line runs through
`duho.run` in place of its own handling of errors and a closed stdout. The statuses and the lines of
every error are the ones of 0.2.0. Two things differ: `--help` answers when the settings file cannot be
used, and Ctrl-C ends a command that does not stop on it itself with status 130 and no traceback.

### How it was checked

Measured on 2026-10-22 at the commit before this one, with netimps 0.4.1, pktcap 0.1.1 and duho 0.7.8,
each as published. The same 47 command lines (statuses, stdout, stderr, with and without
`DUHO_TRACEBACK`, a closed stdout) were run before and after the change; the only differences are the
four `--help` runs with an unusable settings file.

- **Windows on ARM64:** 3,388 passed, 84 skipped, 2 expected failures on Python 3.14; 3,357 passed, 115
  skipped, 2 expected failures on Python 3.9.
- **Linux** (Fedora 44 under WSL2), Python 3.14 and 3.9: 3,219 passed, 97 skipped, 2 expected
  failures.
- Not run for this release: the hosted matrix, the Intel macOS and FreeBSD hosts.

## [0.2.0] - 2026-10-15

`tftp.path` moves from `pathlib-next` to its successors, `pathapi` and `uripath` (published as
`pathapi-uri`), which `pathlib-next` 0.9 cannot be installed beside. A MINOR release because the
documented path API changed: the URI path is `uripath.URIPath("tftp://...")`, `unlink()` always
raises, `move()` from a TFTP path refuses before copying, and `stat()` can say a size is unknown.
It also fixes a failed or abandoned upload replacing the server's file. Nothing outside `tftp.path`
changed.

### How it was checked

Measured on 2026-10-15 at commit `6c27e86` (the code of this release; the commits after it change
the changelog, a docs page and the wheel smoke test), with pathapi 0.1.0, pathapi-uri 0.1.0,
netimps 0.4.1, pktcap 0.1.1 and duho 0.7.4, each as published.

- **Windows on ARM64:** 3,385 passed, 84 skipped, 2 expected failures on Python 3.14; 3,354 passed,
  115 skipped, 2 expected failures on Python 3.9.
- **Linux** (Fedora 44 under WSL2), Python 3.14 and 3.9: 3,216 passed, 97 skipped, 2 expected
  failures.
- **The hosted matrix**, the same 21 jobs as for 0.1.1, at `4f4d881`: green.
- Not run for this release: the Intel macOS and FreeBSD hosts.

## [0.1.1] - 2026-10-14

A dependency release: the `cli` extra requires `duho>=0.7.4,<0.8`. With it every error of a settings
file is one line and status 2; a key nobody has no longer prints the usage lines first. Nothing else
differs from 0.1.0.

### How it was checked

Measured on 2026-10-14 at commit `5086674`, with netimps 0.4.1, pktcap 0.1.1, duho 0.7.4 and
pathlib-next 0.9.14, each as published.

- **Windows on ARM64:** 3,363 passed, 84 skipped, 2 expected failures on Python 3.14; 3,332 passed, 115
  skipped, 2 expected failures on Python 3.9.
- **Linux** (Fedora 44 under WSL2), Python 3.14 and 3.9: 3,194 passed, 97 skipped, 2 expected failures.
- **Intel macOS 15.7**, Python 3.9: 3,167 passed, 124 skipped, 2 expected failures. **FreeBSD 16.0**,
  Python 3.11, an unprivileged account: 3,163 passed, 128 skipped, 2 expected failures.
- **The hosted matrix**, the same 21 jobs as for 0.1.0: green.

## [0.1.0] - 2026-10-12

### Upgrading from 0.0.0

This release breaks the documented API: the changelog's "Renamed" table maps every old name to its
new one, "Removed" lists what is gone and "Changed" has the detail of each behaviour. No old name is
kept as an alias and there is no deprecation period, so an upgrade is a search and replace and a read
of this list.

- **Dependencies.** `netimps>=0.4.1,<0.5` (0.3 is no longer supported) is the one required dependency; the
  `cli` extra is `duho>=0.7.3,<0.8`, the `path` extra `pathlib-next[uri]>=0.9.13,<0.10` and the `pktcap`
  extra `pktcap>=0.1.0,<0.2`, which `pytftp capture`, `pytftp replay`, `--pcap` and reading a capture
  need (`pip install "tftp[cli,pktcap]"`); without it they say so and nothing else changes.
- **Names.** Acronyms are upper case and every role is named for what it is: `TFTPClient`,
  `TFTPServer`, `TFTPRelay`, `TFTPHandler`, `FilesystemBackend`, `TFTPError`, `TFTPURL`,
  `RequestPacket`, `TransferTimeoutError`. Every exception lives in `tftp.exceptions`.
- **Imports.** `tftp` exports what the common task needs; every other name has one home, its role
  module (`tftp.client`, `tftp.server`, `tftp.relay`, `tftp.capture`, `tftp.options`, `tftp.packet`,
  `tftp.backends`, `tftp.path`, `tftp.exceptions`, `tftp.cli`) or one of the topic modules
  `tftp.transfer`, `tftp.listing` and `tftp.netascii`. The modules below them are private.
- **Listening.** The servers and the relays take `listen=` (a netimps listen specification: an address, an
  adapter name, `"*"`, or several) where they took `host=` and `interface=`, and `pytftp serve` and
  `pytftp relay` read `--listen` in that grammar where they had `--interface`.
- **Calls.** Options are keyword-only, constructors check their arguments and raise `TypeError` or
  `ValueError` (`TFTPValueError` for malformed text) instead of failing later, the client's
  whole-transfer limit is `deadline=`, and a server opens nothing in its constructor: `bind()`,
  `serve_forever()`, `shutdown()`, `wait_closed()` and `close()` (`aclose()` on the asyncio server) are
  one lifecycle on the three servers.
- **Handlers.** `TFTPServer` takes plain hooks and `AsyncTFTPServer` coroutine hooks; a synchronous
  handler reaches the asyncio server through `tftp.server.ThreadedHandler`.
- **Values.** `TFTPURL`, `Upstream`, `PortRange` and the packet types are frozen values that compare by
  type, not tuples; a `tftp://` URL carries transfer options in either spelling.
- **Capture.** Reading, writing, filtering and live capture are pktcap's, and `pytftp capture` and
  `pytftp replay` are its commands with TFTP loaded (`--input`, `--to`, pktcap's output options and
  statuses; `--json` and `--payload` are `--format json`); `tftp.capture` keeps events, flows and
  analysis and adds `trace_to`, `TFTPLayer`, `follow_transfers` and `replay_transfers`. Its own filter
  (`compile_filter`) is gone.
- **Command line.** The entry point is `tftp.cli.main(argv)`, which returns the exit status; the command
  refuses flag combinations it used to ignore (exit 2), and `pytftp replay` is new. The command takes a
  settings file (`-c FILE` before the command's name, `PYTFTP_CONFIG`, or the user's own `pytftp/pytftp.ini`)
  with a table for each command; every on/off option gains its opposite (`--no-json`, ... and `--tsize`,
  `--options`, `--create` and `--packets` for the options that were `--no-X`), which duho adds to
  any command that has a settings file.
- **Defaults that bound a request.** A server ends a transfer silent for 60 seconds (`max_idle`),
  `MemoryBackend` takes uploads up to 16 MiB, and `max_sessions` is 500 on every platform.

What to type or write differently, each left-hand entry checked against the `v0.0.0` tag (the changelog's
"Renamed" table has the rest):

| 0.0.0 had | This release has |
| --- | --- |
| `Client`, `Server`, `Relay`, `Handler`, `RequestContext` | `TFTPClient`, `TFTPServer`, `TFTPRelay`, `TFTPHandler`, `TFTPRequestContext` |
| `tftp.aio.AsyncClient`, `tftp.aio.AsyncServer`, the package `tftp.aio` | `AsyncTFTPClient`, `AsyncTFTPServer`, from `tftp`, `tftp.client` and `tftp.server`; `tftp.aio` is gone |
| `tftp.errors`, `TftpError`, `TransferTimeout`, `TransferAborted`, `MalformedPacket`, `UnknownTransferId` | `tftp.exceptions`, `TFTPError`, `TransferTimeoutError`, `TransferAbortedError`, `TFTPDecodeError`, `UnknownTransferID` |
| `TftpURL`, `parse_url(url)`, `format_url(host, filename, port, mode)` | `TFTPURL`, `TFTPURL.parse(url)`, `str(TFTPURL(host, port, filename, mode))`; a `TFTPURL` is no longer a tuple |
| `TftpPath`, `TftpUriPath` | `TFTPPath`, `TFTPURIPath` |
| `FileSystemHandler`, `MemoryHandler`, `HttpHandler`, `UpstreamHandler` | `FilesystemBackend`, `MemoryBackend`, `HTTPBackend`, `UpstreamBackend`, in `tftp.backends` |
| `Request`, `Data`, `Ack`, `OptionAck`, `Error`, `Packet` | `RequestPacket`, `DataPacket`, `AckPacket`, `OptionAckPacket`, `ErrorPacket`, `TFTPPacket`; frozen values, not named tuples |
| `ServerOptions`, `ServerLimits`, `Stats` | `TFTPServerOptions`, `TFTPServerLimits`, `TFTPStats`; every field by keyword |
| `tftp.DEFAULT`, `tftp.STRICT`, `tftp.PXE`, `tftp.HPA`, `tftp.LEGACY` | `Profile.DEFAULT`, `Profile.STRICT`, `Profile.PXE`, `Profile.HPA`, `Profile.LEGACY` |
| `Server(root, host="::", interface="eth0")`, and the same on `AsyncServer` and `Relay` | `TFTPServer(root, listen="eth0")`, or `listen=["::", "eth0"]`; no `host=`, no `interface=` |
| `pytftp serve --interface NAME`, `pytftp relay --interface NAME` | `--listen NAME` |
| `Client(max_duration=)`, `Relay(max_lifetime=)`, `Client(local_address=)`, `Relay(upstream_source=)` | `deadline=`, `max_duration=`, `src=`, `upstream_src=` |
| `download(host, filename, dest)`, `upload(host, filename, source)` and the methods of `Client` | the operands are `dst` and `src` |
| `server.stop(timeout)`, `relay.stop(timeout)`, `await async_server.stop()`, `await async_server.close()` | `server.shutdown()` then `server.wait_closed(timeout)`; `await async_server.aclose()` |
| `server.supports_pktinfo`, `server.dual_stack` | `server.has_pktinfo`; `dual_stack` is gone |
| `result.ok`, `transfer.done`, `transfer.stalled`, `captured.complete` | `is_ok`, `is_done`, `is_stalled`, `is_complete` |
| `_tftp_fast_open_`, `_tftp_copies_`, `_tftp_listing_` on a handler | `opens_fast`, `copies_writes`, `lists_directories` |
| `from tftp import PortRange, OptionRegistry, encode_request, MODES, ...` | the role module: `tftp.server`, `tftp.options`, `tftp.packet`, `tftp.client` |
| `tftp.uri`, `tftp.result` | `TFTPURL`, `download_url`, `upload_url` and `TransferResult` from `tftp` |
| `tftp.cli.run`, `pytftp = "tftp.cli:run"` | `tftp.cli.main(argv)`, which returns the status; `pytftp = "tftp.cli:main"` |
| `read_datagrams`, `read_frames`, `FrameDecoder`, `sniff`, `compile_filter`, `FilterError`, `CaptureFormatError`, `PcapWriter` in `tftp.capture` | pktcap's own (`pip install "tftp[pktcap]"`); a pcap of a run is `trace=trace_to(pktcap.PcapWriter(path))` |
| `pytftp capture FILE`, `--json`, `--payload`, `--filter` over trace events | `pytftp capture --input FILE`, `--format json`, pktcap's filter over frames |
| `netimps>=0.3.4,<0.4`, `duho>=0.6.0,<0.7`, `pathlib-next[uri]>=0.9.0,<0.10` | `netimps>=0.4.1,<0.5`, `duho>=0.7.3,<0.8`, `pathlib-next[uri]>=0.9.13,<0.10`, and the new `pktcap>=0.1.0,<0.2` extra |

### Real firmware

Both PXE clients fetch from the server in QEMU (`tests/integration/firmware_boot.py`, measured
2026-10-08 under KVM): iPXE with `blksize=1432` and `tsize`, and EDK2's UEFI PXE client (the OVMF of
pve-edk2-firmware 4.2026.08), which asks for `tsize=0 blksize=1468 windowsize=4`, ends that first
request with ERROR 8 once the OACK has told it the size, and then downloads the boot file in blocks
of 1468 octets and windows of four.

Both were run again after the server moved to listen specifications.

### How it was checked

Measured on 2026-10-12 at commit `e4bad96`, with netimps 0.4.1, pktcap 0.1.0, duho 0.7.3 and
pathlib-next 0.9.14, each as published.

- **Windows on ARM64:** 3,363 passed, 84 skipped, 2 expected failures on Python 3.14; 3,332 passed, 115
  skipped, 2 expected failures on Python 3.9.
- **Linux** (Fedora 44 under WSL2), Python 3.14 and 3.9: 3,194 passed, 97 skipped, 2 expected failures,
  with the real-peer tests against tftp-hpa, BusyBox and dnsmasq run and passing.
- **Intel macOS 15.7**, Python 3.9: 3,167 passed, 124 skipped, 2 expected failures. **FreeBSD 16.0**,
  Python 3.11, an unprivileged account: 3,163 passed, 128 skipped, 2 expected failures.
- **The hosted matrix, 21 jobs, green:** lint and type-check (black, mypy for Linux, macOS and Windows, and
  the typing contract as a consumer would check it); the suite on Ubuntu for Python 3.9 to 3.14 and on
  Windows and macOS for 3.9 and 3.14 (10 jobs); the suite with every dependency at its declared floor on
  3.9; the bare install imported and run on 3.9 and 3.14 (2 jobs); the built wheel installed bare and with
  its extras on Linux, Windows and macOS for 3.9 and 3.14 (6 jobs); and a coverage report, which gates
  nothing.
- **Real firmware:** a UEFI client (OVMF) and iPXE each booted a file from the server over KVM, after the
  server moved to listen specifications (see above).
- The two expected failures are the one contract test of the path classes that TFTP cannot pass (a read of a
  directory answers "file not found"), once for each path class. The skips on each system are what that
  system lacks: behaviour of the other platforms, peers that are not installed, privileges the account does
  not hold (`tests/AGENTS.md` lists them).

## [0.0.0] - 2026-10-03

First release. What it contains is in the changelog.

### Benchmarks

Loopback, 4 MiB in-memory file, server in its own process, median of 7
(baseline) and 9 (0.0.0) transfers; `python benchmarks/run.py`. Windows 11
ARM64, CPython 3.14.7. The baseline (`baseline-0.1.0-…`, recorded before the
version was set to 0.0.0) is the first complete implementation; results are
in `benchmarks/results/`.

| metric | baseline ms | 0.0.0 ms | 0.0.0 MiB/s |
| --- | ---: | ---: | ---: |
| `download/rfc1350` | 266.6 | 267.3 | 15 |
| `upload/rfc1350` | 257.9 | 272.2 | 15 |
| `download/blksize1428` | 101.5 | 105.0 | 38 |
| `upload/blksize1428` | 110.7 | 70.8 | 57 |
| `download/blksize8192` | 24.3 | 17.2 | 232 |
| `upload/blksize8192` | 27.3 | 18.3 | 218 |
| `download/blksize1428-w16` | 34.5 | 29.3 | 137 |
| `upload/blksize1428-w16` | 33.1 | 27.3 | 146 |
| `download/blksize8192-w16` | 11.8 | 9.4 | 428 |
| `upload/blksize8192-w16` | 9.2 | 8.7 | 458 |
| `download/blksize65464-w8` | 5.2 | 3.7 | 1087 |
| `upload/blksize65464-w8` | 3.8 | 3.4 | 1167 |

- No metric is more than 6% slower than the baseline. The faster ones (up to
  36%) are not claimed as improvements: repeated runs of identical code on this
  machine vary by a similar amount.
- Lock-step speed is bound by system calls and wakeups, not Python: about 34 us
  per block round trip, of which the sends alone take about 18 us. Use
  `windowsize` for throughput.
- Against tftpy on Linux (`benchmarks/compare_tftpy.py`, lock-step, no
  `windowsize`): within about 10%.

### Caveats

- Local runs on one desktop, not CI. Two runs of identical code under WSL2
  differed by up to 24%; treat differences under that as noise.
- Loopback only: no network latency, no loss.

### Validation

- Tests: 488 passed / 18 skipped on Windows (CPython 3.14 and 3.9), 501 passed
  / 5 skipped on Linux (CPython 3.14 and 3.9). Skips are interop peers not
  installed on Windows, and platform-specific cases.
- Interop: curl, tftp-hpa, BusyBox, dnsmasq (Linux); iPXE network boot in
  QEMU. UEFI (EDK2) PXE not yet verified.
- `mkdocs build --strict`, `black --check`, `twine check`: clean. Built sdist
  and wheel checked: license metadata (MIT), `py.typed`, shipped `AGENTS.md` and
  `README.md`, no local override files.

### Prerequisites and state

- Requires `netimps` 0.3.4 or later within 0.3 (on PyPI); 0.3.4 fixed binding
  and replying on link-local IPv6 addresses. Tested against the published
  0.3.4 at the latest dependency versions (CPython 3.14) and at every declared
  floor (CPython 3.9: `duho` 0.6.0, `pathlib-next` 0.9.0), on Windows and
  Linux.
- PyPI Trusted Publishing must be registered for the `tftp` project and the
  release workflow.
- Published 2026-10-03 from tag `v0.0.0`: PyPI (wheel and sdist), GitHub
  release, docs site. CI on Linux, Windows and macOS (CPython 3.9 and 3.14,
  plus 3.10-3.13 on Linux) and the dependency-floor job passed before tagging.
