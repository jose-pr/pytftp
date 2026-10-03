"""Loopback throughput benchmark for the tftp client and server.

Serves an in-memory file (no disk in the measurement) from a server in its
own process -- sharing a process would make client and server contend for
one GIL -- and times complete downloads and uploads for several block and
window sizes.
Each metric reports min/median/max milliseconds per transfer over
``--samples`` runs; compare on the median.

    python benchmarks/run.py                 # print a table
    python benchmarks/run.py --save NAME     # also write benchmarks/results/NAME.json
"""

from __future__ import annotations

import argparse
import io
import json
import multiprocessing
import os
import pathlib
import platform
import statistics
import sys
import time

import tftp

CASES = [
    # (name, blksize, windowsize)
    ("rfc1350", None, None),
    ("blksize1428", 1428, None),
    ("blksize8192", 8192, None),
    ("blksize1428-w16", 1428, 16),
    ("blksize8192-w16", 8192, 16),
    ("blksize65464-w8", 65464, 8),
]


class MemoryHandler:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def open_read(self, context):
        return io.BytesIO(self.payload)

    def open_write(self, context, size):
        return io.BytesIO()


def serve(payload: bytes, host: str, ports) -> None:
    with tftp.Server(MemoryHandler(payload), host, 0, writable=True) as server:
        ports.put(server.server_address[1])
        server.serve_forever()


def measure(fn, samples: int) -> dict:
    times = []
    for _ in range(samples):
        start = time.perf_counter()
        fn()
        times.append((time.perf_counter() - start) * 1000)
    return {
        "min_ms": min(times),
        "median_ms": statistics.median(times),
        "max_ms": max(times),
        "samples": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--size", type=int, default=4 << 20, help="payload bytes (default 4 MiB)")
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--save", metavar="NAME", help="write benchmarks/results/NAME.json")
    args = parser.parse_args()

    payload = os.urandom(args.size)
    metrics = {}
    ports: "multiprocessing.Queue[int]" = multiprocessing.Queue()
    server = multiprocessing.Process(target=serve, args=(payload, args.host, ports), daemon=True)
    server.start()
    port = ports.get(timeout=30)
    try:
        for name, blksize, windowsize in CASES:
            client = tftp.Client(args.host, port, blksize=blksize, windowsize=windowsize, timeout=1.0)

            def download():
                sink = io.BytesIO()
                client.download("bench.bin", sink)
                assert sink.tell() == args.size

            def upload():
                client.upload("bench.bin", payload)

            for direction, fn in (("download", download), ("upload", upload)):
                fn()  # warm up
                result = measure(fn, args.samples)
                result["mib_per_s_median"] = args.size / (1 << 20) / (result["median_ms"] / 1000)
                metrics["%s/%s" % (direction, name)] = result
                print(
                    "%-28s median %8.1f ms  %7.1f MiB/s  (min %.1f, max %.1f)"
                    % (
                        "%s/%s" % (direction, name),
                        result["median_ms"],
                        result["mib_per_s_median"],
                        result["min_ms"],
                        result["max_ms"],
                    )
                )

    finally:
        server.terminate()
        server.join(10)

    if args.save:
        out = pathlib.Path(__file__).parent / "results" / ("%s.json" % args.save)
        out.parent.mkdir(exist_ok=True)
        record = {
            "name": args.save,
            "version": tftp.__version__,
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": "%s-%s" % (sys.platform, platform.machine()),
            "payload_bytes": args.size,
            "host": args.host,
            "metrics": metrics,
        }
        out.write_bytes((json.dumps(record, indent=2) + "\n").encode("utf-8"))  # LF on every platform
        print("saved", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
