# tftp

A **pure-Python TFTP client and server for IPv4 and IPv6** that implements the
whole of modern TFTP: RFC 1350, the option extension (RFC 2347) with
`blksize`, `timeout`, `tsize` and `windowsize`, plus `utimeout`, block-number
rollover and netascii. One event-loop thread serves any number of transfers,
each answered from the address the client used, and the transfer engine is
tested over a simulated lossy link.

## Installation

```bash
pip install tftp            # library
pip install "tftp[cli]"     # plus the pytftp command
```

| Extra | Adds | Needed for |
| --- | --- | --- |
| `cli` | `duho` | the `pytftp` command and `python -m tftp` |

## 30-second tour

```python
import tftp

# Download with large blocks and an RFC 7440 window.
client = tftp.Client("192.0.2.1", blksize=1428, windowsize=16)
result = client.download("pxelinux.0", "pxelinux.0")
print(result.bytes, result.throughput, result.negotiated)

# Serve a directory on IPv6 and IPv4, accepting uploads.
with tftp.Server("/srv/tftp", writable=True) as server:
    server.serve_forever()
```

```bash
pytftp get 192.0.2.1 pxelinux.0 -b 1428 -w 16
pytftp serve /srv/tftp --write --json
```

## Learn more

- [Protocol coverage](protocol.md) — which RFCs and options, and the
  behaviour under loss.
- [Serving files](serving.md) — handlers, containment, uploads, generated
  content.
- API reference: [Client](api/client.md), [Server](api/server.md),
  [Options](api/options.md), [Packets](api/packet.md),
  [Transfer engine](api/transfer.md).
- [Changelog](changelog.md)
