# Serving files

## A directory

```python
import tftp

server = tftp.TFTPServer("/srv/tftp", host="::", port=69, writable=True, overwrite=False)
server.serve_forever()
```

`FilesystemBackend` maps a filename to a path under the root:

- a leading `/` is stripped, and `\` is a separator too (Windows boot loaders
  send `\boot\bcd`);
- `..` is refused, and the resolved path, symlinks included, must stay inside
  the root; on Windows drive letters, `:` streams and device names such as
  `CON` are refused as well;
- uploads are written to a temporary file and renamed into place only when
  the last block has arrived, so a failed upload leaves nothing behind;
- `create`, `overwrite` and `max_upload` decide which uploads are accepted,
  and an announced `tsize` larger than the free space is refused up front.

## Generated content

A handler is any object with `open_read(context)` and
`open_write(context, size)`. Subclass `FilesystemBackend` to generate some
files and serve the rest from disk:

```python
import io
import tftp

class BootMenu(tftp.FilesystemBackend):
    def open_read(self, context):
        if context.filename == "pxelinux.cfg/default":
            return io.BytesIO(b"DEFAULT linux\n# for %s\n" % context.peer[0].encode())
        return super().open_read(context)
```

`context` carries the parsed request, the client's address, and the address
and interface the request arrived on. Raise `tftp.TFTPError(code, message)`
to refuse with a specific ERROR, or let an `OSError` through to have it
mapped by errno.

Handlers run on the server's single event-loop thread, so they should return
quickly.

## Composing handlers

Handlers wrap each other: one that serves each client its own directory is
a few lines (`pytftp serve --per-client` does the same):

```python
import os
import tftp

class PerClientRoot:
    def __init__(self, root, **options):
        self.root, self.options = root, options

    def _pick(self, context):
        own = os.path.join(self.root, context.peer[0].replace(":", "-"))
        return tftp.FilesystemBackend(own if os.path.isdir(own) else self.root, **self.options)

    def open_read(self, context):
        return self._pick(context).open_read(context)

    def open_write(self, context, size):
        return self._pick(context).open_write(context, size)
```

Renaming requests (`pytftp serve --remap`) or matching names whatever their
case (`--ignore-case`) follow the same pattern: rewrite the request, or
override `FilesystemBackend.resolve`.

## Behind a firewall

Every transfer runs on its own UDP port. Pin them to a range a firewall can
allow:

```python
tftp.TFTPServer("/srv/tftp", port_range=(50000, 50100))
```

(`pytftp serve --port-range 50000:50100`; the relay takes the same.) A
request arriving with every port in use is answered "server busy".

## One network adapter

```python
tftp.TFTPServer("/srv/tftp", interface="eth0")   # eth0's IPv4 address
```

`interface` takes a name, a `netimps.Interface`, a MAC or one of the
adapter's addresses (`pytftp serve --interface eth0`). It binds one address
of that adapter, IPv4 unless `host="::"`; requests to its other addresses are
not received.

## Directory listings

`TFTPServerOptions(allowed=tftp.STANDARD_OPTIONS | tftp.LISTING_OPTIONS)` (or
`pytftp serve --listing`) lets pytftp clients list directories and see
modification times; see [Paths](paths.md).

## Watching transfers

```python
def report(result):
    print(result.operation, result.filename, result.peer[0], result.bytes, result.error or "ok")

tftp.TFTPServer("/srv/tftp", on_complete=report).serve_forever()
```

`on_complete` sees every transfer, including refused and failed ones.

## Running in the background

```python
server = tftp.TFTPServer("/srv/tftp", port=0).start()   # binds, then serves on a daemon thread
print(server.server_address)
...
server.shutdown()      # ask it to stop; returns at once
server.wait_closed()   # block until it has
server.close()         # release the sockets; final
```

The constructor opens nothing. `bind()` opens the socket (`start()`,
`serve_forever()` and `with server:` call it), so a privileged port can be taken
before dropping privileges, and `server_address` is `None` until then. A handler
that wants the server down calls `shutdown()`, never `close()`.
