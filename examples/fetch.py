"""Download a file with large blocks and a window, printing progress.

python examples/fetch.py 192.0.2.1 pxelinux.0 [port]
"""

import sys

import tftp

host, name = sys.argv[1], sys.argv[2]
port = int(sys.argv[3]) if len(sys.argv) > 3 else 69


def progress(done, total):
    if total:
        print("\r%5.1f%%" % (100.0 * done / total), end="", flush=True)


client = tftp.TFTPClient(host, port, blksize=1428, windowsize=16)
result = client.download(name, name.replace("\\", "/").rsplit("/", 1)[-1], progress=progress)
print("\n%d bytes in %.2fs, %r" % (result.bytes, result.duration, result.negotiated))
