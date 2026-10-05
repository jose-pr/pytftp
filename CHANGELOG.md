# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- **Defaults that bound what a request can hold.** `ServerLimits(max_idle=60.0)`
  is new: a transfer with no datagram from its peer for 60 seconds ends with
  `TransferTimeout`, and a `timeout` the client negotiated cannot extend it
  (pass `max_idle=None` for the old behaviour: a request asking `timeout=255`
  was held for over two hours). It counts the peer's silence, not the
  transfer's length. `max_sessions` now defaults to 500 on every platform,
  where it was unlimited outside Windows (`None` still means unlimited, and on
  Windows 510, the most `select()` can watch). `pytftp serve --max-sessions`
  defaults to 500; 0 is unlimited.
- `TftpError(code, message)` raises `TypeError` for a `code` that is not an
  `int` (so `TftpError("no such file")` is refused instead of taking the text
  as its code) or a `message` that is not text, and `ValueError` for a code
  outside 0..65535. `encode_error` never raises.
- Requires `netimps>=0.4.0,<0.5`; netimps 0.3 is no longer supported.
- `Client(timeout=..., max_timeout=...)` raises `ValueError` when
  `max_timeout` is below `timeout`, where it used to be raised to `timeout`
  silently.
- Host text is read by netimps' stricter rules, for `Client`, `Server`,
  `Relay` and `upstream`: a port is ASCII digits only
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
  adapter's addresses, so `Server(interface=...)` does not consider it.

### Fixed

- A request nothing follows up no longer costs the whole negotiated window: a
  transfer allocates the buffer for a block when it first reads it, where a
  single 53-octet request asking `blksize=65464 windowsize=64` made the server
  allocate 4 MiB at once. A served file's read buffer is 16 KiB.
- A finished transfer is released when it finishes: its timer entry, which
  stays queued until its deadline, no longer keeps the transfer, its file and
  its window alive (200 downloads asking `timeout=255` kept 799 MiB
  referenced, outside every limit).
- A handler's `TftpError` that could not be encoded as an ERROR (a NUL in
  the message, a code outside 0..65535) ended the synchronous server's thread
  for every client, and left an `AsyncServer` session open for good. Every
  ERROR now encodes: a NUL in the text is sent as `?`, the text is cut to 512
  octets, and a code outside 0..65535 is sent as 0. A transfer records its
  failure before it sends the ERROR, and an exception escaping one dispatch
  of the server loop, the asyncio server or the relay ends that one transfer
  (logged once, with its traceback) and not the loop.
- `Server(max_sessions=...)` and `Relay(max_sessions=...)` above what
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
