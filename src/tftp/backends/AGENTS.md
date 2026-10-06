# `tftp.backends` — public API header

Header-file-style reference for the `tftp.backends` package: the ready-made
handlers a server serves from, and the three that wrap another. Every public
export with its signature, arguments, contract and gotchas, so the package can
be used without reading its source. It ships inside the package and is
self-contained; the top header is `tftp/AGENTS.md`, and the handler contract
they implement is in `tftp/server/AGENTS.md`. Development documentation lives
with the source at <https://github.com/jose-pr/pytftp>.

## Directory backends (`tftp.backends`)

```python
FilesystemBackend(
    root, *, writable=False, create=True, overwrite=False, backslash=True, max_upload=None
)
FilesystemBackend.resolve(filename)
```

`FilesystemBackend` is also exported from `tftp`.

- Serves files under `root` (which must exist). Leading `/` is stripped, so
  `/boot/x` is `root/boot/x`. With `backslash`, `\` is a separator too (Windows
  boot loaders send `\boot\bcd`).
- **Containment**: `..` components are refused (ERROR 2), and the resolved
  path, symlinks included, must stay inside `root` (ERROR 2). On Windows,
  drive letters, `:` streams, reserved device names (`CON`, `NUL`, `CONOUT$`, ...,
  with an extension or trailing spaces too) and a component ending in a dot or
  a space (the file system would open another name) are refused too.
- Reads: a missing file is ERROR 1, and so is a directory — unless
  `context.listing`, when a directory (`""`, `.` or `/` for the root) is
  answered with a `DirectoryListing`.
- Writes: refused unless `writable` (ERROR 2); an existing file needs
  `overwrite` (else ERROR 6); a new file needs `create` (else ERROR 1); the
  directory must exist (directories are never created). An announced `tsize`
  above `max_upload` or the free disk space is ERROR 3, and so is an upload
  that grows past `max_upload`.
- Uploads go through `AtomicWriter`: **a failed or partial upload never
  appears or replaces anything**.
- `resolve(filename) -> str` exposes the mapping (raises `TFTPError`).

Three handlers compose with it and with each other
(`TFTPServer(Remap(PerClient(root, make), rules))`); each is a plain
`TFTPHandler` and none can take a request outside the directory its
`FilesystemBackend` serves:

```python
Remap(inner, rules)
Remap.rewrite(filename)
PerClient(root, make, *, fallback=True)
PerClient.directory(peer)
PerClient.handler_for(context)
CaseInsensitive(
    root, *, writable=False, create=True, overwrite=False, backslash=True, max_upload=None
)
normalize_name(filename, backslash=True)
```

**`Remap`** — rewrites each requested name with the first rule
that matches, then asks `inner`. A rule is `"REGEX=REPLACEMENT"` text (split at
the first `=`) or a `(pattern, replacement)` pair; the first rule whose regex
is found in the name replaces every match in it (`re.sub`, `\1` is a group).
The listing flag and the arrival interface of the request are kept.
`rewrite(name)` shows the mapping; `rules` is a tuple of compiled pairs. A rule
without `=` or a regex that does not compile is `TFTPValueError`.

**`PerClient`** — serves `root/<client
address>/` (`:` written `-`: `fe80--1`; an IPv4 client of a dual-stack socket
by its IPv4 address) from `make(directory)`, one handler per directory. A
client with no directory is served `root` itself, **including every other
client's directory, so this is a convenience and not isolation**; with
`fallback=False` it is answered ERROR 1 to everything and a client reaches only
its own directory. `PerClient.directory(peer)` is the name, `handler_for(context)`
the handler.

**`CaseInsensitive`** — a
`FilesystemBackend` that finds a name whatever its case (`\Boot\BCD` finds
`boot/bcd`): the exact name wins, else each component is matched
case-insensitively; an upload's last component keeps the requested case.

`normalize_name(filename, backslash=True)` is the key a request's file name maps
to: `/a\b//c` is `a/b/c`.

## Other sources (`tftp.backends`)

```python
MemoryBackend(files=None, *, writable=False, overwrite=True, max_upload=16777216, max_entries=1024)
HTTPBackend(
    base_url=None, *, url_for=None, writable=False, headers=None, timeout=10.0, buffer=1048576,
    opener=None
)
UpstreamBackend(
    upstream, *, client_options=None, buffer=1048576, stall_timeout=30.0, writable=False
)
Pipe(capacity=1048576, size=None)
Pipe.put(data, timeout=None)
Pipe.get(n=65536, timeout=None)
Pipe.read(n=65536)
Pipe.finish(error=None)
Pipe.for_upload()
Pipe.set_result(error=None)
```

- **`MemoryBackend`**
  — serves `files` (name → bytes; names normalised so `/a\\b` is `a/b`);
  uploads replace an entry only when complete. Thread-safe `files` updates.
  What an anonymous peer can make it hold is bounded: an announced `tsize`
  above `max_upload` is ERROR 3 at the request, an upload that grows past it
  is ERROR 3, and an upload to a new name when `max_entries` names are held
  (`files` included) is ERROR 3; a name that exists can still be replaced
  (when `overwrite`). The most it can hold is the product of the two; `None`
  removes a bound.
- **`HTTPBackend`**
  — TFTP-to-HTTP(S) gateway on `urllib`: GET `base_url + quote(name)`
  (a name that is not UTF-8 is percent-encoded as the octets that arrived;
  or `url_for(context)`), streamed; `Content-Length` answers `tsize` (ASCII
  digits only). Requests carry `User-Agent: tftp/<version>` (a header in
  `headers` wins, whatever its case) and a `PUT` carries
  `Content-Type: application/octet-stream`. WRQ → `PUT` when `writable`; the
  final ACK waits for the PUT's response. HTTP 404/410 → ERROR 1, 401/403 → 2,
  409 → 6, 413/507 → 3, other → 0; the response of a failed request is
  closed. `..` in a name → ERROR 2.
  - **Schemes.** `base_url` must be `http` or `https` (`ValueError`), a
    `url_for` URL that is not is refused with ERROR 2, and the default opener
    speaks `http` and `https` only, so a redirect to `ftp:`, `file:` or
    `data:` fails (redirects between `http` and `https` are followed). With
    `opener=` the scheme of `base_url` and of `url_for`'s URLs is yours.
  - **Uploads are exact.** The `PUT` carries the announced `tsize` as its
    `Content-Length` (chunked without one, and for netascii, whose announced
    size is not the size received), and its last octets are sent only when the
    transfer is complete. More octets than announced fail the transfer with
    ERROR 3 and fewer with ERROR 0; the origin then holds nothing and sees no
    second request.
  - **A download reads ahead a window, then a buffer.** Until the peer has
    taken data the gateway has pulled about 16 KiB more than the first window
    from the origin (the origin's own socket buffers come on top); the limit
    grows with what the transfer takes, up to `buffer`. The origin is asked
    once per request, when it arrives: run a gateway that anonymous peers can
    reach with `max_sessions` set.
- **`UpstreamBackend`**
  — terminating proxy: `upstream` is `"host"`, `"host:port"`, an address
  object or `netimps.Host`, `(host, port)`, or `upstream(context)` returning
  one. Each side
  negotiates independently (`client_options` for the upstream `TFTPClient`);
  only bytes cross, through a bounded pipe, so a slow client slows the
  upstream. The RRQ is answered after the upstream answers: its ERROR
  reaches the client with the same code and text, its `tsize` passes
  through. A WRQ's final ACK waits for the upstream's final ACK.
- **`Pipe`** — the bounded byte pipe between a
  worker thread and a transfer, for your own handlers. Transfer side
  (non-blocking, `WouldBlock`): `readinto`, `write`, `close`, `abort`,
  `set_wakeup`. Worker side (blocking): `put(data, timeout)`,
  `get(n, timeout)`/`read(n)`, `finish(error=None)`. `for_upload()` makes
  `close()` wait (`WouldBlock`) until the worker calls
  `set_result(error=None)`, and re-raise its error. `size` answers `tsize`.
