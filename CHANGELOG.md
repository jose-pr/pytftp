# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/jose-pr/pytftp/commits/main
