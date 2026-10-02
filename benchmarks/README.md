# Benchmarks

`run.py` measures loopback throughput: a server in its own process serves an
in-memory payload (4 MiB by default, so no disk is measured) and the client
times complete downloads and uploads for several `blksize`/`windowsize`
combinations.

```bash
python benchmarks/run.py                         # print a table
python benchmarks/run.py --save <name>           # also write results/<name>.json
python benchmarks/run.py --size 1048576 --samples 15 --host ::1
```

Results are committed under `results/`, one file per (version, interpreter,
platform), so a before/after comparison is always recoverable. Compare on the
**median**: a single run on a desktop is noisy, and a local run is a sanity
check, not a release claim.

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
