# `tftp` — public API header

Header-file-style reference for the `tftp` package: every public export with
its signature, contract and gotchas, so the package can be used without
reading its source. It ships inside the package and is self-contained; read it
(or the README beside it) with `importlib.resources.files("tftp")`.
Development documentation lives with the source at
<https://github.com/jose-pr/pytftp>.

Everything below is importable from `tftp` directly. The subpackages only
group it: `tftp.packet` (wire format), `tftp.options` (negotiation),
`tftp.transfer` (the I/O-free engine), `tftp.client`, `tftp.server`,
`tftp.netascii`, `tftp.errors`, `tftp.result`, and `tftp.cli` (needs the `cli`
extra). Modules starting with `_` are internal.

`tftp.__version__` — the installed distribution's version.

## Protocol coverage

| Spec | What | Notes |
| --- | --- | --- |
| RFC 1350 | RRQ, WRQ, DATA, ACK, ERROR; octet and netascii | `mail` mode is refused with ERROR 4 |
| RFC 1123 4.2.3.1 | Sorcerer's Apprentice fix | duplicate ACKs never trigger a resend (windowsize 1) |
| RFC 1123 4.2.3.2 | exponential backoff | each consecutive retransmission waits `backoff` times longer, capped |
| RFC 1123 4.2.3.4 | broadcast requests ignored | server, when pktinfo reports the destination |
| RFC 2347 | option extension, OACK, ERROR 8 | unknown options are ignored, as the RFC requires |
| RFC 2348 | `blksize` 8..65464 | server clamps to its `max_blksize` |
| RFC 2349 | `timeout` (1..255 s), `tsize` | `tsize` 0 is never sent in an OACK (curl rejects it) |
| RFC 7440 | `windowsize` 1..65535 | server clamps to its `max_windowsize` (default 64) |
| tftp-hpa | `blksize2`, `utimeout`, `rollover`, `cookie` | **off unless a server allows them** (`ServerOptions(allowed=...)`, a profile) |
| Microsoft | `mstfwindow` (bootmgr/WDS variable window) | off unless allowed; runs a fixed window of 4 (the in-transfer resize is unpublished) |
| pytftp | `x-list` (directory listing), `x-mtime` (modification time) | off unless allowed (`LISTING_OPTIONS`); other servers ignore them |
| — | block-number rollover | blocks wrap after 65535 (to 0, or 1 with `rollover`); file size is unlimited |
| RFC 1350 §6 | dallying | server re-ACKs a repeated last DATA for one timeout |

Not implemented: RFC 2090 multicast, PXE MTFTP.

## Client

**`Client(host, port=69, *, timeout=1.0, retries=5, blksize=1428, windowsize=None, tsize=True, rollover=None, timeout_option=True, family=0, local_address=None, fallback=True, dally=False, backoff=2.0, max_timeout=None, max_duration=None, strict_source=True, utimeout=False, extra_options=None, registry=None, on_negotiated=None)`**

- `host` — a string (name or address; `"[v6]"`, `"host:port"` and
  `"[v6]:port"` are accepted, and a port written there overrides `port`), an
  `ipaddress` address or interface (its address), or a `netimps.Host`. Kept
  as given in `client.host`. The port is ASCII digits and brackets hold an
  IPv6 literal only: `"h:+70"` and `"[10.0.0.5]"` raise `ValueError`
  (`netimps.NetimpsValueError`) when the transfer starts; a `None` or other
  non-host raises `TypeError`.
- `timeout` — seconds before a retransmission. Requested from the server as
  `timeout` when whole (1..255); a fractional one is requested as
  `utimeout` only with `utimeout=True`, otherwise not at all. Nothing is
  requested with `timeout_option=False`.
- `retries` — retransmissions of one packet before `TransferTimeout`.
- `backoff`, `max_timeout` — each consecutive retransmission (of the
  request too) waits `backoff` times longer, up to `max_timeout` (default
  8 × `timeout`; below `timeout` is a `ValueError`); progress resets the wait. `backoff=1` disables it.
- `max_duration` — seconds a whole transfer may take; `None` is unlimited.
- `strict_source` — the first answer must come from the address the request
  was sent to. `False` accepts a multi-homed server answering from another
  address (the transfer then locks on to that address and port).
- `blksize` — requested; `None` asks for nothing (512). The default 1428 fits
  one Ethernet frame on IPv4 and IPv6. `"mtu"` sizes it to the MTU of the
  interface the route to the server uses (1428 when unknown).
- `on_negotiated(negotiated, peer)` — called once the server has answered
  the request (OACK, first DATA or ACK 0) and **before any data moves**;
  `peer` is the server's transfer address. If it raises, the server is sent
  an ERROR (the `TftpError`'s code, else 0) and the exception propagates.
- `extra_options` — further options to request verbatim (`{"blksize2": 4096}`,
  `{"cookie": "x"}`, custom ones). A known option's answer is validated by
  its handler (`registry`, default `DEFAULT_REGISTRY`); an unknown one's
  answer lands in `result.negotiated.extra`. Naming a built-in twice (here
  and as a keyword) raises `ValueError`.
- `windowsize` — requested RFC 7440 window; `None` asks for nothing (1).
- `tsize` — ask for the size (RRQ) or announce it (WRQ, when the source size
  can be determined).
- `rollover` — request rollover to 0 or 1; `None` asks for nothing, wraps to 0,
  and follows a server that wraps to 1.
- `family` — `socket.AF_INET`/`AF_INET6` to force one; `0` is whatever the host
  resolves to first.
- `local_address` — `(address, port)` to send from; the address in any form
  `host` takes.
- `fallback` — if the server answers the *request* with ERROR 8 (options
  refused), ask again once without options.
- `dally` — after the last ACK of a download, keep re-ACKing a repeated last
  DATA for one timeout. Costs that much time per download.
- Options are validated at construction: an out-of-range `blksize`,
  `windowsize` or `rollover` raises `ValueError` there.
- **Not thread-safe**: one `Client` per thread. Each transfer opens its own
  socket, so sequential transfers on one client are fine.

Methods (each returns a `TransferResult` unless noted):

- **`download(filename, dest, *, mode="octet", progress=None)`** — `dest` is a
  path or a writable binary file. A path is created/truncated and **removed
  again if the transfer fails**.
- **`get(filename, *, mode="octet") -> bytes`** — download into memory.
- **`upload(filename, source, *, mode="octet", progress=None)`** — `source` is
  a path, a readable binary file (`readinto` or `read`), or bytes-like.
- **`put(filename, data, *, mode="octet")`** — upload bytes.
- **`size(filename, *, mode="octet") -> int | None`** — the file's size
  without transferring it: an RRQ asking only for `tsize`, abandoned with
  ERROR 8 once the server answers (a server counts that as `declined`, not
  failed). `None` when the server reports no sizes, unless the file fits in
  the first 512-byte block. Raises like a download (`FileNotFound`...).
- **`stat(filename, *, mode="octet") -> RemoteStat(size, mtime, is_dir)`** —
  one probe like `size()`, also asking for `x-mtime` and `x-list`: a server
  allowing them reports the modification time (seconds since the epoch) and
  recognises a directory (`size` is then `None`); others leave `mtime`
  `None` and report a directory as `FileNotFound`. A request refused with
  ERROR 8 is retried as a plain size probe (with `fallback`). `filename`
  `""` is the root.
- **`listdir(dirname="") -> list[ListEntry(name, is_dir, size, mtime)]`** —
  a directory listing from a server speaking `x-list`. `NotADirectoryError`
  when the name is a file (the transfer is abandoned at once);
  `FileNotFound` when it does not exist — which is also what a server
  without listing support answers for a directory.
- **`path(*segments, mode="octet") -> TftpPath`** — see Paths (needs the
  `path` extra).
- `mode` is `"octet"` or `"netascii"`; `"binary"`/`"ascii"` are aliases. Any
  other value raises `ValueError`.
- `progress(done_bytes, total_or_None)` is called after each packet that moved
  data; `total` is the negotiated `tsize`.
- Raises `RemoteError` (the server sent ERROR), `TransferTimeout`,
  (as the subclass for its code: `FileNotFound`, `AccessViolation`, ...),
  `ProtocolError` (the server broke the protocol, e.g. an OACK with a larger
  `blksize` than requested — the client sends ERROR 8 first), or `OSError` for
  local failures (resolution, the local file).

**`download(host, filename, dest, *, port=69, mode="octet", progress=None, **client_options)`**
and **`upload(host, filename, source, ...)`** — one-shot wrappers;
`client_options` go to `Client`.

**`MODES`** — `("octet", "netascii")`.

## Server

**`Server(root_or_handler, host=None, port=69, *, writable=False, create=True, overwrite=False, timeout=1.0, retries=5, options=None, max_sessions=None, reply_from_request_address=True, dally=True, on_complete=None, limits=None, ignore_broadcast=True, backoff=2.0, max_timeout=None, open_in_thread=None, workers=8, port_range=None, interface=None)`**

- `root_or_handler` — a directory (wrapped in `FileSystemHandler` with
  `writable`, `create`, `overwrite`) or any handler object (see below).
- `host` — `None` (default) or `"::"` listens on IPv6 **and IPv4** with a dual-stack
  socket, falling back to `"0.0.0.0"` on a host without IPv6. `"0.0.0.0"` is
  IPv4 only; a specific address (`"192.0.2.10"`, `"[fe80::1%eth0]"`) listens
  there alone. An `ipaddress` address or `netimps.Host` works too.
- `port` — `0` picks a free port; read it from `server_address`.
- `timeout`, `retries` — per transfer, unless the client negotiates `timeout`.
- `options` — a `ServerOptions` policy.
- `max_sessions` — concurrent transfers; beyond it a request gets ERROR 0
  `"server busy"`. `None` is unlimited, **except on Windows, where it defaults
  to 500** because `select()` handles at most 512 sockets; an explicit value
  above 510 there is a `ValueError` at construction.
- `reply_from_request_address` — each transfer's socket is bound to the
  address the request was sent to (pktinfo), so a multi-homed host or a VIP
  answers from the address the client used. Where the platform cannot report
  it, replies come from the listening address, or from the routing table's
  choice when listening on a wildcard. Check `supports_pktinfo`.
- `dally` — keep a finished upload's socket for one timeout to re-ACK a
  repeated last DATA.
- `on_complete(result)` — called with a `TransferResult` after **every**
  transfer, refused and failed ones included (`result.error` set). Exceptions
  it raises are logged and swallowed.
- `limits` — a `ServerLimits` (below).
- `ignore_broadcast` — silently drop requests addressed to a broadcast
  (limited or subnet) or multicast address. Needs pktinfo to see the
  destination; without it every request looks unicast.
- `backoff`, `max_timeout` — as for `Client`, per transfer.
- `open_in_thread`, `workers` — call the handler's `open_read`/`open_write`
  in a pool of `workers` threads so a handler that blocks (HTTP, an upstream
  server) never stalls other transfers. `None` decides per handler: one
  with `_tftp_fast_open_ = True` (`FileSystemHandler`, `MemoryHandler`)
  opens inline on the loop, anything else in a worker. A retransmitted
  request is still recognised while its open is pending.
- `interface` — listen on one network adapter: a name (`"eth0"`), a
  `netimps.Interface`, its MAC, or one of its addresses. The listener binds
  **one address** of it: the one named, else the adapter's primary IPv4
  address (non-loopback preferred), else its IPv6 one; `host="0.0.0.0"` or
  `"::"` picks the family. Another `host`, or an adapter netimps cannot
  resolve, is a `ValueError`. Requests to the adapter's other addresses are
  not received; run one server per family or address as needed.
- `port_range` — `(low, high)` inclusive, a `range`, or a `PortRange`:
  transfer sockets take their ports from it (round-robin, so a just-released
  port is reused last), for a firewall to allow. With every port taken a
  request gets ERROR 0 `"server busy"` (counted `refused`). `None` lets the
  OS choose.
- Raises `OSError` if the port cannot be bound (port 69 needs privileges on
  POSIX).

Lifecycle:

- **`serve_forever()`** — run the event loop in the calling thread until
  `shutdown()`. One thread serves every transfer.
- **`shutdown()`** — stop `serve_forever`; safe from any thread, a handler or
  `on_complete`. Transfers in flight get ERROR 0 `"server shutting down"`
  (their result's `error` is `TransferAborted`).
- **`start() -> Server`** — run `serve_forever` in a daemon thread.
- **`stop(timeout=5.0)`** — `shutdown()` and join the `start()` thread.
- **`close()`** — stop and release every socket. Also the context-manager
  exit. A closed server cannot serve again.

Statistics: **`stats`** (a `Stats`: `stats["completed"]`, `snapshot()`)
counts `requests`, `refused` (refused before a transfer, including limits
and `server busy`), `started`, `completed`, `failed`, `bytes_sent`,
`bytes_received`, `retransmits`; **`stats_snapshot()`** returns them plus
`active`. Thread-safe; meant for a metrics exporter. A `Relay` has the same,
with `bytes_to_clients`/`bytes_from_clients`.

Properties: `server_address` (the bound `(host, port, ...)`, valid after
close), `supports_pktinfo`, `dual_stack`, `active_sessions`.

Behaviour worth knowing:

- Every transfer runs on its **own UDP socket** (its transfer ID). A packet
  from any other address/port gets ERROR 5 and does not disturb the transfer.
- A repeated request from the same client address and port while its transfer
  is running is ignored (it is a retransmission).
- Datagrams on the listening port that are not RRQ/WRQ are **dropped without
  reply** (no reflection). A malformed RRQ/WRQ gets ERROR 4.
- A v4 client of a dual-stack listener is served from a plain IPv4 socket, so
  `result.peer` shows `"192.0.2.7"`, never `"::ffff:192.0.2.7"`.
- Socket buffers are grown to hold two windows (Windows defaults to 64 KiB,
  which a large window overflows).

**`PortRange(low, high)`** — `PortRange.of(value)` takes `None`, a pair, a
`range` or a `PortRange`; `len()`, iteration (every port once, from the
round-robin cursor). `ValueError` outside 1..65535 or with `low > high`.

**`ServerLimits(max_request_size=1024, max_filename_length=512, max_options=16, max_option_length=255, max_sessions_per_client=None, max_duration=None)`**
— bounds on untrusted input. A request over a size/name/option limit gets
ERROR 4 from the listening port; a client over `max_sessions_per_client`
(counted per address, any port) gets ERROR 0 `"server busy"`.
`max_duration` ends a transfer that runs longer (ERROR 0 to the peer,
`TransferTimeout` in the result).

**`ServerOptions(max_blksize=65464, max_windowsize=64, max_window_bytes=4 MiB, allowed=None, refused=(), fit_mtu=False, registry=None)`**
— the negotiation policy.

- A larger `blksize`/`windowsize` request is answered with the maximum
  (which the RFCs allow). `windowsize` is also lowered so one window of the
  negotiated block size fits `max_window_bytes` — the memory a sender holds.
- `allowed` — option names ever acknowledged; `None` is `STANDARD_OPTIONS`
  (`blksize`, `timeout`, `tsize`, `windowsize`). Add `EXTENSION_OPTIONS`
  names to accept extensions: tftp-hpa's `blksize2`, `utimeout`,
  `rollover`, `cookie`; Microsoft's `mstfwindow`; pytftp's `x-list`,
  `x-mtime` (`LISTING_OPTIONS`). A name not in `registry` raises
  `ValueError`.
- `refused` — never acknowledged even if allowed: for firmware that asks for
  an option and then mishandles it (`refused={"windowsize"}`).
- `fit_mtu` — lower `blksize` so a DATA packet fits the MTU of the interface
  the request arrived on (IP + UDP + 4-byte header): 1468 on IPv4 and 1448
  on IPv6 for a 1500 MTU (`netimps.max_udp_payload` − 4). Needs pktinfo for
  the interface; boot ROMs often cannot reassemble fragments.
- `accepts(name)` — `name` in `allowed` and not in `refused`.

## Options, registry and profiles

Every option is an **`OptionHandler`** (`name`, `standard`) with
`negotiate(value, ctx) -> str | None` (server: the value to acknowledge, or
`None` to leave it out — RFC 2347's refusal; set fields on `ctx.result`) and
`accept(requested, acked, ctx)` (client: validate and apply; raise
`tftp.options.refuse(msg)` for ERROR 8). `ServerContext` carries `result`,
`requested`, `acked`, `policy`, `is_read`, `size`, `mtu`, `ipv6`, `stream`
(what the handler opened for an RRQ, else `None`) and `max_blksize` (policy
limit after `fit_mtu`); `ClientContext` carries
`result`, `requested`, `is_read`. Custom values go in `ctx.result.extra`.

**`OptionRegistry(handlers=BUILTIN_OPTIONS)`** — `register(handler,
replace=False)` (a duplicate name raises), `unregister(name)`, `get(name)`,
`in`, iteration, `names()`, `standard()`, `copy()`. **Registration order is
negotiation order** (`windowsize` after the block size it depends on).
`DEFAULT_REGISTRY` is what servers and clients use unless given another;
`register_option(handler)` adds to it. A custom option must also be in a
server's `allowed`.

Built-ins: `blksize`, `timeout`, `tsize`, `windowsize` (standard), and
`blksize2` (largest power of two ≤ the request and the limit; ignored when
`blksize` was acknowledged), `utimeout` (10 000..255 000 000 µs),
`rollover` (0/1), `cookie` (echoed unchanged; a client refuses a changed
one), `mstfwindow` (`Mstfwindow`: answers `31416` with `27182` and runs a
window of 4 unless `windowsize` was also acknowledged; a client refuses any
other answer), `x-list` (`XList`: acknowledged `1` only when the RRQ's stream
is a listing, `_tftp_listing_`), `x-mtime` (`XMtime`: an RRQ's OACK carries
the stream's `mtime` attribute or `fstat` time, whole seconds; omitted when
unknown). `stream_mtime(stream)` is that lookup.

**Profiles** — `Profile(name, server, client)`: `.server` is a
`ServerOptions`, `.client` a fresh dict of `Client` keyword arguments.
`PROFILES` maps names to the five presets:

| profile | server | client |
| --- | --- | --- |
| `STRICT` | standard options | `fallback=False` |
| `DEFAULT` | standard options | library defaults |
| `PXE` | standard + `rollover`, `utimeout`; `fit_mtu=True` | `blksize=1428` |
| `HPA` | tftp-hpa's extensions | `utimeout=True` |
| `LEGACY` | standard, `windowsize` refused | no options at all, `strict_source=False` |

```python
tftp.Server("/srv/tftp", options=tftp.PXE.server)
tftp.Client("192.0.2.1", **tftp.LEGACY.client)
```

## Handlers

A handler is any object with:

- **`open_read(context) -> reader`** — a binary reader with `readinto` or
  `read`, and `close`. `tsize` is answered when the reader has an integer
  `size` attribute, a real `fileno()`, or is seekable.
- **`open_write(context, size) -> writer`** — a binary writer with `write` and
  `close`. `size` is the client's announced `tsize` or `None`. **`close` is
  called after the last block is written and before it is acknowledged**, so
  an exception there reaches the client as ERROR. If the writer has `abort()`,
  a failed transfer calls it instead of `close()`.

**Slow or asynchronous streams.** A reader's `readinto`/`read`, a writer's
`write` and its `close` may raise `tftp.WouldBlock` when nothing is ready:
the transfer pauses (a writer's block stays unacknowledged, which is
backpressure on the client) without blocking the loop. A stream that has
`set_wakeup(callback)` is given a thread-safe callback to call when it can
make progress again; without it a stalled transfer only resumes when the
peer retransmits. A paused transfer is subject to `max_duration`, not to
peer retries.

Either may raise `TftpError(code, message)` to refuse with that ERROR, or
`OSError`, mapped by errno (`ENOENT` → 1, `EACCES`/`EPERM` → 2, `ENOSPC` → 3,
`EEXIST` → 6). The OS message is **not** sent (it could disclose paths). Any
other exception is logged and sent as ERROR 0. What reaches the wire is
always encodable (see `encode_error`), and an exception escaping one transfer
ends that transfer, with a logged traceback, and never the loop. Handlers run
on the event-loop thread: **a slow handler stalls every transfer**.

`write` receives a `memoryview` of a reused buffer when the writer is a
standard file object (`io.IOBase`) or declares `_tftp_copies_ = True`, and
`bytes` otherwise — so a writer that keeps references is safe by default.

**`RequestContext`** — `request` (the `Request`), `peer` (client address
tuple), `local_address` (the destination address as text, or `None` without
pktinfo), `interface_index` (or 0), `interface` (that interface as a
`netimps.Interface` — name, addresses, MTU — or `None`), `listing` (the RRQ asks for `x-list=1`
and the server allows it); shortcuts `filename`, `mode`, `options`.

**`FileSystemHandler(root, *, writable=False, create=True, overwrite=False, backslash=True, max_upload=None)`**

- Serves files under `root` (which must exist). Leading `/` is stripped, so
  `/boot/x` is `root/boot/x`. With `backslash`, `\` is a separator too (Windows
  boot loaders send `\boot\bcd`).
- **Containment**: `..` components are refused (ERROR 2), and the resolved
  path, symlinks included, must stay inside `root` (ERROR 2). On Windows,
  drive letters, `:` streams and reserved device names (`CON`, `NUL`, ...) are
  refused too.
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
- `resolve(filename) -> str` exposes the mapping (raises `TftpError`).

**`tftp.listing`** — the `x-list` format: UTF-8 lines `<f|d> <size> <mtime|-> <name>`
(`%`, CR, LF in names as `%25`, `%0D`, `%0A`). **`ListEntry(name, is_dir,
size, mtime=None)`**, **`format_listing(entries) -> bytes`**,
**`parse_listing(data) -> list`** (malformed lines skipped),
**`DirectoryListing(directory, root=None)`** (a `BytesIO` with `size`,
`mtime`, `_tftp_listing_`; sorted by name; leaves out symlinks resolving
outside `root` and in-progress uploads `.name.*.part`), `LIST_OPTION`,
`MTIME_OPTION`.

**`AtomicWriter(path, overwrite=True)`** — writes to a hidden temp file beside
`path`; `close()` renames it into place (refusing with ERROR 6 if
`overwrite=False` and `path` appeared meanwhile); `abort()` deletes it.

## asyncio (`tftp.aio`)

**`AsyncClient(...)`** — `Client`'s arguments and rules (options, backoff,
fallback, `trace`, `on_negotiated`); coroutine methods `download(filename,
dest, *, mode, progress)`, `get`, `upload(filename, source, *, mode,
progress)`, `put`, `size`, `stat` (both in the executor), `listdir`, and the async generator **`stream(filename, *, mode,
buffer=1 MiB)`** yielding chunks as they arrive (a slow consumer holds ACKs
back; at most `buffer` bytes are held). Name resolution runs in the
executor. Destinations: a path, a binary file (written on the loop — fine
for local files), or an **async writer** (`async write(data)`, or `write` +
`async drain()` like `asyncio.StreamWriter`); `download` returns only once
an async writer has taken every byte (the writer is not closed). Sources: a
path, bytes, a binary file, or an **async reader** (`async read(n)`) or async
iterable of bytes. Cancelling the task sends the server ERROR 0. Each
attempt (including the option fallback) uses a fresh socket.

**`AsyncServer(root_or_handler, host=None, port=69, *, executor=None, **server_options)`**
— `Server`'s arguments except `open_in_thread`/`workers`. `async with
AsyncServer(...) as server: await server.serve_forever()`, or `await
server.start()` … `await server.stop()`; `shutdown()` is thread-safe; `await
close()`. Handlers:

- `open_read`/`open_write` may be `async def` (awaited on the loop), or
  synchronous: those marked `_tftp_fast_open_` (file, memory) run inline,
  others in `executor` (default: the loop's) — so `HttpHandler` and
  `UpstreamHandler` work unchanged (their `Pipe` wake-ups are thread-safe).
- The returned stream may be an async reader/iterable (RRQ) or async writer
  (WRQ); a writer is closed after the last block, and the final ACK waits
  until everything is written.
- pktinfo is kept on every loop: the listener is read with netimps'
  `UDPEndpoint.arecv` (`add_reader`, or a readiness thread where the loop
  has none — Windows' Proactor loop).
- On Windows the default `max_sessions` is 500 (a selector loop's
  `select()`).

**`AsyncReaderBridge(source, capacity=1 MiB, size=None)`** /
**`AsyncWriterBridge(sink, capacity=1 MiB, close_sink=True)`** — the
adapters doing that (engine-side `readinto`/`write`/`close` raising
`WouldBlock`, `set_wakeup`; writer `await finish()`); create them on the
running loop. `is_async_reader(obj)` / `is_async_writer(obj)` say which
objects they take.

## Paths (`tftp.path`, `path` extra)

`pip install tftp[path]` adds `pathlib-next[uri]`. Importing `tftp` never
loads it.

**`TftpPath(*segments, client, mode="octet")`** — a `pathlib_next.Path`
bound to a `Client` (`client.path("boot", "x")`). Joined like a
`PurePosixPath` (`\` counts as `/`); the path text is the filename sent,
so `/boot/x` and `boot/x` stay distinct. Joining onto a `TftpPath` keeps its
client; `with_client()`, `with_mode()`; `client`, `transfer_mode`. Equality
and hashing include the server (host, port). `as_uri()` is the `tftp://`
URL. `relative_to()` works on the path text.

**`TftpUriPath`** — the `tftp://host[:port]/path[;mode=netascii]` scheme
for `pathlib_next.uri.UriPath`, registered through the
`pathlib_next.schemes` entry point, so `UriPath("tftp://...")` returns one
without importing `tftp`. `filename`, `transfer_mode`;
`with_options(**client_options)` / `with_client(client)` choose the
`Client` (default: the URI's host and port, library defaults). The URI host
may be an address object; `Client` accepts one.

Both support what TFTP can do, plus listing against a server speaking
`x-list`:

- `open("r")` streams a download; `open("w")` streams an upload that
  completes (and raises the server's error) on `close()`; `open("x")`
  first probes the size and raises `FileExistsError` if the file exists
  (not atomic). `a` and `+` modes raise `NotImplementedError`. `open()`
  returns only once the server has answered, so `FileNotFoundError`/
  `PermissionError` surface there. At most 1 MiB is buffered; an abandoned
  read sends the server an ERROR.
- `read_bytes`, `read_text`, `write_bytes`, `write_text` (text newline
  handling is pathlib's), `copy()`/`move()` to and from any pathlib_next path.
- `stat()` is one `Client.stat()` probe (`FileStat`: `st_size` and
  `st_mtime` 0 when unknown, directory mode for a listed directory);
  `exists()`, `is_file()`, `is_dir()`.
- `iterdir()`, `walk()`, `glob()`/`rglob()` and `copy(..., recursive=True)`
  need a server allowing `x-list` (one listing per directory, with each
  entry's type, size and time, so no per-entry probe). Against other servers
  a directory raises `FileNotFoundError`; listing a file raises
  `NotADirectoryError`.
- Deleting, renaming, creating directories and permissions raise
  `NotImplementedError`.
- TFTP errors become pathlib's: `FileNotFoundError`, `PermissionError`,
  `FileExistsError`, `OSError(ENOSPC)`, `TimeoutError`, else `OSError(EIO)`.

## Backends (`tftp.backends`)

- **`MemoryHandler(files=None, *, writable=False, overwrite=True, max_upload=None)`**
  — serves `files` (name → bytes; names normalised so `/a\\b` is `a/b`);
  uploads replace an entry only when complete. Thread-safe `files` updates.
- **`HttpHandler(base_url=None, *, url_for=None, writable=False, headers=None, timeout=10.0, buffer=1 MiB, opener=None)`**
  — TFTP-to-HTTP(S) gateway on `urllib`: GET `base_url + quote(name)`
  (or `url_for(context)`), streamed; `Content-Length` answers `tsize`;
  WRQ → `PUT` (chunked without `tsize`) when `writable`; the final ACK
  waits for the PUT's response. HTTP 404/410 → ERROR 1, 401/403 → 2,
  409 → 6, 413/507 → 3, other → 0. `..` in a name → ERROR 2.
- **`UpstreamHandler(upstream, *, client_options=None, buffer=1 MiB, stall_timeout=30.0, writable=False)`**
  — terminating proxy: `upstream` is `"host"`, `"host:port"`, an address
  object or `netimps.Host`, `(host, port)`, or `upstream(context)` returning
  one. Each side
  negotiates independently (`client_options` for the upstream `Client`);
  only bytes cross, through a bounded pipe, so a slow client slows the
  upstream. The RRQ is answered after the upstream answers: its ERROR
  reaches the client with the same code and text, its `tsize` passes
  through. A WRQ's final ACK waits for the upstream's final ACK.
- **`Pipe(capacity=1 MiB, size=None)`** — the bounded byte pipe between a
  worker thread and a transfer, for your own handlers. Transfer side
  (non-blocking, `WouldBlock`): `readinto`, `write`, `close`, `abort`,
  `set_wakeup`. Worker side (blocking): `put(data, timeout)`,
  `get(n, timeout)`/`read(n)`, `finish(error=None)`. `for_upload()` makes
  `close()` wait (`WouldBlock`) until the worker calls
  `set_result(error=None)`, and re-raise its error. `size` answers `tsize`.

## Relay (`tftp.relay`)

**`Relay(route, host=None, port=69, *, idle_timeout=30.0, max_lifetime=3600.0, linger=2.0, upstream_source=None, limits=None, max_sessions=None, ignore_broadcast=True, reply_from_request_address=True, trace=None, on_session_end=None, port_range=None, interface=None)`**
— a transparent application relay (there is no standard TFTP relay). The
request is forwarded **byte for byte** from a fresh upstream-side socket; the
upstream's TID is learned from its first answer (from the address asked);
datagrams then cross unchanged between `client <-> relay(client-side TID)` and
`relay(upstream-side TID) <-> upstream`, so options and extensions this
library does not know work end to end. Each side negotiates nothing with the
relay — the client sees the upstream's OACK. For different settings per side
use `UpstreamHandler` (a terminating proxy) instead.

- `interface` — listen on one adapter, as for `Server`.
- `port_range` — as for `Server`, for both sockets of each relayed transfer
  (client side and upstream side).
- `route` — an upstream (`"host"`, `"host:port"`, `"[v6]:port"`, an address
  object or `netimps.Host`, `(host, port)`, `Upstream`) or `route(request, context) -> upstream | None`;
  `None` refuses with ERROR 2 `"no route"`. Hostnames are resolved per
  request (cached 60 s). Routes run on the relay's loop: keep them fast.
- A transfer ends on: an ERROR either way (after ≤1 s), the final DATA/ACK
  exchange (the block size followed from an OACK's `blksize`/`blksize2`, then
  `linger`), `idle_timeout` without traffic, or `max_lifetime`. Keep
  `idle_timeout` above the longest timeout × retries a peer may use.
- A repeated request from the same client address/port is forwarded again
  only while the upstream has not answered. Strays on either leg get ERROR 5.
- Shutdown sends ERROR 0 `"relay shutting down"` to both sides of each
  transfer. Windows caps `max_sessions` at 250 by default (two sockets each);
  an explicit value above 255 there is a `ValueError`.
- `on_session_end(RelaySummary)` — `session`, `client`, `upstream` (the
  learned TID), `filename`, `operation`, `mode`, `bytes_to_client`,
  `bytes_from_client`, `packets`, `duration`, `reason` (`"complete"`,
  `"error"`, `"idle"`, `"lifetime"`, `"shutdown"`), `error` (`(code,
  message)` of an ERROR that passed through, or `None`).
- Lifecycle and properties as for `Server`: `serve_forever`, `shutdown`,
  `start`, `stop`, `close`, context manager; `server_address`,
  `supports_pktinfo`, `active_sessions`.

Routing helpers (`tftp.relay`): **`upstream(value) -> Upstream(host, port)`**
(`host` read by `netimps.split_host`: brackets dropped, a `"host:port"`
split, the port ASCII digits only);
**`by_subnet({network: upstream})`** (keys: anything `netimps.parse(...,
IPNetwork)` takes — CIDR strings, `ipaddress` networks, interfaces, addresses
as /32 or /128, `(address, prefix)`; client address, longest prefix; mapped
v4 matches v4 networks); **`by_prefix({prefix: upstream})`** (filename,
longest prefix, `\` = `/`, leading separators ignored); **`by_interface({key:
upstream})`** (keys: an interface index, a `netimps.Interface`, or the local
address the request was sent to — string, `ipaddress` address or interface,
`netimps.Host`; needs pktinfo); **`RouteTable(routes, default=None)`** (first
match wins).

## Packet events (`tftp.capture`)

**`PacketEvent(time, direction, local, remote, data, role="capture", session=None, leg=None)`**
— one datagram. `direction` is `"in"`/`"out"` from `role`'s view
(`"seen"` for a passive capture); `session` correlates one transfer's events;
`leg` is the relay side. Properties: `opcode`, `opcode_name`, `block`,
`payload_size`, `summary` (one line, never raises), `source`, `destination`;
methods `decode()`, `format()` (a human line), `to_dict(payload=False)` (JSON-
ready metadata; DATA payloads only as hex with `payload=True`).
`summarize(data)` is the one-line description on its own.

**Trace hooks** — `Client(trace=)`, `Server(trace=)`, `Relay(trace=)` take
`trace(PacketEvent)`, called for every datagram received and sent, on the
thread doing the I/O; exceptions it raises are logged, never propagated.
Roles are `"client"`, `"server"`, `"relay"`; one `session` id per transfer
(`c…`, `s…`, `r…`). A server traces a transfer's request as arriving at the
listening address. Requests a server refuses before a transfer exists are
not traced. Off (`None`) costs nothing per packet.

**`PcapWriter(path_or_binary_file)`** — writes events (it is a ready trace
hook: `Server(..., trace=PcapWriter("t.pcap"))`) or `write(time, source,
destination, payload)` as pcap, link type RAW, with synthesized IPv4/IPv6 and
UDP headers and valid checksums, so Wireshark/tshark decode it as TFTP
(v4-mapped addresses are written as IPv4). Context manager; `close()`.

**Reading captures** — pcap and pcapng (both byte orders, µs/ns and
`if_tsresol`, several interfaces), from a path or a stream (a live
`tcpdump -i eth0 -U -w - udp` pipe). Link types: Ethernet (VLAN/QinQ),
RAW, IPv4, IPv6, Linux SLL/SLL2, BSD NULL/LOOP. IPv4 and IPv6 fragments are
reassembled (a large `blksize` fragments on the wire). Non-UDP is skipped.

- **`read_frames(source) -> Iterator[(time, linktype, frame)]`**,
  **`read_datagrams(source) -> Iterator[UdpDatagram(time, source, destination, payload)]`**
  — `CaptureFormatError` (a `ValueError`) for anything that is not a capture.
- **`FlowTracker(ports=(69,), keep_payloads=True)`** — `feed(datagram) ->
  PacketEvent | None` (role `"capture"`, direction `"seen"`; `None` for UDP
  that is neither TFTP traffic of a known transfer nor to/from a request
  port), `feed_all(datagrams)`, `.transfers`. A transfer starts at an
  RRQ/WRQ to a request port and follows the server's answer (its TID; from
  another address too, within 10 s) between that TID and the client's
  address/port. `::ffff:a.b.c.d` and `a.b.c.d` count as one host.
- **`CapturedTransfer`** — `session`, `client`, `server`, `server_tid`,
  `filename`, `mode`, `operation`, `requested`, `acknowledged`, `blksize`,
  `windowsize`, `tsize`, `error` (`(code, message, "client"|"server")`),
  `complete` (final DATA seen and ACKed), `packets`, `retransmissions` (DATA
  seen again), `request_retransmissions`, `bytes`, `missing_blocks`,
  `started`, `ended`, `duration`; `data(decode_netascii=True)` returns the
  file up to the first gap (block numbers followed across rollover);
  `to_dict()`.
- **`analyze(source, *, ports=(69,), filter=None, keep_payloads=True) -> Analysis(events, transfers)`**
  — a whole capture (path, stream, or datagrams) at once.
- **`sniff(interface=None, *, stop=None) -> Iterator[UdpDatagram]`** — live,
  Linux only (`AF_PACKET`, needs `CAP_NET_RAW`); `live_capture_supported()`.
  Elsewhere pipe `tcpdump`/`dumpcap -w -` into `read_datagrams`.

**Filters** — **`compile_filter(text) -> predicate(event)`**: `key=value`
clauses joined by `and`, `,` for "any of", `!=` to negate; empty matches all.
Keys: `op` (opcode name), `host`/`src`/`dst` (address, CIDR, `addr:port`,
`[v6]:port`, `:port`; mapped v4 matches v4), `port`, `file` (shell pattern,
requests only), `block`, `code` (ERROR code), `session`, `leg`, `direction`.
`FilterError` (a `ValueError`) for an unknown key or malformed value.

## Results and errors

**`TransferResult`** — `filename`, `operation` (`"read"` for RRQ, `"write"`
for WRQ, on both sides), `mode`, `peer`, `local`, `bytes` (payload bytes on
the wire; netascii-encoded size in netascii mode), `blocks`, `retransmits`,
`duration` (seconds), `negotiated`, `error` (or `None`); properties `ok` and
`throughput` (bytes/s).

**`Negotiated`** — what a transfer ran with: `blksize`, `windowsize`,
`timeout` (seconds), `tsize` (or `None`), `rollover`, and `options` (the OACK
as sent/received; empty when RFC 1350 defaults applied).

Exceptions: **`TftpError(code=0, message="")`** (base; `.code` is an
`ErrorCode` when the value is known, `.message` defaults to the code's
standard text; `code` must be an `int` in 0..65535 and `message` text, else
`TypeError` or `ValueError` at construction, so `TftpError("no such file")` is
refused rather than built with the text as its code), **`RemoteError`** (the peer sent ERROR; always raised as
the subclass for its code — `FileNotFound` 1, `AccessViolation` 2,
`DiskFull` 3, `IllegalOperation` 4, `UnknownTransferId` 5,
`FileAlreadyExists` 6, `NoSuchUser` 7, `OptionNegotiationError` 8 — and
plain `RemoteError` for 0 and unknown codes; `RemoteError.from_code(code,
message)` builds one), **`ProtocolError`** (the peer broke the protocol;
code 4, or 8 for option problems), **`TransferTimeout`** (also a
`TimeoutError`; retries exhausted or `max_duration` passed),
**`TransferAborted`** (cancelled locally: `abort()`, server shutdown).
`MalformedPacket` is a `ValueError`.

## Wire format

`Opcode` (`RRQ`=1 … `OACK`=6) and `ErrorCode` (`NOT_DEFINED`=0 …
`OPTION_REFUSED`=8) are `IntEnum`s. Packet types are `NamedTuple`s:
`Request(opcode, filename, mode, options, raw=b"")` (`.is_read`; option
names lower-cased, first occurrence wins, mode lower-cased; `raw` is the
datagram as received, `.raw_options` every pair as sent — original case,
order, duplicates — and `.encode()` returns `raw` or a fresh encoding, so a
relay forwards unknown options untouched), `Data(block, data)`, `Ack(block)`,
`Error(code, message)`, `OptionAck(options)`.

- **`decode(bytes) -> Packet`** — raises `MalformedPacket`. Tolerates a
  missing final NUL and drops a dangling option name.
- **`encode_request(opcode, filename, mode="octet", options=None)`**,
  **`encode_data(block, data)`**, **`encode_ack(block)`**,
  **`encode_error(code, message="")`**, **`encode_oack(options)`** — option
  values are sent as `str(value)`; NUL inside a string raises `ValueError`,
  except in `encode_error`, which never raises: NUL becomes `?`, the text is
  cut to 512 octets and a code outside 0..65535 is sent as 0.
- Strings are UTF-8 with `surrogateescape`, so any byte sequence round-trips
  and real UTF-8 names decode naturally.
- Constants: `DEFAULT_BLKSIZE` 512, `MIN_BLKSIZE` 8, `MAX_BLKSIZE` 65464,
  `MAX_WINDOWSIZE` 65535, `STANDARD_OPTIONS`, `EXTENSION_OPTIONS`,
  `LISTING_OPTIONS` (`x-list`, `x-mtime`), `SUPPORTED_OPTIONS` (standard and
  extensions).

## URIs (RFC 3617)

- **`parse_url(url) -> TftpURL(host, port, filename, mode)`** — the file name
  is the percent-decoded path after the authority's `/`; `;mode=netascii`
  selects the mode (default `octet`); the port defaults to 69. Anything else
  (another scheme, no host or file, an unknown parameter, `mode=mail`) raises
  `ValueError`.
- **`format_url(host, filename, port=69, mode="octet")`** — the inverse
  (`str(TftpURL)` too); `host` may be an address or interface object or a
  `netimps.Host`; IPv6 hosts are bracketed. `port` must be an `int` (`None`
  omits it): a `str` or `bool` raises `TypeError`.
- **`download_url(url, dest, *, progress=None, **client_options)`**,
  **`upload_url(url, source, ...)`** — one-shot transfers by URL.

## Netascii

Local text uses LF; on the wire a line ends CR LF and a bare CR is CR NUL.
The translation is lossless both ways (a local `\r\n` travels as `\r\0\r\n`).
**`NetasciiReader(raw)`** (`readinto`, `close`) encodes a binary reader;
**`NetasciiWriter(raw)`** (`write`, `flush`, `close`, `abort`) decodes into a
binary writer, holding a trailing CR until it sees what follows. Both handle a
CR on any block boundary. `tftp.netascii` also has `encode`, `decode` and
`encoded_size(fileobj)` (seekable; position preserved).

## Transfer engine

**`Sender(send, read, negotiated, retries, now, oack=None, **kw)`** and
**`Receiver(send, write, negotiated, retries, now, reply=None, complete=None, **kw)`**
(`kw`: `backoff=2.0`, `max_timeout=None`, `expires=None` — an absolute
deadline in the caller's clock)
are one side of the DATA/ACK exchange **without any I/O**: packets leave
through `send(packet)`, arrive through `handle(buffer, n, now)`, and the caller
calls `on_timeout(now)` once `deadline` (in the caller's clock) passes, and
`resume(now)` when a stalled source/sink (`WouldBlock`) is ready again;
`abort(message)` cancels. State: `done`, `error`, `stalled`, `bytes`,
`blocks`, `retransmits`, `deadline`. A repeated OACK is tolerated: a
receiver re-sends its ACK 0, a sender ignores it (its own timeout resends
DATA 1). Build `read`
and `write` with `tftp.transfer.as_readinto(fileobj)` / `as_write(fileobj)`.
This is what the client and server drive; use it to run TFTP over another
transport or event loop.

## Command line (`cli` extra)

`pip install tftp[cli]` adds duho. The `pytftp` console script is installed
either way; without the extra it prints one line naming the extra and exits 1.
`python -m tftp` is equivalent.

```
pytftp get HOST REMOTE [LOCAL|-]       [-p PORT] [-m octet|netascii] [-b BLKSIZE] [-w WINDOW]
pytftp get tftp://HOST[:PORT]/FILE [LOCAL|-]      [-t TIMEOUT] [-r RETRIES] [--no-tsize] [--no-options]
                                       [--compat PROFILE] [-4|-6] [--json] [--trace] [--pcap FILE]
pytftp put HOST LOCAL|- [REMOTE]       (same options; or put tftp://HOST/FILE LOCAL|-)
pytftp ls HOST [DIR] | tftp://HOST/DIR (same options) [--json]
pytftp serve [ROOT] [--http URL | --upstream HOST[:PORT]] [-l ADDRESS | --interface NIC] [-p PORT] [-W/--write]
             [--overwrite] [--no-create] [--compat PROFILE | --max-blksize N --max-windowsize N
             --allow OPTION... --refuse OPTION... --fit-mtu] [--listing] [--max-sessions N]
             [--max-per-client N] [--port-range LOW:HIGH] [--per-client] [--ignore-case]
             [--remap REGEX=REPLACEMENT]... [--json] [--trace] [--pcap FILE]
pytftp relay [UPSTREAM] [--route-subnet CIDR=HOST[:PORT]]... [--route-prefix PREFIX=HOST[:PORT]]...
             [-l ADDRESS | --interface NIC] [-p PORT] [--idle-timeout S] [--max-sessions N] [--port-range LOW:HIGH]
             [--json] [--trace] [--pcap FILE]
pytftp capture FILE|- | -i IFACE  [-p PORT]... [-f FILTER] [--no-packets] [--transfers]
             [--extract DIR] [--json] [--payload]
```

- `-b 0` / `-w 0` request no `blksize` / `windowsize`; defaults are 1428 and 0.
  `--compat PROFILE` (`strict`, `default`, `pxe`, `hpa`, `legacy`) replaces the
  option flags with the profile's settings (client and server alike).
- `get` writes to the remote file's basename by default; `-` is stdout.
  `put` from `-` (stdin) needs a remote name. A `tftp://` URL replaces HOST and
  the remote name (and sets the mode with `;mode=netascii`).
- `--trace` prints every datagram on stderr; `--pcap FILE` writes them as a
  capture (Wireshark-readable).
- `serve --http URL` is the HTTP gateway, `serve --upstream` the terminating
  proxy; `--write` enables uploads for both. `--allow` adds extension options
  to the standard four; `--refuse` removes any.
- `--interface NIC` (serve, relay) listens on that adapter's IPv4 address
  (`-l ::` for its IPv6 one).
- `serve --listing` allows `x-list`/`x-mtime` (with `--compat` too), which
  `pytftp ls` and `TftpPath.iterdir()` need. `--port-range` pins transfer
  ports. Directory serving only: `--per-client` serves `ROOT/<client
  address>/` when it exists (IPv6 `:` written `-`), else `ROOT`;
  `--ignore-case` finds names whatever their case (the exact name wins).
  `--remap REGEX=REPLACEMENT` (repeatable, split at the first `=`) rewrites
  requested names with the first matching rule, for any source. These are
  CLI conveniences, not library API.
- `ls` prints `type size time name` per entry (`--json`: a list of entries);
  exit 1 for a file, a missing name, or a server without listing.
- `relay` needs an UPSTREAM (the default route) or routes; prefix routes are
  tried before subnet routes. `--json` prints one line per finished transfer
  (its `RelaySummary`).
- `capture` reads a pcap/pcapng file, a live pipe on stdin (`tcpdump -i eth0 -U
  -w - udp | pytftp capture -`), or (Linux) an interface. Packets print as they
  are decoded; `--transfers` adds a summary per transfer at the end;
  `--extract DIR` writes each transfer's file as `<session>-<name>`
  (`.partial` when incomplete). Ctrl-C ends a live capture and still prints
  the summaries.
- Exit codes: 0 success, 1 the transfer failed or the port could not be
  bound (error text on stderr), 2 a caller error (bad argument, missing file,
  unreadable capture, bad filter).
- `--json`: one JSON object on stdout per transfer, session or packet;
  diagnostics always go to stderr.
- `serve` and `relay` log each transfer at INFO on stderr (`-v`/`-q` adjust)
  and their final counters when stopped.

## Dependencies

`netimps` (required, no dependencies of its own; imported lazily, on the
first transfer or server): pktinfo receive and reply sockets (`UDPEndpoint`,
also `arecv` for `AsyncServer`), broadcast/multicast checks, MTU payload
sizing, the retransmission timer (`Backoff`), socket binding,
host:port parsing, address/network types (`HostLike`, `IPNetworkLike`,
`Host`, `Interface`: what the address-taking parameters accept), bind-error
hints. Importing netimps installs its additive `recvmsg`/`sendmsg`
socket patch on Windows unless `NETIMPS_SOCKET_PATCH=0` is set first (pktinfo
does not depend on it).
`duho` (optional, `cli` extra).
