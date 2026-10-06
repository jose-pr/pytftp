# `tftp` — public API header

Header-file-style reference for the `tftp` package: every public export with
its signature, contract and gotchas, so the package can be used without
reading its source. It ships inside the package and is self-contained; read it
(or the README beside it) with `importlib.resources.files("tftp")`.
Development documentation lives with the source at
<https://github.com/jose-pr/pytftp>.

`tftp` exports what the common task needs: the clients and servers with their
asyncio twins, `download` and `upload` and the URL one-shots, the exceptions,
`TransferResult` and `TFTPURL`, the handler contract with `FilesystemBackend`,
the policy classes and `Profile`, the packet types and `decode`, and the
engine's `Sender` and `Receiver`. Every other name has one home, its role
module, and is imported from it (`from tftp.options import OptionRegistry`):
"Where names live" below lists every public module's exports. Modules starting
with `_` are internal.

Options are keyword-only: a callable takes its operands (the file name, the
host, the handler) by position and every option after them by keyword, and a
policy or record class (`TFTPServerOptions`, `TFTPServerLimits`, `Negotiated`)
takes every field by keyword.

`tftp.__version__` — the installed distribution's version.

## Where names live

| Module | Exports |
| --- | --- |
| `tftp` | `AccessViolation`, `AckPacket`, `AsyncTFTPClient`, `AsyncTFTPServer`, `AtomicWriter`, `DataPacket`, `DiskFull`, `ErrorPacket`, `FileAlreadyExists`, `FileNotFound`, `FilesystemBackend`, `IllegalOperation`, `ListEntry`, `NetasciiReader`, `NetasciiWriter`, `NoSuchUser`, `OptionAckPacket`, `OptionNegotiationError`, `Profile`, `Receiver`, `RemoteError`, `RequestPacket`, `Sender`, `TFTPClient`, `TFTPDecodeError`, `TFTPError`, `TFTPErrorCode`, `TFTPHandler`, `TFTPOpcode`, `TFTPProtocolError`, `TFTPRequestContext`, `TFTPServer`, `TFTPServerLimits`, `TFTPServerOptions`, `TFTPURL`, `TFTPValueError`, `TransferAbortedError`, `TransferResult`, `TransferTimeoutError`, `UnknownTransferID`, `WouldBlock`, `decode`, `download`, `download_url`, `upload`, `upload_url` |
| `tftp.client` | `AsyncTFTPClient`, `MODES`, `RemoteStat`, `TFTPClient`, `download`, `upload` |
| `tftp.server` | `AsyncTFTPHandler`, `AsyncTFTPReader`, `AsyncTFTPServer`, `AsyncTFTPWriter`, `AtomicWriter`, `PortRange`, `PortRangeLike`, `TFTPChunkReader`, `TFTPHandler`, `TFTPReader`, `TFTPRequestContext`, `TFTPServer`, `TFTPServerLimits`, `TFTPStats`, `TFTPWriter`, `ThreadedHandler` |
| `tftp.relay` | `RelaySummary`, `RouteFunction`, `RouteTable`, `TFTPRelay`, `Upstream`, `by_interface`, `by_prefix`, `by_subnet` |
| `tftp.capture` | `Analysis`, `CaptureFilterError`, `CaptureFormatError`, `CapturedTransfer`, `FILTER_KEYS`, `FlowTracker`, `FrameDecoder`, `LINKTYPES`, `PacketEvent`, `PcapWriter`, `UDPDatagram`, `analyze`, `compile_filter`, `live_capture_supported`, `new_session_id`, `read_datagrams`, `read_frames`, `sniff`, `summarize` |
| `tftp.options` | `BUILTIN_OPTIONS`, `Blksize2Option`, `BlksizeOption`, `ClientOptionContext`, `CookieOption`, `DEFAULT_BLKSIZE`, `DEFAULT_REGISTRY`, `EXTENSION_OPTIONS`, `LISTING_OPTIONS`, `MAX_BLKSIZE`, `MAX_UTIMEOUT`, `MAX_WINDOWSIZE`, `MIN_BLKSIZE`, `MIN_UTIMEOUT`, `MstfwindowOption`, `Negotiated`, `OptionHandler`, `OptionRegistry`, `PROFILES`, `Profile`, `RolloverOption`, `STANDARD_OPTIONS`, `SUPPORTED_OPTIONS`, `ServerOptionContext`, `TFTPServerOptions`, `TimeoutOption`, `TsizeOption`, `UtimeoutOption`, `WindowsizeOption`, `XListOption`, `XMtimeOption`, `accept_oack`, `negotiate`, `refuse`, `register_option`, `request_options` |
| `tftp.packet` | `AckPacket`, `DataPacket`, `ErrorPacket`, `FILENAME_ENCODING`, `OptionAckPacket`, `RequestPacket`, `TFTPErrorCode`, `TFTPOpcode`, `TFTPPacket`, `decode`, `encode_ack`, `encode_data`, `encode_error`, `encode_oack`, `encode_request` |
| `tftp.backends` | `FilesystemBackend`, `HTTPBackend`, `MemoryBackend`, `Pipe`, `UpstreamBackend`, `normalize_name` |
| `tftp.path` | `TFTPPath`, `TFTPURIPath` |
| `tftp.exceptions` | `AccessViolation`, `CaptureFilterError`, `CaptureFormatError`, `DiskFull`, `FileAlreadyExists`, `FileNotFound`, `IllegalOperation`, `NoSuchUser`, `OptionNegotiationError`, `RemoteError`, `TFTPDecodeError`, `TFTPError`, `TFTPProtocolError`, `TFTPValueError`, `TransferAbortedError`, `TransferTimeoutError`, `UnknownTransferID`, `WouldBlock` |
| `tftp.cli` | `CaptureCmd`, `Get`, `Ls`, `Put`, `Pytftp`, `RelayCmd`, `Serve`, `run` |
| `tftp.transfer` | `Receiver`, `Sender`, `Transfer`, `as_readinto`, `as_write` |
| `tftp.uri` | `TFTPURL`, `download_url`, `upload_url` |
| `tftp.listing` | `DirectoryListing`, `LIST_OPTION`, `ListEntry`, `MTIME_OPTION`, `dumps`, `loads` |
| `tftp.netascii` | `NetasciiReader`, `NetasciiWriter`, `decode`, `encode`, `encoded_size` |
| `tftp.result` | `TransferResult` |

## Protocol coverage

| Spec | What | Notes |
| --- | --- | --- |
| RFC 1350 | RRQ, WRQ, DATA, ACK, ERROR; octet and netascii | `mail` mode is refused with ERROR 4 |
| RFC 1123 4.2.3.1 | Sorcerer's Apprentice fix | duplicate ACKs never trigger a resend (windowsize 1); with a window, an ACK that moves it on sends only the new blocks, and an ACK resends blocks in flight at most once per two windows of progress, so one duplicated, lost or late ACK costs one window of DATA, not every window after it |
| RFC 1123 4.2.3.2 | exponential backoff | each consecutive retransmission waits `backoff` times longer, capped |
| RFC 1123 4.2.3.4 | broadcast requests ignored | server, when pktinfo reports the destination |
| RFC 2347 | option extension, OACK, ERROR 8 | unknown options are ignored, as the RFC requires |
| RFC 2348 | `blksize` 8..65464 | server clamps to its `max_blksize` |
| RFC 2349 | `timeout` (1..255 s), `tsize` | `tsize` 0 is never sent in an OACK (curl rejects it) |
| RFC 7440 | `windowsize` 1..65535 | server clamps to its `max_windowsize` (default 64) |
| tftp-hpa | `blksize2`, `utimeout`, `rollover`, `cookie` | **off unless a server allows them** (`TFTPServerOptions(allowed=...)`, a profile) |
| Microsoft | `mstfwindow` (bootmgr/WDS variable window) | off unless allowed; runs a fixed window of 4 (the in-transfer resize is unpublished) |
| pytftp | `x-list` (directory listing), `x-mtime` (modification time) | off unless allowed (`LISTING_OPTIONS`); other servers ignore them |
| — | block-number rollover | blocks wrap after 65535 (to 0, or 1 with `rollover`); file size is unlimited |
| RFC 1350 §6 | dallying | server re-ACKs a repeated last DATA for one timeout |

Not implemented: RFC 2090 multicast, PXE MTFTP.

## Client

**`TFTPClient(host, port=69, *, timeout=1.0, retries=5, blksize=1428, windowsize=None, tsize=True, rollover=None, timeout_option=True, family=0, src=None, fallback=True, dally=False, backoff=2.0, max_timeout=None, deadline=None, strict_source=True, utimeout=False, extra_options=None, registry=None, on_negotiated=None)`**

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
- `retries` — retransmissions of one packet before `TransferTimeoutError`.
- `backoff`, `max_timeout` — each consecutive retransmission (of the
  request too) waits `backoff` times longer, up to `max_timeout` (default
  8 × `timeout`; below `timeout` is a `ValueError`); progress resets the wait. `backoff=1` disables it; below 1 is a `ValueError`.
- `deadline` — seconds a whole transfer may take, counted from when it
  starts; `None` is unlimited. A relative duration: the client's
  `deadline` argument and attribute are not the engine's `deadline` (the
  instant it next wants `on_timeout`, see "Transfer engine").
- `strict_source` — the first answer must come from the address the request
  was sent to; addresses are compared by value (packed address, and scope id
  when both sides have one), never by their text, so a link-local server named
  with its zone (`fe80::1%7`) is heard. `False` accepts a multi-homed server
  answering from another address (the transfer then locks on to that address
  and port). A datagram of any length from any other address is answered with
  ERROR 5 and does not disturb the transfer.
- `blksize` — requested; `None` asks for nothing (512). The default 1428 fits
  one Ethernet frame on IPv4 and IPv6. `"mtu"` sizes it to the MTU of the
  interface the route to the server uses (1428 when unknown).
- `on_negotiated(negotiated, peer)` — called once the server has answered
  the request (OACK, first DATA or ACK 0) and **before any data moves**;
  `peer` is the server's transfer address. If it raises, the server is sent
  an ERROR (the `TFTPError`'s code, else 0) and the exception propagates.
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
- `src` — `(address, port)` to send from; the address in any form
  `host` takes.
- `fallback` — if the server answers the *request* with ERROR 8 (options
  refused), ask again once without options.
- `dally` — after the last ACK of a download, keep re-ACKing a repeated last
  DATA for one timeout. Costs that much time per download.
- Arguments are validated at construction, with no I/O and no name
  resolution (the host text is read when the transfer starts): an
  out-of-range `blksize`, `windowsize` or `rollover`, a `port` outside
  1..65535, a negative `retries`, a `timeout` or `deadline` that is not
  positive, a `backoff` below 1, a `family` other than `0`, `AF_INET` and
  `AF_INET6`, and a `src` that is not an `(address, 0..65535)` pair raise
  `ValueError`; a wrong type (a `str` port, a fractional `retries`, a
  `bool` for a number, a `src` that is not a pair) raises `TypeError`.
- **Not thread-safe**: one `TFTPClient` per thread. Each transfer opens its own
  socket, so sequential transfers on one client are fine.

Methods (each returns a `TransferResult` unless noted):

- **`download(filename, dst, *, mode="octet", progress=None)`** — `dst` is a
  path or a writable binary file. A path is created/truncated and **removed
  again if the transfer fails**.
- **`get(filename, *, mode="octet") -> bytes`** — download into memory.
- **`upload(filename, src, *, mode="octet", progress=None)`** — `src` is
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
- **`path(*segments, mode="octet") -> TFTPPath`** — see Paths (needs the
  `path` extra).
- `mode` is `"octet"` or `"netascii"`; `"binary"`/`"ascii"` are aliases. Any
  other value raises `ValueError`.
- `progress(done_bytes, total_or_None)` is called after each packet that moved
  data; `total` is the negotiated `tsize`.
- Raises `RemoteError` (the server sent ERROR), `TransferTimeoutError`,
  (as the subclass for its code: `FileNotFound`, `AccessViolation`, ...),
  `TFTPProtocolError` (the server broke the protocol, e.g. an OACK with a larger
  `blksize` than requested — the client sends ERROR 8 first, or a DATA longer
  than the negotiated `blksize`), or `OSError` for local failures (resolution,
  the local file, a send the host refuses: the asyncio client raises it at
  once and does not wait out the retries).

**`download(host, filename, dst, /, *, port=69, mode="octet", progress=None, **client_options)`**
and **`upload(host, filename, src, /, ...)`** — one-shot wrappers;
`client_options` go to `TFTPClient`. The operands are positional-only, so
`src=` in `client_options` is the client's source address, not the data.

**`MODES`** — `("octet", "netascii")`.

## Server

**`TFTPServer(root_or_handler, *, host=None, port=69, writable=False, create=True, overwrite=False, timeout=1.0, retries=5, options=None, max_sessions=500, reply_from_request_address=True, dally=True, on_complete=None, limits=None, ignore_broadcast=True, backoff=2.0, max_timeout=None, open_in_thread=None, workers=8, port_range=None, interface=None)`**

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
- **`wait_closed(timeout=None) -> bool`** — block until serving has stopped
  and the `start()` thread has ended; `False` when `timeout` seconds passed
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
- Socket buffers are grown to hold two windows (Windows defaults to 64 KiB,
  which a large window overflows).

**`PortRange(low, high)`** — a frozen, hashable value (`low`, `high`; equal
to another `PortRange` only): `len()` counts the ports, iteration and `in`
cover them in order, `str()` is `LOW:HIGH`, `PortRange.parse("LOW:HIGH")`
(`LOW-HIGH` too) reads it back and `PortRange.try_parse(text, default=None)`
returns `default` instead of raising. `TFTPValueError` outside 1..65535 or with
`low > high` or for text that is not a range, `TypeError` for a non-`int` or
non-`str`. **`PortRangeLike`** (`tftp.server`) is what `port_range=` takes in
place of a `PortRange`: a `(low, high)` pair, a `range` of step 1 or the text.

**`TFTPServerLimits(*, max_request_size=1024, max_filename_length=512, max_options=16, max_option_length=255, max_sessions_per_client=None, max_duration=None, max_idle=60.0)`**
— bounds on untrusted input. A request over a size/name/option limit gets
ERROR 4 from the listening port; a client over `max_sessions_per_client`
(counted per address, any port) gets ERROR 0 `"server busy"`.
`max_duration` ends a transfer that runs longer (ERROR 0 to the peer,
`TransferTimeoutError` in the result). **`max_idle`** (seconds, default 60; `None`
for no bound) ends a transfer that has had no datagram from its peer for that
long, with `TransferTimeoutError`, whatever `timeout` the client negotiated: it
counts the peer's silence, not the transfer's length, and not time the
handler keeps the server waiting. A transfer holds memory for the blocks it
has read, not for the window it negotiated, and none of it once it ends.
`TFTPServerLimits`, `TFTPServerOptions` and `Negotiated` are mutable: they
compare equal when every field does, are unhashable and print their fields.

**`TFTPServerOptions(*, max_blksize=65464, max_windowsize=64, max_window_bytes=4 MiB, allowed=None, refused=(), fit_mtu=False, registry=None)`**
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
`tftp.options.refuse(msg)` for ERROR 8). `ServerOptionContext` carries `result`,
`requested`, `acked`, `policy`, `is_read`, `size`, `mtu`, `ipv6`, `stream`
(what the handler opened for an RRQ, else `None`) and `max_blksize` (policy
limit after `fit_mtu`); `ClientOptionContext` carries
`result`, `requested`, `is_read`. Custom values go in `ctx.result.extra`.

**`OptionRegistry(handlers=BUILTIN_OPTIONS)`** — `register(handler, *,
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
one), `mstfwindow` (`MstfwindowOption`: answers `31416` with `27182` and runs a
window of 4 unless `windowsize` was also acknowledged; a client refuses any
other answer), `x-list` (`XListOption`: acknowledged `1` only when the RRQ's stream
is a listing, `lists_directories = True`), `x-mtime` (`XMtimeOption`: an RRQ's OACK carries
the stream's `mtime` attribute or `fstat` time, whole seconds; omitted when
unknown). `stream_mtime(stream)` is that lookup.

**Profiles** — `Profile(name, server, client)`: `.server` is a fresh copy of
the `TFTPServerOptions` and `.client` a fresh dict of `TFTPClient` keyword
arguments, so changing what either returns changes no profile.
The five presets are class attributes of `Profile`; `PROFILES` maps their names
(`"strict"`, `"default"`, `"pxe"`, `"hpa"`, `"legacy"`) to them:

| profile | server | client |
| --- | --- | --- |
| `Profile.STRICT` | standard options | `fallback=False` |
| `Profile.DEFAULT` | standard options | library defaults |
| `Profile.PXE` | standard + `rollover`, `utimeout`; `fit_mtu=True` | `blksize=1428` |
| `Profile.HPA` | tftp-hpa's extensions | `utimeout=True` |
| `Profile.LEGACY` | standard, `windowsize` refused | no options at all, `strict_source=False` |

```python
tftp.TFTPServer("/srv/tftp", options=tftp.Profile.PXE.server)
tftp.TFTPClient("192.0.2.1", **tftp.Profile.LEGACY.client)
```

## Handlers

The contract is a set of `typing.Protocol`s in `tftp.server`; the members
listed as optional are looked up once per request or transfer, and a class
that lacks them is still a valid implementation. A **plain** handler is for
`TFTPServer`; the asyncio server takes the coroutine contract below.

**`TFTPHandler`** — an object with:

- **`open_read(context) -> reader`** — a `TFTPReader` (required: `readinto`,
  `close`) or a `TFTPChunkReader` (required: `read`, `close`). Optional:
  `size` (an integer; `tsize` is also answered from a real `fileno()` or a
  seekable file), `mtime`, `set_wakeup`, `lists_directories`.
- **`open_write(context, size) -> writer`** — a `TFTPWriter` (required:
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

**`TFTPRequestContext(request, peer, *, local_address=None, interface_index=0)`** — `request` (the `RequestPacket`), `peer` (client address
tuple), `local_address` (the destination address as text, or `None` without
pktinfo), `interface_index` (or 0), `interface` (that interface as a
`netimps.Interface` — name, addresses, MTU — or `None`), `listing` (the RRQ asks for `x-list=1`
and the server allows it); shortcuts `filename`, `mode`, `options`;
`with_filename(name)` — a copy for a request naming `name`, everything else
carried over.

**`FilesystemBackend(root, *, writable=False, create=True, overwrite=False, backslash=True, max_upload=None)`** — in `tftp.backends`, and exported from the root

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
- `resolve(filename) -> str` exposes the mapping (raises `TFTPError`).

**`tftp.listing`** — the `x-list` format: UTF-8 lines `<f|d> <size> <mtime|-> <name>`
(`%`, CR, LF in names as `%25`, `%0D`, `%0A`). **`ListEntry(name, is_dir,
size, mtime=None)`** (a named tuple: it is only handed out),
**`dumps(entries) -> bytes`**, **`loads(data) -> list`** (malformed lines
skipped),
**`DirectoryListing(directory, *, root=None)`** (a `BytesIO` with `size`,
`mtime`, `lists_directories`; sorted by name; leaves out symlinks resolving
outside `root` and in-progress uploads `.name.*.part`), `LIST_OPTION`,
`MTIME_OPTION`.

**`AtomicWriter(path, *, overwrite=True)`** — writes to a hidden temp file beside
`path`; `close()` renames it into place (refusing with ERROR 6 if
`overwrite=False` and `path` appeared meanwhile); `abort()` deletes it.

## asyncio

**`AsyncTFTPClient(...)`** (from `tftp` and `tftp.client`) — a sibling of
`TFTPClient`, not a subclass: the same arguments and rules (options, backoff,
fallback, `trace`, `on_negotiated`); coroutine methods `download(filename,
dst, *, mode, progress)`, `get`, `upload(filename, src, *, mode,
progress)`, `put`, `size`, `stat` (both in the executor), `listdir`, and the async generator **`stream(filename, *, mode,
buffer=1 MiB)`** yielding chunks as they arrive (a slow consumer holds ACKs
back; at most `buffer` bytes are held). Name resolution runs in the
executor. Destinations: a path (written on the loop — fine for local
files) or an object with `async write(data)`; `download` returns only once
that writer has taken every byte (it is not closed). Sources: a path, bytes
or an object with `async read(n)`. Cancelling the task sends the server ERROR 0. Each
attempt (including the option fallback) uses a fresh socket.

**`AsyncTFTPServer(root_or_handler, *, host=None, port=69, **server_options)`**
(from `tftp` and `tftp.server`) — `TFTPServer`'s arguments except
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
  handler wrapped in **`ThreadedHandler(handler, *, executor=None)`**
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
- `max_sessions` defaults to 500; `None` is 510 on Windows (a selector
  loop's `select()`).

## Paths (`tftp.path`, `path` extra)

`pip install tftp[path]` adds `pathlib-next[uri]`. Importing `tftp` never
loads it.

**`TFTPPath(*segments, client, mode="octet")`** — a `pathlib_next.Path`
bound to a `TFTPClient` (`client.path("boot", "x")`). Joined like a
`PurePosixPath` (`\` counts as `/`); the path text is the filename sent,
so `/boot/x` and `boot/x` stay distinct. Joining onto a `TFTPPath` keeps its
client; `with_client()`, `with_mode()`; `client`, `transfer_mode`. Equality
and hashing include the server (host, port). `as_uri()` is the `tftp://`
URL. `relative_to()` works on the path text. A path is synchronous: binding
it to an `AsyncTFTPClient` (`TFTPPath(..., client=)`, `with_client()`) raises
`TypeError`, and `AsyncTFTPClient` has no `path()`.

**`TFTPURIPath`** — the `tftp://host[:port]/path[;mode=netascii]` scheme
for `pathlib_next.uri.UriPath`, registered through the
`pathlib_next.schemes` entry point, so `UriPath("tftp://...")` returns one
without importing `tftp`. `filename`, `transfer_mode`;
`with_options(**client_options)` / `with_client(client)` choose the
`TFTPClient` (default: the URI's host and port, library defaults). The URI host
may be an address object; `TFTPClient` accepts one. The URI's transfer options
(`;name=value` on the last segment, or a `?name=value&name=value` query; the
`TFTPURL` grammar) apply to its operations: `with_options` keywords win over
them, a client given to `with_client` is used as it is, and text that grammar
refuses raises `TFTPValueError` from the operation. The path is decoded before
it is read, so a `;` in a file name cannot be told from a parameter: write such
a value in the query.

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
- `stat()` is one `TFTPClient.stat()` probe (`FileStat`: `st_size` and
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

- **`MemoryBackend(files=None, *, writable=False, overwrite=True, max_upload=None)`**
  — serves `files` (name → bytes; names normalised so `/a\\b` is `a/b`);
  uploads replace an entry only when complete. Thread-safe `files` updates.
- **`HTTPBackend(base_url=None, *, url_for=None, writable=False, headers=None, timeout=10.0, buffer=1 MiB, opener=None)`**
  — TFTP-to-HTTP(S) gateway on `urllib`: GET `base_url + quote(name)`
  (or `url_for(context)`), streamed; `Content-Length` answers `tsize`;
  WRQ → `PUT` (chunked without `tsize`) when `writable`; the final ACK
  waits for the PUT's response. HTTP 404/410 → ERROR 1, 401/403 → 2,
  409 → 6, 413/507 → 3, other → 0. `..` in a name → ERROR 2.
- **`UpstreamBackend(upstream, *, client_options=None, buffer=1 MiB, stall_timeout=30.0, writable=False)`**
  — terminating proxy: `upstream` is `"host"`, `"host:port"`, an address
  object or `netimps.Host`, `(host, port)`, or `upstream(context)` returning
  one. Each side
  negotiates independently (`client_options` for the upstream `TFTPClient`);
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

**`TFTPRelay(route, *, host=None, port=69, idle_timeout=30.0, max_duration=3600.0, linger=2.0, upstream_src=None, limits=None, max_sessions=None, ignore_broadcast=True, reply_from_request_address=True, trace=None, on_session_end=None, port_range=None, interface=None)`**
— a transparent application relay (there is no standard TFTP relay). The
request is forwarded **byte for byte** from a fresh upstream-side socket; the
upstream's TID is learned from its first answer (from the address asked,
compared by value, so an upstream named by a link-local address with its zone
answers);
datagrams then cross unchanged between `client <-> relay(client-side TID)` and
`relay(upstream-side TID) <-> upstream`, so options and extensions this
library does not know work end to end. Each side negotiates nothing with the
relay — the client sees the upstream's OACK. For different settings per side
use `UpstreamBackend` (a terminating proxy) instead.

- `interface` — listen on one adapter, as for `TFTPServer`.
- `port_range` — as for `TFTPServer`, for both sockets of each relayed transfer
  (client side and upstream side).
- `route` — an upstream (`"host"`, `"host:port"`, `"[v6]:port"`, an address
  object or `netimps.Host`, `(host, port)`, `Upstream`) or `route(request, context) -> upstream | None`;
  `None` refuses with ERROR 2 `"no route"`. `RouteFunction` is the type of that
  callable (`tftp.relay.RouteFunction`). Hostnames are resolved per
  request (cached 60 s). Routes run on the relay's loop: keep them fast.
- A transfer ends on: an ERROR either way (after ≤1 s), the final DATA/ACK
  exchange (the block size followed from an OACK's `blksize`/`blksize2`, then
  `linger`), `idle_timeout` without traffic, or `max_duration`. Keep
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
- Lifecycle and properties as for `TFTPServer`: `bind`, `serve_forever`,
  `shutdown`, `start`, `wait_closed`, `close`, context manager;
  `server_address` (`None` before `bind()`), `has_pktinfo`, `active_sessions`.

Routing helpers (`tftp.relay`): **`Upstream(host, port=69)`** — a frozen,
hashable value that equals only another `Upstream` (`port` an `int` in
1..65535: `TypeError`, or `TFTPValueError` outside the range) and
**`Upstream.parse(value)`**, which takes a host (`host` read by
`netimps.split_host`: brackets dropped, a `"host:port"` split, the port ASCII
digits only), an address or `netimps.Host` (port 69), `(host, port)` or an
`Upstream`;
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
methods `decode()`, `to_dict(payload=False)` (JSON-
ready metadata; DATA payloads only as hex with `payload=True`);
`str(event)` is the human line. `summarize(data)` is the one-line description on its own.

**Trace hooks** — `TFTPClient(trace=)`, `TFTPServer(trace=)`, `TFTPRelay(trace=)` take
`trace(PacketEvent)`, called for every datagram received and sent, on the
thread doing the I/O; exceptions it raises are logged, never propagated.
Roles are `"client"`, `"server"`, `"relay"`; one `session` id per transfer
(`c…`, `s…`, `r…`). A server traces a transfer's request as arriving at the
listening address. Requests a server refuses before a transfer exists are
not traced. Off (`None`) costs nothing per packet.

**`PcapWriter(path_or_binary_file)`** — writes events (it is a ready trace
hook: `TFTPServer(..., trace=PcapWriter("t.pcap"))`) or `write(time, source,
destination, payload)` as pcap, link type RAW, with synthesized IPv4/IPv6 and
UDP headers and valid checksums, so Wireshark/tshark decode it as TFTP
(v4-mapped addresses are written as IPv4). Context manager; `close()`.

**Reading captures** — pcap and pcapng (both byte orders, µs/ns and
`if_tsresol`, several interfaces), from a path or a stream (a live
`tcpdump -i eth0 -U -w - udp` pipe). Link types: Ethernet (VLAN/QinQ),
RAW, IPv4, IPv6, Linux SLL/SLL2, BSD NULL/LOOP. IPv4 and IPv6 fragments are
reassembled (a large `blksize` fragments on the wire). Non-UDP is skipped.

- **`read_frames(source) -> Iterator[(time, linktype, frame)]`**,
  **`read_datagrams(source) -> Iterator[UDPDatagram(time, source, destination, payload)]`**
  — `CaptureFormatError` (a `TFTPValueError`) for anything that is not a capture.
- **`FlowTracker(ports=(69,), keep_payloads=True)`** — `feed(datagram) ->
  PacketEvent | None` (role `"capture"`, direction `"seen"`; `None` for UDP
  that is neither TFTP traffic of a known transfer nor to/from a request
  port), `feed_all(datagrams)`, `.transfers`. A transfer starts at an
  RRQ/WRQ to a request port and follows the server's answer (its TID; from
  another address too, within 10 s) between that TID and the client's
  address/port. `::ffff:a.b.c.d` and `a.b.c.d` count as one host.
  `keep_payloads=False` drops the DATA payloads (`CapturedTransfer.data()` is
  then empty); `size`, `retransmissions` and `missing_blocks` are counted
  either way.
- **`CapturedTransfer`** — `session`, `client`, `server`, `server_tid`,
  `filename`, `mode`, `operation`, `requested`, `acknowledged`, `blksize`,
  `windowsize`, `tsize`, `error` (`(code, message, "client"|"server")`),
  `is_complete` (final DATA seen and ACKed), `packets`, `retransmissions` (DATA
  seen again), `request_retransmissions`, `size` (the DATA bytes; `"bytes"`
  in `to_dict()`), `missing_blocks`,
  `started`, `ended`, `duration`; `data(decode_netascii=True)` returns the
  file up to the first gap (block numbers followed across rollover);
  `to_dict()`.
- **`analyze(source, *, ports=(69,), filter=None, keep_payloads=True) -> Analysis(events, transfers)`**
  — a whole capture (path, stream, or datagrams) at once.
- **`sniff(interface=None, *, stop=None) -> Iterator[UDPDatagram]`** — live,
  Linux only (`AF_PACKET`, needs `CAP_NET_RAW`); `live_capture_supported()`.
  Elsewhere pipe `tcpdump`/`dumpcap -w -` into `read_datagrams`.

**Filters** — **`compile_filter(text) -> predicate(event)`**: `key=value`
clauses joined by `and`, `,` for "any of", `!=` to negate; empty matches all.
Keys: `op` (opcode name), `host`/`src`/`dst` (address, CIDR, `addr:port`,
`[v6]:port`, `:port`; mapped v4 matches v4), `port`, `file` (shell pattern,
requests only), `block`, `code` (ERROR code), `session`, `leg`, `direction`.
`CaptureFilterError` (a `TFTPValueError`) for an unknown key or malformed value.

## Results and errors

**`TransferResult`** — `filename`, `operation` (`"read"` for RRQ, `"write"`
for WRQ, on both sides), `mode`, `peer`, `local`, `bytes` (payload bytes on
the wire; netascii-encoded size in netascii mode), `blocks`, `retransmits`,
`duration` (seconds), `negotiated`, `error` (or `None`); properties `is_ok` and
`throughput` (bytes/s).

**`Negotiated(*, blksize=512, windowsize=1, timeout=1.0, tsize=None, rollover=0, options=None, extra=None)`** — what a transfer ran with: `blksize`, `windowsize`,
`timeout` (seconds), `tsize` (or `None`), `rollover`, and `options` (the OACK
as sent/received; empty when RFC 1350 defaults applied).

Exceptions, all defined in `tftp.exceptions` (every one but the two capture
errors is also importable from `tftp`): every one is a **`TFTPError`** except
`WouldBlock`, so `except TFTPError` catches what the library reports on its
own account. A caller's own mistake (a wrong
argument type, an option out of range) is plain `TypeError` or `ValueError`,
not one of these. `TFTPError` plays three roles, told apart by the subclass:

- *What a handler raises to refuse a request*: **`TFTPError(code=0,
  message="")`** itself, or `TFTPProtocolError`; the code and message become
  the ERROR the client receives. `.code` is a `TFTPErrorCode` when the value is
  known, `.message` defaults to the code's standard text; `code` must be an
  `int` in 0..65535 and `message` text, else `TypeError` or `ValueError` at
  construction, so `TFTPError("no such file")` is refused rather than built
  with the text as its code.
- *What a client raises for the server's ERROR*: **`RemoteError`**, always
  raised as the subclass for its code (`FileNotFound` 1, `AccessViolation` 2,
  `DiskFull` 3, `IllegalOperation` 4, `UnknownTransferID` 5,
  `FileAlreadyExists` 6, `NoSuchUser` 7, `OptionNegotiationError` 8) and plain
  `RemoteError` for 0 and unknown codes; `RemoteError.from_code(code,
  message)` (a classmethod) builds one. The leaves describe the *server's* file, so none is
  also a `FileNotFoundError` or `PermissionError`; `tftp.path` raises those.
  Also **`TFTPProtocolError(message, code=4)`** (the peer broke the protocol;
  code 4, or 8 for option problems), **`TransferTimeoutError(message)`** (also
  a `TimeoutError`, with `errno` `None`; retries exhausted or the client's `deadline`
  passed) and **`TransferAbortedError(message)`** (cancelled locally:
  `abort()`, server shutdown), both with code 0.
- *What `TransferResult.error` holds*: whichever of the above ended the
  transfer, or `None`.

Malformed text raises **`TFTPValueError`**, also a `ValueError`:
**`TFTPDecodeError`** (bytes that are not a packet; `.code` is 4),
`CaptureFormatError` and `CaptureFilterError` (both in `tftp.capture` too) and
`TFTPURL.parse`'s refusals. `str()` of these is the message alone. **`WouldBlock`**
is a `BlockingIOError`, a signal that a source or sink has nothing ready.

Every exception copies and pickles (`copy.copy`, `multiprocessing`), a
`TransferResult` holding one included. `TFTPError.from_exception(exc)`
(a classmethod, returning a `TFTPError`) maps any exception to the ERROR to
send: a `TFTPError` as is, an `OSError` by errno with the generic text (never
the OS text, which would disclose server paths).

## Wire format

`TFTPOpcode` (`RRQ`=1 … `OACK`=6) and `TFTPErrorCode` (`NOT_DEFINED`=0 …
`OPTION_REFUSED`=8) are `IntEnum`s; `TFTPErrorCode(n)` for any other `n` in
0..65535 is an unnamed member (`.name` is `CODE_<n>`) carrying the number, so
a peer's code is forwarded as it came, and `ValueError` outside the range.

The packet types are frozen, hashable values that equal only their own type
(a packet is not a tuple, and `AckPacket(5) != (5,)`): `RequestPacket(opcode,
filename, mode, options={}, raw=b"")`, `DataPacket(block, data)`,
`AckPacket(block)`, `ErrorPacket(code, message)` (`code` a `TFTPErrorCode`),
`OptionAckPacket(options)`. Each has a `decode(data)` classmethod (a datagram
of another kind raises `TFTPDecodeError`), `encode()` and `bytes(packet)`:
`bytes(AckPacket(5)) == b"\x00\x04\x00\x05"`. `options` is a read-only
mapping with lower-cased names and text values (an `int` value is stored as
its decimal text); a block is an `int` in 0..65535 (`ValueError`, or
`TypeError` for a non-`int`). A request has `.is_read`; its mode is
lower-cased; `raw` is the datagram as received and is not part of equality;
`.raw_options` lists every pair as sent (original case, order, duplicates) and
`.encode()` returns `raw` or a fresh encoding, so a relay forwards unknown
options untouched. `repr()` of a packet is a constructor call.

- **`decode(bytes) -> TFTPPacket`** — dispatches on the opcode; raises
  `TFTPDecodeError`. Liberal: tolerates a missing final NUL, drops a dangling
  option name, accepts any mode and any error code.
- **`encode_request(opcode, filename, *, mode="octet", options=None)`**,
  **`encode_data(block, data)`**, **`encode_ack(block)`**,
  **`encode_error(code, message="")`**, **`encode_oack(options)`** — strict,
  and what the packet types' `encode()` call; they build the bytes without
  a packet object, for a caller on a hot path. `ValueError` for an empty file or option name, a NUL in a string, a mode
  other than `netascii`, `octet` or `mail`, a request over 512 octets (RFC
  2347), an OACK with no options, a block or error code outside 0..65535 and
  an error message over 512 octets; `TypeError` for a wrong type, an option
  value that is not text or an `int` (`None`, `True`, a float) included.
- Strings are UTF-8 with `surrogateescape`, so any byte sequence round-trips
  and real UTF-8 names decode naturally.
- Constants: `DEFAULT_BLKSIZE` 512, `MIN_BLKSIZE` 8, `MAX_BLKSIZE` 65464,
  `MAX_WINDOWSIZE` 65535, `STANDARD_OPTIONS`, `EXTENSION_OPTIONS`,
  `LISTING_OPTIONS` (`x-list`, `x-mtime`), `SUPPORTED_OPTIONS` (standard and
  extensions).

## URIs (RFC 3617)

- **`TFTPURL(host, port, filename, mode="octet", options=None)`** — a frozen,
  hashable value that equals only another `TFTPURL`; it is not a tuple. The
  constructor validates and normalises: `host` is lower-cased text (an
  `ipaddress` address or interface, or a `netimps.Host`, is reduced to its
  text; an IPv6 literal is compressed and its zone kept), `port` an `int` in
  0..65535 (`0` is the default port, 69), `mode` `"octet"` or `"netascii"`
  (any case), `filename` non-empty text without a NUL, `options` a mapping of
  option name to text (an `int` value is written in decimal), kept as a
  read-only `Mapping[str, str]` with lower-case names, part of equality and
  the hash and in the `repr` only when not empty. A wrong type raises
  `TypeError`, a value no URL can carry `TFTPValueError`. `str(url)` is the
  URL (IPv6 hosts bracketed, a zone written `%25`, the port only when it is not
  69) in the `;` spelling: `;mode=netascii` first and only for netascii, then
  `;name=value` for each option in name order, names and values
  percent-encoded. A URL with a mode and no options is exactly RFC 3617's form.
  `str()` imports nothing: `TFTPURL.parse(str(url)) == url`.
- **`TFTPURL.parse(text)`** — RFC 3617's `"tftp://" host "/" file [ mode ]`
  plus an optional `:port` (default 69; `0` and an empty port mean 69), `/`
  inside the file name, and options. The file name and the option names and
  values are percent-decoded as UTF-8 with `surrogateescape` (the packet
  codec's own, so any octet sequence round-trips); a `%25` zone decodes.
  Raises `TFTPValueError` for another scheme, no host or file, a fragment
  (`#`), userinfo, a non-digit or out-of-range port, a control character, a NUL
  in the file name, both option delimiters unencoded, a repeated name, an
  empty pair, a pair with no `=`, an option value its name cannot read, and
  `mode=mail` (write `%23` for a literal `#`); `TypeError` for a non-`str`.
  **`TFTPURL.try_parse(text, default=None)`** returns `default` instead of
  raising `TFTPValueError`.
- **Options in a URL are this library's extension** (RFC 3617 defines `;mode=`
  and nothing else; another tool reads the text after `?` as part of the file
  name, and curl looks for `;mode=` only). Two spellings; whichever of `?` and
  `;` comes first after the file name decides how the rest is read:
  `tftp://h/f?blksize=1428&windowsize=16` (after `?`, `name=value` pairs
  separated by `&`) is the same URL as `tftp://h/f;blksize=1428;windowsize=16`
  (after `;`, separated by `;`). `mode` is a name in both. The other
  spelling's delimiter inside the list is refused: write `;` and `?` in a
  value as `%3B` and `%3F` (`&` in the `;` spelling is plain text). Names are
  compared without case and stored lower-case; a repeated one is refused.

  | Name | Read as the `TFTPClient` keyword | Value |
  | --- | --- | --- |
  | `mode` | the transfer mode | `octet` or `netascii` |
  | `blksize` | `blksize` | `mtu`, or ASCII digits within 8..65464 |
  | `windowsize` | `windowsize` | ASCII digits, 1..65535 |
  | `timeout` | `timeout` | seconds, ASCII digits with an optional fraction, above 0 |
  | `tsize` | `tsize` | `1`, `0`, `true` or `false` |
  | `rollover` | `rollover` | `0` or `1` |
  | any other | `extra_options`, requested verbatim | any text |

  A value a known name cannot read, or whose range the client refuses, raises
  `TFTPValueError` when the URL is built, not at the transfer.
- **`download_url(url, dst, /, *, progress=None, **client_options)`**,
  **`upload_url(url, src, /, ...)`** — one-shot transfers by URL; the operands
  are positional-only, so `src=` in `client_options` is the client's source
  address. The URL's options become client keywords; a keyword in
  `client_options` wins over the URL's option of the same name, and
  `extra_options` merge name by name with the keyword winning. The command
  line (`pytftp get|put|ls tftp://...`) and `TFTPURIPath` follow the same
  rule: a flag or a `with_options` argument wins over the URL.

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
(`kw`: `backoff=2.0`, `max_timeout=None`, `expires=None` — the instant, in
the caller's clock, at which the transfer is abandoned)
are one side of the DATA/ACK exchange **without any I/O**: packets leave
through `send(packet)`, arrive through `handle(buffer, n, now)`, and the caller
calls `on_timeout(now)` once `deadline` passes (here `deadline` is an
attribute: the instant, in the caller's clock, the engine next wants to be
called; the client's `deadline` argument is a number of seconds), and
`resume(now)` when a stalled source/sink (`WouldBlock`) is ready again;
`abort(message)` cancels. State: `is_done`, `error`, `is_stalled`, `bytes`,
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

- `-b 0` / `-w 0` request no `blksize` / `windowsize`; without a flag the
  URL's option is used, else 1428 and none. A flag wins over a URL's option
  of the same name (`-m`, `-b`, `-w`, `-t`), and `--no-options` drops the
  URL's options too.
  `--compat PROFILE` (`strict`, `default`, `pxe`, `hpa`, `legacy`) replaces the
  option flags with the profile's settings (client and server alike).
- `get` writes to the remote file's basename by default; `-` is stdout.
  `put` from `-` (stdin) needs a remote name. A `tftp://` URL replaces HOST and
  the remote name, sets the mode with `;mode=netascii` and may carry transfer
  options (`"tftp://h/f?blksize=1024&windowsize=8"`; quote the `?` and `&`
  for the shell).
- `--trace` prints every datagram on stderr; `--pcap FILE` writes them as a
  capture (Wireshark-readable).
- `serve --http URL` is the HTTP gateway, `serve --upstream` the terminating
  proxy; `--write` enables uploads for both. `--allow` adds extension options
  to the standard four; `--refuse` removes any.
- `--interface NIC` (serve, relay) listens on that adapter's IPv4 address
  (`-l ::` for its IPv6 one).
- `serve --listing` allows `x-list`/`x-mtime` (with `--compat` too), which
  `pytftp ls` and `TFTPPath.iterdir()` need. `--port-range` pins transfer
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
  and their final counters when stopped. Ctrl-C, Ctrl-Break and SIGTERM stop
  them, idle or not (the signal wakes the loop through its wake socket; there
  is no polling), with exit status 0 and the counters logged.
- `serve --max-sessions N` defaults to 500; 0 is unlimited (510 at most on
  Windows).

## Dependencies

`netimps` (required, no dependencies of its own; imported lazily, on the
first transfer or server): pktinfo receive and reply sockets (`UDPEndpoint`,
also `arecv` for `AsyncTFTPServer`), broadcast/multicast checks, MTU payload
sizing, the retransmission timer (`Backoff`), socket binding,
host:port parsing, address/network types (`HostLike`, `IPNetworkLike`,
`Host`, `Interface`: what the address-taking parameters accept), bind-error
hints. Importing netimps installs its additive `recvmsg`/`sendmsg`
socket patch on Windows unless `NETIMPS_SOCKET_PATCH=0` is set first (pktinfo
does not depend on it).
`duho` (optional, `cli` extra).
