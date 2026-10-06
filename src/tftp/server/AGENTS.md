# `tftp.server` — public API header

Header-file-style reference for the `tftp.server` package: the two servers, the
handler contract a server calls, the per-request context and the limits and
counters of a server. Every public export with its signature, arguments,
contract and gotchas, so the package can be used without reading its source. It
ships inside the package and is self-contained; the top header is
`tftp/AGENTS.md`. Development documentation lives with the source at
<https://github.com/jose-pr/pytftp>.

`AsyncTFTPServer` is bound on first access, so importing `tftp.server` never
imports `asyncio`. The sync and async servers are documented together, the
differences stated once.

## Server (`tftp.server`)

```python
TFTPServer(
    root_or_handler, *, host=None, port=69, writable=False, create=True, overwrite=False,
    timeout=1.0, retries=5, options=None, max_sessions=500, reply_from_request_address=True,
    dally=True, on_complete=None, limits=None, ignore_broadcast=True, backoff=2.0,
    max_timeout=None, trace=None, open_in_thread=None, workers=8, port_range=None, interface=None
)
TFTPServer.bind()
TFTPServer.serve_forever()
TFTPServer.shutdown()
TFTPServer.start()
TFTPServer.wait_closed(timeout=None)
TFTPServer.close()
```

- `root_or_handler` — a directory (wrapped in `FilesystemBackend` with
  `writable`, `create`, `overwrite`) or any handler object (see below).
- `host` — `None` (default) or `"::"` listens on IPv6 **and IPv4** with a dual-stack
  socket, falling back to `"0.0.0.0"` on a host without IPv6. `"0.0.0.0"` is
  IPv4 only; a specific address (`"192.0.2.10"`, `"[fe80::1%eth0]"`) listens
  there alone. An `ipaddress` address or `netimps.Host` works too.
- `port` — `0` picks a free port; read it from `server_address` after `bind()`.
- `timeout`, `retries` — per transfer, unless the client negotiates `timeout`.
- `options` — a `TFTPServerOptions` policy.
- `max_sessions` — concurrent transfers; beyond it a request gets ERROR 0
  `"server busy"`. **The default is 500 on every platform.** `None` is
  unlimited, except on Windows, where it is 510 because `select()` watches at
  most 512 sockets; an explicit value above 510 there is a `ValueError` at
  construction.
- `reply_from_request_address` — each transfer's socket is bound to the
  address the request was sent to (pktinfo), so a multi-homed host or a VIP
  answers from the address the client used. Where the platform cannot report
  it, replies come from the listening address, or from the routing table's
  choice when listening on a wildcard. Check `has_pktinfo`.
- `dally` — keep a finished upload's socket for one timeout to re-ACK a
  repeated last DATA.
- `on_complete(result)` — called with a `TransferResult` after **every**
  transfer, refused and failed ones included (`result.error` set). Exceptions
  it raises are logged and swallowed.
- `limits` — a `TFTPServerLimits` (below).
- `ignore_broadcast` — silently drop requests addressed to a broadcast
  (limited or subnet) or multicast address. Needs pktinfo to see the
  destination; without it every request looks unicast.
- `backoff`, `max_timeout` — as for `TFTPClient`, per transfer.
- `trace` — a hook called with a `PacketEvent` for every datagram received and sent
  (`tftp/capture/AGENTS.md`); an exception it raises never reaches a transfer.
- `open_in_thread`, `workers` — call the handler's `open_read`/`open_write`
  in a pool of `workers` threads so a handler that blocks (HTTP, an upstream
  server) never stalls other transfers. `None` decides per handler: one
  with `opens_fast = True` (`FilesystemBackend`, `MemoryBackend`)
  opens inline on the loop, anything else in a worker. A retransmitted
  request is still recognised while its open is pending.
- `interface` — listen on one network adapter: a name (`"eth0"`), a
  `netimps.Interface`, its MAC, or one of its addresses. The listener binds
  **one address** of it: the one named, else the adapter's primary IPv4
  address (non-loopback preferred), else its IPv6 one; `host="0.0.0.0"` or
  `"::"` picks the family. Another `host` is a `ValueError` at construction,
  and an adapter netimps cannot resolve is one from `bind()`. Requests to the adapter's other addresses are
  not received; run one server per family or address as needed.
- `port_range` — a `PortRangeLike`: a `PortRange`, a `(low, high)` pair
  (inclusive), a `range` or `"LOW:HIGH"` text. Transfer sockets take their
  ports from it (round-robin from a position this server owns, so a
  just-released port is reused last and two servers given one range share
  no position), for a firewall to allow. With every port taken a
  request gets ERROR 0 `"server busy"` (counted `refused`). `None` lets the
  OS choose.
- The constructor checks and stores its arguments and opens nothing; `bind()`
  raises `OSError` if the port cannot be bound (port 69 needs privileges on
  POSIX).

Lifecycle (the same names on `AsyncTFTPServer` and `TFTPRelay`; a handler
that wants the server down calls `shutdown()`, never `close()`):

- **`bind()`** — open and bind the sockets; idempotent. `start()`,
  `serve_forever()` and entering the context manager call it, so call it
  yourself to take a privileged port and then drop privileges, or to read
  `server_address` before serving. Nothing is left open when it raises.
- **`serve_forever()`** — bind, then run the event loop in the calling thread
  until `shutdown()`. One thread serves every transfer. Raises `RuntimeError`
  when already serving or closed.
- **`shutdown()`** — ask serving to stop and return at once; safe from any
  thread, a handler or `on_complete`; no effect when nothing is serving. Transfers in flight get ERROR 0 `"server shutting down"`
  (their result's `error` is `TransferAbortedError`).
- **`start() -> TFTPServer`** — bind, run the loop in a daemon thread and
  return once serving. Raises what `bind()` raised, and `RuntimeError` when
  already serving or closed.
- **`wait_closed`** — block until serving has stopped
  and the `start()` thread has ended; returns `False` when `timeout` seconds passed
  first. `RuntimeError` from the serving thread itself.
- **`close()`** — `shutdown()`, `wait_closed()` (up to five seconds; a handler
  that has not returned by then is abandoned) and release every socket. Final
  and repeatable; also the context-manager exit (which does not serve:
  entering only binds). A closed server cannot bind or serve again; a server
  stopped with `shutdown()` and not closed can serve again.

Statistics: **`stats`** (a `TFTPStats`: `stats["completed"]`, `snapshot()`)
counts `requests`, `refused` (refused before a transfer, including limits
and `server busy`), `started`, `completed`, `failed`, `bytes_sent`,
`bytes_received`, `retransmits`; **`stats_snapshot()`** returns them plus
`active`. Thread-safe; meant for a metrics exporter. A `TFTPRelay` has the same,
with `bytes_to_clients`/`bytes_from_clients`.

Properties: `server_address` (the bound `(host, port, ...)`, `None` before
`bind()`, kept after close), `has_pktinfo` and `is_dual_stack` (`None` before
`bind()`), `active_sessions`.

Behaviour worth knowing:

- Every transfer runs on its **own UDP socket** (its transfer ID). A packet
  from any other address/port gets ERROR 5 and does not disturb the transfer.
- A repeated request from the same client address and port while its transfer
  is running is ignored (it is a retransmission).
- Datagrams on the listening port that are not RRQ/WRQ are **dropped without
  reply** (no reflection). A malformed RRQ/WRQ gets ERROR 4.
- A v4 client of a dual-stack listener is served from a plain IPv4 socket, so
  `result.peer` shows `"192.0.2.7"`, never `"::ffff:192.0.2.7"`.
- Socket buffers are grown to hold two windows of the negotiated `blksize`
  (Windows defaults to 64 KiB, which a large window overflows; FreeBSD and
  macOS refuse a datagram above the sender's `SO_SNDBUF`, 57344 and 9216 by
  default), on the server's transfer sockets and on both clients'. The server
  agrees to the `blksize` its policy allows and does not cap it by the host's
  defaults: it cannot know the peer's receive buffer, and **a client that asks
  for a block larger than its own receive buffer gets no data on FreeBSD**
  (default 42080), where the kernel drops the oversized datagram without a
  report. A client that raises its buffer or asks for less is served.
- A send the host refuses (`EMSGSIZE`, an unreachable peer) ends that transfer:
  the peer gets ERROR 0, `on_complete` gets a result whose `error` is set, the
  reason is logged at warning, and the server goes on. A full send buffer
  drops the datagram, which the retransmission timer recovers.

## Limits and counters (`tftp.server`)

```python
PortRange(low, high)
PortRange.parse(text)
PortRange.try_parse(text, default=None)
TFTPServerLimits(
    *, max_request_size=1024, max_filename_length=512, max_options=16, max_option_length=255,
    max_sessions_per_client=None, max_duration=None, max_idle=60.0
)
```

**`PortRange`** — a frozen, hashable value (`low`, `high`; equal
to another `PortRange` only): `len()` counts the ports, iteration and `in`
cover them in order, `str()` is `LOW:HIGH`, `PortRange.parse("LOW:HIGH")`
(`LOW-HIGH` too) reads it back and `PortRange.try_parse(text, default=None)`
returns `default` instead of raising. `TFTPValueError` outside 1..65535 or with
`low > high` or for text that is not a range, `TypeError` for a non-`int` or
non-`str`. **`PortRangeLike`** (`tftp.server`) is what `port_range=` takes in
place of a `PortRange`: a `(low, high)` pair, a `range` of step 1 or the text.

`TFTPServerLimits` — bounds on untrusted input. A request over a size/name/option limit gets
ERROR 4 from the listening port; a client over `max_sessions_per_client`
(counted per address, any port) gets ERROR 0 `"server busy"`.
`max_duration` ends a transfer that runs longer (ERROR 0 to the peer,
`TransferTimeoutError` in the result). **`max_idle`** (seconds, default 60; `None`
for no bound) ends a transfer that has had no datagram from its peer for that
long, with `TransferTimeoutError`, whatever `timeout` the client negotiated: it
counts the peer's silence, not the transfer's length, and not time the
handler keeps the server waiting. A transfer holds memory for the blocks it
has read, not for the window it negotiated, and none of it once it ends.
`TFTPServerLimits` is mutable: it compares equal when every field does, is unhashable and prints
its fields.

## Handlers (`tftp.server`)

```python
TFTPHandler.open_read(context)
TFTPHandler.open_write(context, size)
TFTPRequestContext(request, peer, *, local_address=None, interface_index=0)
TFTPRequestContext.with_filename(filename)
AtomicWriter(path, *, overwrite=True, mode=None)
```

The contract is a set of `typing.Protocol`s in `tftp.server`; the members
listed as optional are looked up once per request or transfer, and a class
that lacks them is still a valid implementation. A **plain** handler is for
`TFTPServer`; the asyncio server takes the coroutine contract below.

**`TFTPHandler`** — an object with:

- **`open_read`** — a `TFTPReader` (required: `readinto`,
  `close`) or a `TFTPChunkReader` (required: `read`, `close`). Optional:
  `size` (an integer; `tsize` is also answered from a real `fileno()` or a
  seekable file), `mtime`, `set_wakeup`, `lists_directories`.
- **`open_write`** — a `TFTPWriter` (required:
  `write`, `close`). `size` is the client's announced `tsize` or `None`.
  **`close` is called after the last block is written and before it is
  acknowledged**, so an exception there reaches the client as ERROR. Optional:
  `abort()` (a failed transfer calls it instead of `close()`),
  `copies_writes`, `set_wakeup`.
- Optional on the handler itself: **`opens_fast = True`**, so its hooks run
  inline on the loop; otherwise they run in a worker thread.

A handler whose hooks are `async def` is refused by `TFTPServer` with
`TypeError` at construction.

**Slow or asynchronous streams.** A reader's `readinto`/`read`, a writer's
`write` and its `close` may raise `tftp.WouldBlock` when nothing is ready:
the transfer pauses (a writer's block stays unacknowledged, which is
backpressure on the client) without blocking the loop. A stream that has
`set_wakeup(callback)` is given a thread-safe callback to call when it can
make progress again; without it a stalled transfer only resumes when the
peer retransmits. A paused transfer is subject to `max_duration`, not to
peer retries.

Either hook may raise `TFTPError(code, message)` to refuse with that ERROR, or
`OSError`, mapped by errno (`ENOENT` → 1, `EACCES`/`EPERM` → 2, `ENOSPC` → 3,
`EEXIST` → 6). The OS message is **not** sent (it could disclose paths). Any
other exception is logged and sent as ERROR 0. What reaches the wire is
always encodable (see `encode_error`), and an exception escaping one transfer
ends that transfer, with a logged traceback, and never the loop. Streams are
read and written on the server's thread: **a slow stream stalls every transfer**
(a stalled `readinto`/`write` raises `WouldBlock` instead).

`write` receives a `memoryview` of a reused buffer when the writer is a
standard file object (`io.IOBase`) or has `copies_writes = True`, and
`bytes` otherwise — so a writer that keeps references is safe by default.

**`TFTPRequestContext`** — `request` (the `RequestPacket`), `peer` (client address
tuple), `local_address` (the destination address as text, or `None` without
pktinfo), `interface_index` (or 0), `interface` (that interface as a
`netimps.Interface` — name, addresses, MTU — or `None`), `listing` (the RRQ asks for `x-list=1`
and the server allows it); shortcuts `filename`, `mode`, `options`;
`with_filename(name)` — a copy for a request naming `name`, everything else
carried over.

**`AtomicWriter`** — writes to a hidden temp file beside
`path`; `close()` renames it into place (refusing with ERROR 6 if
`overwrite=False` and `path` appeared meanwhile); `abort()` deletes it. The
temp file is private (0600) unless `mode` is given: it is then created as
`open()` creates a file with that mode (less the umask) and takes the
permissions of the file it replaces. On Windows a rename refused with "access
denied" is tried again for up to half a second, as a virus scanner or an
indexer holds the file just written for a moment; after that the error is raised.

## asyncio server (`tftp.server`)

```python
AsyncTFTPServer(
    root_or_handler, *, host=None, port=69, writable=False, create=True, overwrite=False,
    timeout=1.0, retries=5, options=None, max_sessions=500, reply_from_request_address=True,
    dally=True, on_complete=None, limits=None, ignore_broadcast=True, backoff=2.0,
    max_timeout=None, trace=None, port_range=None, interface=None
)
ThreadedHandler(handler, *, executor=None)
```

**`AsyncTFTPServer`** (from `tftp` and `tftp.server`) — `TFTPServer`'s arguments except
`open_in_thread`/`workers`, and its lifecycle: `bind()` and `shutdown()` are
plain methods (`shutdown()` is thread-safe), `await start()`, `await
serve_forever()` and `await wait_closed(timeout=None)` are coroutines, and
`await aclose()` is the closer: there is no `close()` and no `stop()`. `async
with AsyncTFTPServer(...) as server: await server.serve_forever()` binds on
entry and awaits `aclose()` on exit; `aclose()` from another task ends a
running `serve_forever()` without an exception. A serving task that ended on
an error is seen by `wait_closed()` and `aclose()`. Handlers:

- `root_or_handler` is a directory path, a handler with **coroutine hooks**
  (`AsyncTFTPHandler`: `async open_read(context) -> AsyncTFTPReader`,
  `async open_write(context, size) -> AsyncTFTPWriter`), or a synchronous
  handler wrapped in **`ThreadedHandler`**
  (`tftp.server`): its hooks run in `executor` (default: the loop's), or on
  the loop when the handler has `opens_fast = True`, and its streams stay
  synchronous. `HTTPBackend` and `UpstreamBackend` work through it (their `Pipe`
  wake-ups are thread-safe). A synchronous handler given without it raises
  `TypeError` when the server is built.
- `AsyncTFTPReader` requires `async read(size)` (`b""` at the end) and
  `async close()`, optionally `size`. `AsyncTFTPWriter` requires
  `async write(data)` (`data` is `bytes`) and `async close()`; it is closed
  after the last block, and the final ACK waits until everything is written.
- pktinfo is kept on every loop: the listener is read with netimps'
  `UDPEndpoint.arecv` (`add_reader`, or a readiness thread where the loop
  has none — Windows' Proactor loop).

## Types (`tftp.server`)

| Name | Meaning |
| --- | --- |
| `PortRangeLike` | what `port_range=` takes in place of a `PortRange` |
| `TFTPReader`, `TFTPChunkReader`, `TFTPWriter` | what a plain handler's `open_read` and `open_write` return |
| `AsyncTFTPHandler`, `AsyncTFTPReader`, `AsyncTFTPWriter` | the coroutine counterparts the asyncio server takes |
| `TFTPStats` | the counters of `stats`: a mapping of the names listed under "Server" with `snapshot()` |
