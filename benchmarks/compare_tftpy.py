"""Download throughput of pytftp against tftpy, each library talking to itself.

Both servers serve the same file from the same temporary directory (so both
read from disk), each in its own process; both clients write to memory. Only
options tftpy also supports are used (no ``windowsize``). Run on Linux:
tftpy's Windows support is incomplete.

    python benchmarks/compare_tftpy.py --tftpy-path ~/dev/tftpy

A local run is a sanity check, not a release claim.
"""

from __future__ import annotations

import argparse
import io
import logging
import multiprocessing
import os
import socket
import statistics
import sys
import tempfile
import time

CASES = [("rfc1350", None), ("blksize1428", 1428), ("blksize8192", 8192)]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve_pytftp(root: str, port: int) -> None:
    import tftp

    tftp.TFTPServer(root, "127.0.0.1", port).serve_forever()


def _serve_tftpy(root: str, port: int) -> None:
    import tftpy

    logging.getLogger("tftpy").setLevel(logging.ERROR)
    tftpy.TftpServer(root).listen("127.0.0.1", port)


def _pytftp_download(port: int, blksize, size: int) -> None:
    import tftp

    sink = io.BytesIO()
    tftp.TFTPClient("127.0.0.1", port, blksize=blksize, tsize=False, timeout_option=False).download(
        "f.bin", sink
    )
    assert sink.tell() == size


def _tftpy_download(port: int, blksize, size: int) -> None:
    import tftpy

    sink = io.BytesIO()
    options = {"blksize": blksize} if blksize else {}
    tftpy.TftpClient("127.0.0.1", port, options=options).download("f.bin", sink)
    assert sink.tell() == size


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tftpy-path", help="directory containing the tftpy package")
    parser.add_argument("--size", type=int, default=1 << 20, help="file bytes (default 1 MiB)")
    parser.add_argument("--samples", type=int, default=7)
    args = parser.parse_args()
    if args.tftpy_path:
        sys.path.insert(0, os.path.expanduser(args.tftpy_path))
        os.environ["PYTHONPATH"] = os.pathsep.join(filter(None, [sys.path[0], os.environ.get("PYTHONPATH")]))
    import tftpy  # noqa: F401 -- fail early with a clear ImportError

    logging.getLogger("tftpy").setLevel(logging.ERROR)
    root = tempfile.mkdtemp()
    with open(os.path.join(root, "f.bin"), "wb") as handle:
        handle.write(os.urandom(args.size))

    print("%-14s %14s %14s %8s" % ("case", "pytftp ms", "tftpy ms", "ratio"))
    for name, blksize in CASES:
        medians = []
        for serve, download in ((_serve_pytftp, _pytftp_download), (_serve_tftpy, _tftpy_download)):
            port = _free_port()
            server = multiprocessing.Process(target=serve, args=(root, port), daemon=True)
            server.start()
            time.sleep(0.5)
            try:
                download(port, blksize, args.size)  # warm up
                times = []
                for _ in range(args.samples):
                    start = time.perf_counter()
                    download(port, blksize, args.size)
                    times.append((time.perf_counter() - start) * 1000)
                medians.append(statistics.median(times))
            finally:
                server.terminate()
                server.join(5)
        print("%-14s %14.1f %14.1f %7.1fx" % (name, medians[0], medians[1], medians[1] / medians[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
