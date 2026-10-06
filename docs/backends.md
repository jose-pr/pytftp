# Backends

Besides a directory (`FilesystemBackend`), `tftp.backends` has:

## Memory

```python
from tftp.backends import MemoryBackend

files = MemoryBackend({"pxelinux.cfg/default": b"DEFAULT linux\n"}, writable=True)
tftp.TFTPServer(files).serve_forever()       # uploads land in files.files when complete
```

Uploads are bounded: `max_upload` (16 MiB by default) is the most one upload may
hold and `max_entries` (1024, the names already in `files` included) the most names;
a client that crosses either gets ERROR 3, and a name that exists can still be
replaced. Pass `None` to remove a bound.

## HTTP(S) gateway

Boot ROMs speak TFTP; images often live behind HTTP.

```python
from tftp.backends import HTTPBackend

tftp.TFTPServer(HTTPBackend("https://images.example.com/pxe/")).serve_forever()
```

`boot/vmlinuz` is fetched from `https://images.example.com/pxe/boot/vmlinuz`
and streamed: `Content-Length` answers `tsize`, a 404 becomes ERROR 1, a 403
ERROR 2. With `writable=True` uploads become HTTP `PUT`s, and the client's
last block is acknowledged only once the HTTP server has answered. Pass
`url_for=` to route by client or filename, `headers=` for authentication, or
an `opener` for proxies and TLS settings. Only the standard library is used.

The gateway fetches `http` and `https` only: a `base_url` of another scheme is a
`ValueError`, and the default opener refuses a redirect to `ftp:`, `file:` or
`data:` (an `opener` of your own is yours to restrict or widen). An upload's `PUT`
carries the announced size, so a client that sends more or fewer octets than it
announced fails with an ERROR and the origin stores nothing. Run a gateway that
anonymous peers can reach with `max_sessions` set: each request costs the origin
one connection.

## Another TFTP server (terminating proxy)

```python
from tftp.backends import UpstreamBackend

proxy = UpstreamBackend("10.0.0.20", client_options={"blksize": 8192, "windowsize": 16})
tftp.TFTPServer(proxy, options=tftp.Profile.LEGACY.server).serve_forever()
```

Each side negotiates on its own, and they are joined by a bounded buffer: a
slow client slows the upstream rather than the proxy holding the file. The
upstream's errors reach the client with the same code and message, and its
`tsize` passes through. `upstream` may be a callable choosing the server per
request. For forwarding packets unchanged instead, see [Relaying](relay.md).

## Writing your own

A handler returns a reader or writer. If it can be slow, return a
`tftp.backends.Pipe` and fill (or drain) it from a thread: the transfer pauses
while the pipe is empty (or full) and resumes when it is woken, without
blocking anything else. Handlers that block while opening are run in worker
threads automatically; mark fast ones with `opens_fast = True`.
