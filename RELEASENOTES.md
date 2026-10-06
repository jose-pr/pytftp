# Release notes

The detailed companion to [CHANGELOG.md](CHANGELOG.md): benchmark history,
caveats and the evidence behind each release.

## [Unreleased]

Target: no metric of `benchmarks/run.py` is more than 10% slower than the
`baseline-memory-backend` result of the same system and interpreter in
`benchmarks/results/`, run on the median of the same number of samples.

### Upgrading from 0.0.0

This release breaks the documented API: the changelog's "Renamed" table maps every old name to its
new one, "Removed" lists what is gone and "Changed" has the detail of each behaviour. No old name is
kept as an alias and there is no deprecation period, so an upgrade is a search and replace and a read
of this list.

- **Dependencies.** `netimps>=0.4.0,<0.5` (0.3 is no longer supported) and `pktcap>=0.1.0,<0.2` are
  required; the `cli` extra is `duho>=0.6.0,<0.7` and the `path` extra `pathlib-next[uri]>=0.9.0,<0.10`.
- **Names.** Acronyms are upper case and every role is named for what it is: `TFTPClient`,
  `TFTPServer`, `TFTPRelay`, `TFTPHandler`, `FilesystemBackend`, `TFTPError`, `TFTPURL`,
  `RequestPacket`, `TransferTimeoutError`. Every exception lives in `tftp.exceptions`.
- **Imports.** `tftp` exports what the common task needs; every other name has one home, its role
  module (`tftp.client`, `tftp.server`, `tftp.relay`, `tftp.capture`, `tftp.options`, `tftp.packet`,
  `tftp.backends`, `tftp.path`, `tftp.exceptions`, `tftp.cli`) or one of the topic modules
  `tftp.transfer`, `tftp.listing` and `tftp.netascii`. The modules below them are private.
- **Calls.** Options are keyword-only, constructors check their arguments and raise `TypeError` or
  `ValueError` (`TFTPValueError` for malformed text) instead of failing later, the client's
  whole-transfer limit is `deadline=`, and a server opens nothing in its constructor: `bind()`,
  `serve_forever()`, `shutdown()`, `wait_closed()` and `close()` (`aclose()` on the asyncio server) are
  one lifecycle on the three servers.
- **Handlers.** `TFTPServer` takes plain hooks and `AsyncTFTPServer` coroutine hooks; a synchronous
  handler reaches the asyncio server through `tftp.server.ThreadedHandler`.
- **Values.** `TFTPURL`, `Upstream`, `PortRange` and the packet types are frozen values that compare by
  type, not tuples; a `tftp://` URL carries transfer options in either spelling.
- **Capture.** Reading, writing and live capture are pktcap's; `tftp.capture` keeps events, flows,
  analysis, filters and replay, and adds `trace_to`, `TFTPLayer` and `replay_transfers`.
- **Command line.** The entry point is `tftp.cli.main(argv)`, which returns the exit status; the command
  refuses flag combinations it used to ignore (exit 2), and `pytftp replay` is new.
- **Defaults that bound a request.** A server ends a transfer silent for 60 seconds (`max_idle`),
  `MemoryBackend` takes uploads up to 16 MiB, and `max_sessions` is 500 on every platform.

The version in development is 0.1.0; the number of the release is chosen when it is cut.

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
