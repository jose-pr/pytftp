# Benchmarks

`run.py` measures loopback throughput: a server in its own process serves an
in-memory payload from the library's `MemoryBackend` (4 MiB by default, so no
disk is measured) and the client times complete downloads and uploads for
several `blksize`/`windowsize` combinations.

```bash
python benchmarks/run.py                         # print a table
python benchmarks/run.py --save <name>           # also write results/<name>.json
python benchmarks/run.py --size 1048576 --samples 15 --host ::1
```

Results are committed under `results/`, one file per (version, interpreter,
platform), so a before/after comparison is always recoverable. Compare on the
**median**: a single run on a desktop is noisy, and a local run is a sanity
check, not a release claim. On one Windows desktop (2026-10-06) the same code
with the same handler measured up to twice as slow in one session as in another
a few hours apart, so compare results of one session. Results recorded before
the `baseline-memory-backend` ones served from a handler of their own, which
opens on a worker thread where the memory backend does not, so they are not
comparable with later ones.

The `Benchmarks` workflow runs the same script on the three systems when
dispatched by hand and keeps each result as a workflow artifact; shared runners
are noisy, so it never fails on a number.

## Schema

```json
{
  "name": "baseline-0.1.0-win-arm64-py3.14",
  "version": "0.1.0",
  "python": "3.14.7",
  "implementation": "CPython",
  "platform": "win32-ARM64",
  "payload_bytes": 4194304,
  "host": "127.0.0.1",
  "metrics": {
    "download/blksize1428": {
      "min_ms": 96.3, "median_ms": 101.5, "max_ms": 109.9,
      "samples": 7, "mib_per_s_median": 39.4
    }
  }
}
```

Metric names are `<download|upload>/<case>`; the cases are listed in `CASES`
in `run.py`.

## Against tftpy

`compare_tftpy.py` times downloads with pytftp talking to itself and tftpy
talking to itself, on the options both support (no `windowsize`), each server
reading the same file from disk. Linux only:

```bash
python benchmarks/compare_tftpy.py --tftpy-path ~/dev/tftpy
```

## What limits lock-step transfers

Profiled 2026-10-03 (Windows 11 ARM64, Python 3.14, `windowsize` 1, 512-byte
blocks, about 34 us per block round trip): the time is in system calls and
wakeups, not in pytftp's Python. `sendto` costs about 9.6 us on the client and
8.3 us on the server, and the rest is spent in `recvfrom_into`/`select`
waiting for the other side. pytftp's own code is about 1.5 us per block in the
client's receiver and 5-6 us in the server. tftpy, measured the same way on
Linux, is within about 10% in lock-step. The large gains are from windowing
(`windowsize`) and larger blocks, which cut the round trips themselves.
