"""Serve a directory over TFTP, read-only, on IPv4 and IPv6.

python examples/serve_directory.py /srv/tftp 6969
"""

import logging
import sys

import tftp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
root = sys.argv[1] if len(sys.argv) > 1 else "."
port = int(sys.argv[2]) if len(sys.argv) > 2 else 69

with tftp.TFTPServer(root, "::", port) as server:
    print("serving %s on %s (pktinfo: %s)" % (root, server.server_address[:2], server.has_pktinfo))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
