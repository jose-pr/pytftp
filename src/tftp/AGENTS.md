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
| RFC 2347 | option extension, OACK, ERROR 8 | unknown options are ignored, as the RFC requires |
| RFC 2348 | `blksize` 8..65464 | server clamps to its `max_blksize` |
| RFC 2349 | `timeout` (1..255 s), `tsize` | `tsize` 0 is never sent in an OACK (curl rejects it) |
| RFC 7440 | `windowsize` 1..65535 | server clamps to its `max_windowsize` (default 64) |
| tftp-hpa | `utimeout` (microseconds) | sent by the client for a fractional `timeout` |
| common | `rollover` 0/1 | block numbers wrap after 65535; file size is unlimited |
| RFC 1350 §6 | dallying | server re-ACKs a repeated last DATA for one timeout |

Not implemented: RFC 2090 multicast.

## Client

**`Client(host, port=69, *, timeout=1.0, retries=5, blksize=1428, windowsize=None, tsize=True, rollover=None, timeout_option=True, family=0, local_address=None, fallback=True, dally=False)`**

- `host` — name or address; `"[v6]"`, `"host:port"` and `"[v6]:port"` are
  accepted, and a port written there overrides `port`.
- `timeout` — seconds before a retransmission. Requested from the server as
  `timeout` when whole (1..255) and as `utimeout` when fractional, unless
  `timeout_option=False`.
- `retries` — retransmissions of one packet before `TransferTimeout`.
- `blksize` — requested; `None` asks for nothing (512). The default 1428 fits
  one Ethernet frame on IPv4 and IPv6.
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
  `ProtocolError` (the server broke the protocol, e.g. an OACK with a larger
  `blksize` than requested — the client sends ERROR 8 first), or `OSError` for
  local failures (resolution, the local file).

**`download(host, filename, dest, *, port=69, mode="octet", progress=None, **client_options)`**
and **`upload(host, filename, source, ...)`** — one-shot wrappers;
`client_options` go to `Client`.

**`MODES`** — `("octet", "netascii")`.

## Server

**`Server(root_or_handler, host="::", port=69, *, writable=False, create=True, overwrite=False, timeout=1.0, retries=5, options=None, max_sessions=None, reply_from_request_address=True, dally=True, on_complete=None)`**

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
- Raises `OSError` if the port cannot be bound (port 69 needs privileges on
  POSIX).

Lifecycle:

- **`serve_forever()`** — run the event loop in the calling thread until
  `shutdown()`. One thread serves every transfer.
- **`shutdown()`** — stop `serve_forever`; safe from any thread, a handler or
  `on_complete`. Transfers in flight get ERROR 0 `"server shutting down"`.
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

**`ServerOptions(max_blksize=65464, max_windowsize=64, allowed=SUPPORTED_OPTIONS)`**
— the negotiation policy. A larger request is answered with the maximum
(which the RFCs allow). `allowed` is a subset of `SUPPORTED_OPTIONS`; options
outside it are never acknowledged. A sender holds a whole window in memory, so
`max_windowsize * max_blksize` bounds memory per transfer. Invalid values raise
`ValueError`.

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
standard text), **`RemoteError`** (the peer sent ERROR), **`ProtocolError`**
(the peer broke the protocol; code 4, or 8 for option problems),
**`TransferTimeout`** (also a `TimeoutError`). `MalformedPacket` is a
`ValueError`.

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
  `MAX_WINDOWSIZE` 65535, `SUPPORTED_OPTIONS`.

## Netascii

Local text uses LF; on the wire a line ends CR LF and a bare CR is CR NUL.
The translation is lossless both ways (a local `\r\n` travels as `\r\0\r\n`).
**`NetasciiReader(raw)`** (`readinto`, `close`) encodes a binary reader;
**`NetasciiWriter(raw)`** (`write`, `flush`, `close`, `abort`) decodes into a
binary writer, holding a trailing CR until it sees what follows. Both handle a
CR on any block boundary. `tftp.netascii` also has `encode`, `decode` and
`encoded_size(fileobj)` (seekable; position preserved).

## Transfer engine

**`Sender(send, read, negotiated, retries, now, oack=None)`** and
**`Receiver(send, write, negotiated, retries, now, reply=None, complete=None)`**
are one side of the DATA/ACK exchange **without any I/O**: packets leave
through `send(packet)`, arrive through `handle(buffer, n, now)`, and the caller
calls `on_timeout(now)` once `deadline` (in the caller's clock) passes. State:
`done`, `error`, `bytes`, `blocks`, `retransmits`, `deadline`. Build `read`
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
