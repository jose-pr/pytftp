# Release notes

The detailed companion to [CHANGELOG.md](CHANGELOG.md): benchmark history,
caveats and the evidence behind each release.

## [Unreleased]

## [0.0.0] — prepared

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

- Tests: 487 passed / 18 skipped on Windows (CPython 3.14 and 3.9), 500 passed
  / 5 skipped on Linux (CPython 3.14 and 3.9). Skips are interop peers not
  installed on Windows, and platform-specific cases.
- Interop: curl, tftp-hpa, BusyBox, dnsmasq (Linux); iPXE network boot in
  QEMU. UEFI (EDK2) PXE not yet verified.
- `mkdocs build --strict`, `black --check`, `twine check`: clean. Built sdist
  and wheel checked: license metadata (MIT), `py.typed`, shipped `AGENTS.md` and
  `README.md`, no local override files.

### Prerequisites and state

- Needs `netimps` 0.3.3 on PyPI (the declared floor) before CI or a user
  install can resolve dependencies.
- PyPI Trusted Publishing must be registered for the `tftp` project and the
  release workflow.
- Not pushed; no tag.
