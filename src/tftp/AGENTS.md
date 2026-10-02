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
| — | block-number rollover | blocks wrap after 65535 (to 0, or 1 with `rollover`); file size is unlimited |
| RFC 1350 §6 | dallying | server re-ACKs a repeated last DATA for one timeout |

Not implemented: RFC 2090 multicast.

## Client

**`Client(host, port=69, *, timeout=1.0, retries=5, blksize=1428, windowsize=None, tsize=True, rollover=None, timeout_option=True, family=0, local_address=None, fallback=True, dally=False, backoff=2.0, max_timeout=None, max_duration=None, strict_source=True, utimeout=False, extra_options=None, registry=None)`**

- `host` — name or address; `"[v6]"`, `"host:port"` and `"[v6]:port"` are
  accepted, and a port written there overrides `port`.
- `timeout` — seconds before a retransmission. Requested from the server as
  `timeout` when whole (1..255); a fractional one is requested as
  `utimeout` only with `utimeout=True`, otherwise not at all. Nothing is
  requested with `timeout_option=False`.
- `retries` — retransmissions of one packet before `TransferTimeout`.
- `backoff`, `max_timeout` — each consecutive retransmission (of the
  request too) waits `backoff` times longer, up to `max_timeout` (default
  8 × `timeout`); progress resets the wait. `backoff=1` disables it.
- `max_duration` — seconds a whole transfer may take; `None` is unlimited.
- `strict_source` — the first answer must come from the address the request
  was sent to. `False` accepts a multi-homed server answering from another
  address (the transfer then locks on to that address and port).
- `blksize` — requested; `None` asks for nothing (512). The default 1428 fits
  one Ethernet frame on IPv4 and IPv6. `"mtu"` sizes it to the MTU of the
  interface the route to the server uses (1428 when unknown).
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
- `local_address` — `(host, port)` to send from.
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

**`Server(root_or_handler, host="::", port=69, *, writable=False, create=True, overwrite=False, timeout=1.0, retries=5, options=None, max_sessions=None, reply_from_request_address=True, dally=True, on_complete=None, limits=None, ignore_broadcast=True, backoff=2.0, max_timeout=None)`**

- `root_or_handler` — a directory (wrapped in `FileSystemHandler` with
  `writable`, `create`, `overwrite`) or any handler object (see below).
- `host` — `"::"` (default) listens on IPv6 **and IPv4** with a dual-stack
  socket, falling back to `"0.0.0.0"` on a host without IPv6. `"0.0.0.0"` is
  IPv4 only; a specific address (`"192.0.2.10"`, `"[fe80::1%eth0]"`) listens
  there alone.
- `port` — `0` picks a free port; read it from `server_address`.
- `timeout`, `retries` — per transfer, unless the client negotiates `timeout`.
- `options` — a `ServerOptions` policy.
- `max_sessions` — concurrent transfers; beyond it a request gets ERROR 0
  `"server busy"`. `None` is unlimited, **except on Windows, where it defaults
  to 500** because `select()` handles at most 512 sockets.
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
  names (`blksize2`, `utimeout`, `rollover`, `cookie`) to accept tftp-hpa's
  extensions. A name not in `registry` raises `ValueError`.
- `refused` — never acknowledged even if allowed: for firmware that asks for
  an option and then mishandles it (`refused={"windowsize"}`).
- `fit_mtu` — lower `blksize` so a DATA packet fits the MTU of the interface
  the request arrived on (IP + UDP + 4-byte header): 1468 on IPv4 and 1448
  on IPv6 for a 1500 MTU. Needs pktinfo for the interface; boot ROMs often
  cannot reassemble fragments. Interface facts are cached for 30 s.
- `accepts(name)` — `name` in `allowed` and not in `refused`.

## Options, registry and profiles

Every option is an **`OptionHandler`** (`name`, `standard`) with
`negotiate(value, ctx) -> str | None` (server: the value to acknowledge, or
`None` to leave it out — RFC 2347's refusal; set fields on `ctx.result`) and
`accept(requested, acked, ctx)` (client: validate and apply; raise
`tftp.options.refuse(msg)` for ERROR 8). `ServerContext` carries `result`,
`requested`, `acked`, `policy`, `is_read`, `size`, `mtu`, `ipv6` and
`max_blksize` (policy limit after `fit_mtu`); `ClientContext` carries
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
one).

**Profiles** — `Profile(name, server, client)`: `.server` is a
`ServerOptions`, `.client` a fresh dict of `Client` keyword arguments.
`PROFILES` maps names to the five presets:

| profile | server | client |
| --- | --- | --- |
| `STRICT` | standard options | `fallback=False` |
| `DEFAULT` | standard options | library defaults |
| `PXE` | standard + `rollover`, `utimeout`; `fit_mtu=True` | `blksize=1428` |
| `HPA` | every extension | `utimeout=True` |
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
other exception is logged and sent as ERROR 0. Handlers run on the event-loop
thread: **a slow handler stalls every transfer**.

`write` receives a `memoryview` of a reused buffer when the writer is a
standard file object (`io.IOBase`) or declares `_tftp_copies_ = True`, and
`bytes` otherwise — so a writer that keeps references is safe by default.

**`RequestContext`** — `request` (the `Request`), `peer` (client address
tuple), `local_address` (the destination address as text, or `None` without
pktinfo), `interface_index` (or 0); shortcuts `filename`, `mode`, `options`.

**`FileSystemHandler(root, *, writable=False, create=True, overwrite=False, backslash=True, max_upload=None)`**

- Serves files under `root` (which must exist). Leading `/` is stripped, so
  `/boot/x` is `root/boot/x`. With `backslash`, `\` is a separator too (Windows
  boot loaders send `\boot\bcd`).
- **Containment**: `..` components are refused (ERROR 2), and the resolved
  path, symlinks included, must stay inside `root` (ERROR 2). On Windows,
  drive letters, `:` streams and reserved device names (`CON`, `NUL`, ...) are
  refused too.
- Reads: a missing file or a directory is ERROR 1.
- Writes: refused unless `writable` (ERROR 2); an existing file needs
  `overwrite` (else ERROR 6); a new file needs `create` (else ERROR 1); the
  directory must exist (directories are never created). An announced `tsize`
  above `max_upload` or the free disk space is ERROR 3, and so is an upload
  that grows past `max_upload`.
- Uploads go through `AtomicWriter`: **a failed or partial upload never
  appears or replaces anything**.
- `resolve(filename) -> str` exposes the mapping (raises `TftpError`).

**`AtomicWriter(path, overwrite=True)`** — writes to a hidden temp file beside
`path`; `close()` renames it into place (refusing with ERROR 6 if
`overwrite=False` and `path` appeared meanwhile); `abort()` deletes it.

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
standard text), **`RemoteError`** (the peer sent ERROR; always raised as
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
`Request(opcode, filename, mode, options)` (`.is_read`; option names
lower-cased, first occurrence wins, mode lower-cased), `Data(block, data)`,
`Ack(block)`, `Error(code, message)`, `OptionAck(options)`.

- **`decode(bytes) -> Packet`** — raises `MalformedPacket`. Tolerates a
  missing final NUL and drops a dangling option name.
- **`encode_request(opcode, filename, mode="octet", options=None)`**,
  **`encode_data(block, data)`**, **`encode_ack(block)`**,
  **`encode_error(code, message="")`**, **`encode_oack(options)`** — option
  values are sent as `str(value)`; NUL inside a string raises `ValueError`.
- Strings are UTF-8 with `surrogateescape`, so any byte sequence round-trips
  and real UTF-8 names decode naturally.
- Constants: `DEFAULT_BLKSIZE` 512, `MIN_BLKSIZE` 8, `MAX_BLKSIZE` 65464,
  `MAX_WINDOWSIZE` 65535, `STANDARD_OPTIONS`, `EXTENSION_OPTIONS`,
  `SUPPORTED_OPTIONS` (their union).

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
pytftp get HOST REMOTE [LOCAL|-]   [-p PORT] [-m octet|netascii] [-b BLKSIZE] [-w WINDOW]
                                   [-t TIMEOUT] [-r RETRIES] [--no-tsize] [--no-options] [-4|-6] [--json]
pytftp put HOST LOCAL|- [REMOTE]   (same options)
pytftp serve [ROOT] [-l ADDRESS] [-p PORT] [-W/--write] [--overwrite] [--no-create]
                    [--max-blksize N] [--max-windowsize N] [--max-sessions N] [--json]
```

- `-b 0` / `-w 0` request no `blksize` / `windowsize`; defaults are 1428 and 0.
- `get` writes to the remote file's basename by default; `-` is stdout.
  `put` from `-` (stdin) needs a remote name.
- Exit codes: 0 success, 1 the transfer failed (error text on stderr), 2 a
  caller error (bad argument, missing local file).
- `--json`: one JSON object on stdout per transfer (`serve` prints one line per
  completed transfer); diagnostics always go to stderr.
- `serve` logs each transfer at INFO on stderr; `-v`/`-q` adjust.

## Dependencies

`netimps` (required, no dependencies of its own; imported lazily, on the
first transfer or server): pktinfo receive (`UdpEndpoint`), socket binding,
host:port parsing, bind-error hints. Importing netimps installs its additive `recvmsg`/`sendmsg`
socket patch on Windows unless `NETIMPS_NO_SOCKET_PATCH=1` is set first.
`duho` (optional, `cli` extra).
