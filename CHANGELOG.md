# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- **Defaults that bound what a request can hold.** `ServerLimits(max_idle=60.0)`
  is new: a transfer with no datagram from its peer for 60 seconds ends with
  `TransferTimeoutError`, and a `timeout` the client negotiated cannot extend it
  (pass `max_idle=None` for the old behaviour: a request asking `timeout=255`
  was held for over two hours). It counts the peer's silence, not the
  transfer's length. `max_sessions` now defaults to 500 on every platform,
  where it was unlimited outside Windows (`None` still means unlimited, and on
  Windows 510, the most `select()` can watch). `pytftp serve --max-sessions`
  defaults to 500; 0 is unlimited.
- `TFTPError(code, message)` raises `TypeError` for a `code` that is not an
  `int` (so `TFTPError("no such file")` is refused instead of taking the text
  as its code) or a `message` that is not text, and `ValueError` for a code
  outside 0..65535. `encode_error` never raises.
- Requires `netimps>=0.4.0,<0.5`; netimps 0.3 is no longer supported.
- `TFTPClient(timeout=..., max_timeout=...)` raises `ValueError` when
  `max_timeout` is below `timeout`, where it used to be raised to `timeout`
  silently.
- Host text is read by netimps' stricter rules, for `TFTPClient`, `TFTPServer`,
  `TFTPRelay` and `upstream`: a port is ASCII digits only
  (`"host:+70"` is refused) and brackets hold an IPv6 literal only
  (`"[10.0.0.5]"` is refused), each raising `ValueError`
  (`netimps.NetimpsValueError`); a host that is not text, an address, a `Host`
  or an `FQDN` (`None`, say) raises `TypeError` where it raised `ValueError`.
- `format_url` raises `TypeError` for a port that is not an `int` (a `str` or
  a `bool`).
- A socket-buffer shortfall on a windowed transfer is logged by netimps, once
  per process for each distinct request and grant, at `WARNING` on the logger
  `netimps._sockets`; `tftp` no longer logs it at `DEBUG`.
- On Windows an address the system marks tentative or duplicate (the
  169.254.x.x of a media-disconnected adapter) is no longer one of an
  adapter's addresses, so `TFTPServer(interface=...)` does not consider it.
- Every exception the library raises on its own account is defined in
  `tftp.exceptions` and is a `TFTPError`: `TFTPDecodeError` (bytes that are not
  a packet), `CaptureFormatError` and `CaptureFilterError` were plain
  `ValueError` subclasses and are now `TFTPError` as well, and the new
  `TFTPValueError(TFTPError, ValueError)` is what `parse_url` raises for a
  URL it cannot read (it used to raise a bare `ValueError`; `except ValueError`
  still catches it). `WouldBlock` moves there too and stays a
  `BlockingIOError`, outside `TFTPError`, since it is a signal and not a
  failure. `str()` of the decode, filter, capture-format and value errors is
  the message alone.
- `TransferTimeoutError().errno` is `None`; it was `TFTPErrorCode.NOT_DEFINED`
  (0), which is not an operating-system error number.

### Renamed

Old names are not kept as aliases.

| Old | New |
| --- | --- |
| `tftp.errors` (module) | `tftp.exceptions` |
| `TftpError` | `TFTPError` |
| `ProtocolError` | `TFTPProtocolError` |
| `TransferTimeout` | `TransferTimeoutError` |
| `TransferAborted` | `TransferAbortedError` |
| `UnknownTransferId` | `UnknownTransferID` |
| `MalformedPacket` (`tftp.packet`) | `TFTPDecodeError` |
| `FilterError` (`tftp.capture`) | `CaptureFilterError` |
| `Client`, `AsyncClient` | `TFTPClient`, `AsyncTFTPClient` |
| `Server`, `AsyncServer` | `TFTPServer`, `AsyncTFTPServer` |
| `Relay` | `TFTPRelay` |
| `Handler` | `TFTPHandler` |
| `RequestContext` | `TFTPRequestContext` |
| `Opcode`, `ErrorCode` | `TFTPOpcode`, `TFTPErrorCode` |
| `Request`, `Data`, `Ack`, `OptionAck`, `Packet` | `RequestPacket`, `DataPacket`, `AckPacket`, `OptionAckPacket`, `TFTPPacket` |
| `Error` (the ERROR packet, not an exception) | `ErrorPacket` |
| `TftpURL`, `TftpPath`, `TftpUriPath` | `TFTPURL`, `TFTPPath`, `TFTPURIPath` |
| `UdpDatagram` | `UDPDatagram` |
| `FileSystemHandler` (`tftp.server`) | `FilesystemBackend` (`tftp.backends`; the root still exports it) |
| `MemoryHandler`, `HttpHandler`, `UpstreamHandler` | `MemoryBackend`, `HTTPBackend`, `UpstreamBackend` |
| `TftpBackend` (`tftp.path`) | private |
| entry point `tftp.path.uri:TftpUriPath` | `tftp.path:TFTPURIPath` (the scheme is still `tftp`) |

### Fixed

- `TransferTimeoutError`, `TransferAbortedError` and `TFTPProtocolError` can be
  copied and pickled, so a `TransferResult` holding one crosses a
  `multiprocessing` boundary; each raised `TypeError` before.
- `pytftp serve ROOT --remap ...` on a plain directory answered every request
  with ERROR 0 (a `str` was wrapped where a handler belongs); only combined
  with `--per-client` or `--ignore-case` did it work. A remapped name also
  lost the request's listing flag, so `pytftp ls HOST alias` for a remapped
  directory answered "file not found": `TFTPRequestContext.with_filename()` copies
  a context with another filename and `--remap` uses it.
- On Windows an idle `pytftp serve` did not stop on Ctrl-C until a datagram
  arrived, and Ctrl-Break and SIGTERM ended `serve` and `relay` without
  closing the server, the capture or logging the final counters. The command
  now stops on all three, idle or not, with status 0 and the `served:` /
  `relayed:` line; the signal wakes the loop through its wake socket, so an
  idle server still costs no polling.
- `pytftp capture --transfers` (and `FlowTracker(keep_payloads=False)`)
  reported 0 bytes, 0 retransmissions and no missing blocks unless
  `--extract` kept the payloads. They are counted from the sizes and block
  numbers either way.
- A `TFTPPath` bound to an `AsyncTFTPClient` returned wrong results without
  raising (`read_bytes()` gave `b""`, `write_bytes()` reported success, and
  nothing was sent: the client's coroutines were never awaited).
  `AsyncTFTPClient.path()` raises `TypeError`, and `TFTPPath`, `TFTPURIPath`
  (through `with_client()` and `with_backend()`) refuse an asyncio client with
  `TypeError`.
- A server named by a link-local IPv6 address with a zone (`fe80::1%7`) was
  never heard under the default `strict_source=True`: the reply's source was
  compared as text, and a received address carries no zone in its text. Both
  clients and the relay now compare addresses by value (packed address, and
  the scope id when both sides have one) and send to a resolved socket address
  with the scope id as a number, which also makes a request to such a server
  work on the Proactor event loop (the host refused the zone in the text, and
  the error was dropped). The relay's upstream leg is fixed the same way.
- A send the host refuses ends an asyncio client's request with that
  `OSError` at once; it was reported as `TransferTimeoutError` after every retry.
  An ICMP report from the peer is still loss.
- On Windows, one datagram longer than the client's receive buffer, from any
  address, ended a synchronous transfer with an uncaught `OSError` (WinError
  10040). The buffer is now longer than any datagram: a stray one is answered
  with ERROR 5 and the transfer carries on, and a DATA longer than the
  negotiated `blksize` is a `TFTPProtocolError`.
- **With a window above 1, one duplicated, lost or late ACK, or one reordered
  DATA pair, made every remaining window be sent and acknowledged twice** (a
  3001-block transfer at `windowsize=16` sent 5922 DATA datagrams). An ACK
  that moves the sender's window on now sends only the blocks that fit
  (an ACK asking `blksize=65464 windowsize=64` for one block used to draw 64
  DATA, 4 MiB, per 4-octet ACK), and ACKs resend blocks already in flight at
  most once per two windows of progress; timeouts are unchanged. The
  receiver reports a gap twice at most. After one such event the traffic is
  the control's plus one window, at windowsize 1, 2, 4 and 16; windowsize 1
  is unchanged.
- A request nothing follows up no longer costs the whole negotiated window: a
  transfer allocates the buffer for a block when it first reads it, where a
  single 53-octet request asking `blksize=65464 windowsize=64` made the server
  allocate 4 MiB at once. A served file's read buffer is 16 KiB.
- A finished transfer is released when it finishes: its timer entry, which
  stays queued until its deadline, no longer keeps the transfer, its file and
  its window alive (200 downloads asking `timeout=255` kept 799 MiB
  referenced, outside every limit).
- A handler's `TFTPError` that could not be encoded as an ERROR (a NUL in
  the message, a code outside 0..65535) ended the synchronous server's thread
  for every client, and left an `AsyncTFTPServer` session open for good. Every
  ERROR now encodes: a NUL in the text is sent as `?`, the text is cut to 512
  octets, and a code outside 0..65535 is sent as 0. A transfer records its
  failure before it sends the ERROR, and an exception escaping one dispatch
  of the server loop, the asyncio server or the relay ends that one transfer
  (logged once, with its traceback) and not the loop.
- `TFTPServer(max_sessions=...)` and `TFTPRelay(max_sessions=...)` above what
  `select()` can watch on Windows (510 and 255) raise `ValueError` at
  construction, where the serving thread died at the first request past it.
- The shipped API header named `NETIMPS_NO_SOCKET_PATCH`, which netimps 0.4
  rejects at import; the variable is `NETIMPS_SOCKET_PATCH=0`.

## [0.0.0] - 2026-10-03

### Added

- `Client`: download and upload over IPv4 and IPv6, to and from paths, binary
  files or memory, with `blksize`, `windowsize`, `tsize` and `timeout`
  negotiation (and the `utimeout`, `rollover`, `blksize2` and `cookie`
  extensions on request), progress and `on_negotiated` callbacks, exponential
  backoff of retransmissions, an optional time limit, and a one-time retry
  without options when a server refuses them (ERROR 8). `blksize="mtu"` sizes
  blocks to the route's interface MTU; hosts may carry a port
  (`"[::1]:6969"`).
- `Server`: one event-loop thread serving any number of transfers, each on its
  own socket bound to the address the request was sent to (pktinfo); dual-stack
  `::` listening by default; a negotiation policy (`ServerOptions`: maximum
  block and window size, window memory bound, allowed and refused options,
  fitting `blksize` to the arrival interface's MTU); resource limits
  (`ServerLimits`: request size, filename length, option count and length,
  sessions per client, transfer duration); ignoring requests sent to broadcast
  or multicast addresses; blocking handlers opened in worker threads;
  dallying on the last ACK of an upload; `on_complete` for every transfer;
  counters for metrics (`stats`, `stats_snapshot()`).
- `FileSystemHandler`: serves a directory with containment against `..`,
  symlinks and Windows device names; backslash separators; atomic uploads;
  `create`/`overwrite` policy; `max_upload` and free-space checks from `tsize`.
- Handler protocol (`open_read`/`open_write`) for generated content; sources
  and sinks may report "not ready yet" (`WouldBlock`) and wake the transfer
  from another thread, so slow backends apply backpressure instead of
  blocking the server.
- Backends: `MemoryHandler`; `HttpHandler`, a TFTP-to-HTTP(S) gateway (GET and
  PUT, HTTP status codes mapped to TFTP errors, `Content-Length` as `tsize`);
  `UpstreamHandler`, a terminating proxy to another TFTP server whose two sides
  negotiate independently; `Pipe` for writing such handlers.
- `Relay`: a transparent relay forwarding requests byte for byte to upstream
  servers and datagrams unchanged between the two transfer IDs, with routing
  by client subnet, filename prefix or arrival interface, idle and lifetime
  limits, per-transfer summaries and counters.
- `tftp.aio`: `AsyncClient` (including `stream()`, an async generator with
  backpressure) and `AsyncServer` (async handlers and streams, pktinfo on every
  event loop including Windows' Proactor loop).
- Option negotiation through a registry of option handlers, so applications can
  add their own; compatibility profiles `STRICT`, `DEFAULT`, `PXE`, `HPA` and
  `LEGACY`.
- Capture and debugging: `trace=` hooks on the client, server and relay
  reporting every datagram as a `PacketEvent`; `PcapWriter` (Wireshark-readable
  pcap); pcap/pcapng reading from files or live pipes with IP fragment
  reassembly; `FlowTracker`/`analyze()` reconstructing transfers and their
  files from a capture; a filter language; live capture on Linux.
- `tftp://` URLs (RFC 3617): `parse_url`, `format_url`, `download_url`,
  `upload_url`.
- `Client.size()`: a file's size without transferring it (a `tsize` probe the
  server counts as declined, not failed).
- `path` extra: `TftpPath`, a pathlib-next `Path` bound to a client
  (`client.path(...)`), and `TftpUriPath`, the `tftp://` scheme for
  pathlib-next's `UriPath`; whole-file streaming reads and writes, `stat()`
  without a transfer, `copy()`/`move()` across schemes.
- Typed errors: `RemoteError` raised as `FileNotFound`, `AccessViolation`,
  `DiskFull`, `IllegalOperation`, `UnknownTransferId`, `FileAlreadyExists`,
  `NoSuchUser` or `OptionNegotiationError`; `TransferAborted`.
- Transfer engine (`Sender`/`Receiver`) with RFC 7440 windowing, the
  Sorcerer's Apprentice fix, unknown-TID handling, tolerance of repeated
  OACKs and unlimited file size via block-number rollover, usable without
  sockets.
- Streaming netascii in both directions; packet encode/decode for every RFC
  1350 and RFC 2347 packet, keeping a request's raw bytes for forwarding.
- `pytftp` command (`cli` extra): `get` and `put` (also by URL), `serve` (a
  directory, `--http` gateway or `--upstream` proxy), `relay`, `capture`;
  `--compat` profiles, `--trace` and `--pcap` on every command that moves
  packets, `--json` output.
- `port_range=` on `Server`, `AsyncServer` and `Relay` (`PortRange`):
  transfer sockets take their ports from a range a firewall can allow;
  `--port-range` on `pytftp serve` and `relay`.
- `pytftp serve --per-client` (serve `ROOT/<client address>/` when it exists),
  `--ignore-case` and `--remap REGEX=REPLACEMENT`.
- `mstfwindow`, Windows bootmgr's variable-window option, as an extension a
  server may allow (fixed window of 4).
- pytftp's `x-list` and `x-mtime` extensions (`LISTING_OPTIONS`): directory
  listings (`tftp.listing`) and modification times. `Client.stat()`,
  `Client.listdir()` (async too), `pytftp ls`, `pytftp serve --listing`, and
  `iterdir`/`walk`/`glob`/`is_dir`/`st_mtime` on `TftpPath` and `TftpUriPath`.
- Hosts, networks and interfaces accepted as objects (`ipaddress` types,
  `netimps.Host`, `netimps.Interface`) wherever an address is taken, typed
  with netimps' aliases.
- `interface=` on `Server`, `AsyncServer` and `Relay` (and `--interface` on
  `pytftp serve`/`relay`): listen on one network adapter, by name,
  `netimps.Interface`, MAC or address.
- `RequestContext.interface`: the arrival interface as a `netimps.Interface`.
- Loopback throughput benchmark (`benchmarks/run.py`).
- Licensed under MIT.

[Unreleased]: https://github.com/jose-pr/pytftp/compare/v0.0.0...HEAD
[0.0.0]: https://github.com/jose-pr/pytftp/releases/tag/v0.0.0
