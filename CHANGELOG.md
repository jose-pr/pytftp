# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- **Defaults that bound what a request can hold.** `TFTPServerLimits(max_idle=60.0)`
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
- **`TFTPURL` is a validated, immutable value, not a tuple.** The constructor
  normalises (host lower-cased, mode lower-cased) and refuses what no URL can
  carry: a port outside 1..65535, a mode other than `octet` and `netascii`,
  an empty file name or one with a NUL, a host that is empty or holds `/ ? # @`
  or a space (`TFTPValueError`), and an argument of the wrong type
  (`TypeError`). It equals another `TFTPURL` only: it no longer equals, hashes
  like or unpacks as a tuple of its fields. `TFTPURL.parse(text)` and
  `TFTPURL.try_parse(text)` are the way in and `str(url)` the way back; both
  read and write the file name with the packet codec's encoding, so
  `tftp://h/caf%E9` keeps its octet where it used to become U+FFFD, and a
  `%25` IPv6 zone decodes. `str(url)` imports nothing, so formatting a URL no
  longer imports netimps and, on Windows, patches `socket`.
- **`TFTPURL.parse` refuses what RFC 3617's grammar has no place for**, where
  `parse_url` dropped it: a query (`tftp://h/f?x=1` named the file `f`), a
  fragment, userinfo, port 0 (it was read as 69), a repeated `mode`
  parameter (the last one won), an empty parameter (`f;`), a `NUL` in the file
  name (`%00`), a control character, and a bracketed host that is not an IPv6
  address. A literal `?` or `#` in a file name is written `%3F` or `%23`.
  `parse_url(None)` and `parse_url(b"...")` raised `TFTPValueError`; `parse`
  raises `TypeError`.
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
  `TFTPValueError(TFTPError, ValueError)` is what `TFTPURL.parse` raises for a
  URL it cannot read (it used to raise a bare `ValueError`; `except ValueError`
  still catches it). `WouldBlock` moves there too and stays a
  `BlockingIOError`, outside `TFTPError`, since it is a signal and not a
  failure. `str()` of the decode, filter, capture-format and value errors is
  the message alone.
- `TransferTimeoutError().errno` is `None`; it was `TFTPErrorCode.NOT_DEFINED`
  (0), which is not an operating-system error number.

- **The five packet types are validated, immutable values, not named tuples.**
  `RequestPacket`, `DataPacket`, `AckPacket`, `ErrorPacket` and
  `OptionAckPacket` equal only their own type (`AckPacket(5) == (5,)` was
  `True`), are hashable (a request and an OACK were not), do not unpack or
  index, and refuse a block outside 0..65535 or a wrong type at construction.
  `options` is a read-only mapping. Each has a `decode(data)` classmethod,
  `encode()` and `bytes(packet)`: `bytes(AckPacket(5))` is
  `b"\x00\x04\x00\x05"`, where it was one octet, and `bytes(DataPacket(...))`
  raised. `decode()` returns an ERROR's `code` as a `TFTPErrorCode`, which
  carries a number the RFCs do not define as an unnamed member
  (`TFTPErrorCode(258).name == "CODE_258"`) instead of a plain `int`.
- **The encoders are strict**, where they wrote what the decoder refuses. These
  inputs raised nothing and now raise `ValueError`: `encode_request` with an
  empty file name, a mode other than `netascii`, `octet` and `mail`, an empty
  option name or a request over 512 octets (RFC 2347), `encode_oack` with no
  options or an empty option name, and `encode_error` with a code outside
  0..65535 (it sent 0), a NUL in the message (it sent `?`) or a message over 512
  octets (it cut it). An option value that is not text or an `int`
  (`None`, `True`, a float) raises `TypeError`; it was written as `str(value)`.
  `encode_data` and `encode_ack` raise `ValueError` or `TypeError` for a bad
  block, where `struct.error` escaped. The servers and clients still send
  every ERROR they report, however malformed its code or text.

- **`PortRange` is a frozen value; the round-robin position moved to the
  server.** It compares and hashes by `low` and `high` (`PortRange(4000, 4010)
  == PortRange(4000, 4010)` was `False`), cannot be assigned to, prints as
  `LOW:HIGH` with `str()`, and has `parse` and `try_parse` for that text
  (`PortRange.of("4000:4010")` raised). Iteration no longer starts after the
  last port handed out, and `ordered()` and `taken()` are gone: every
  `TFTPServer` and `TFTPRelay` owns its position, so two given one `PortRange`
  no longer take ports from one cursor. `port_range=` takes a
  `PortRangeLike`: a `PortRange`, a `(low, high)` pair, a `range` or the text.
  A bound that is not an `int` (`"5"`) raises `TypeError`, where it was
  converted; an out-of-range one raises `TFTPValueError`, still a `ValueError`.
- `TFTPServerLimits`, `TFTPServerOptions` and `Negotiated` compare equal when
  their fields do, and `TFTPServerLimits` and `OptionRegistry` print their
  contents. `Profile.server` returns a copy each time (like `Profile.client`),
  so `tftp.Profile.PXE.server.max_blksize = 512` no longer changes the preset.

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
| `parse_url(url)` | `TFTPURL.parse(url)` (and `TFTPURL.try_parse(url)`) |
| `format_url(host, filename, port, mode)` | `str(TFTPURL(host, port, filename, mode))` |
| `PortRange.of(value)` | `PortRange.parse(text)`; the functions take a `PortRangeLike` |
| `ServerOptions`, `ServerLimits` | `TFTPServerOptions`, `TFTPServerLimits` |
| `ServerContext`, `ClientContext` | `ServerOptionContext`, `ClientOptionContext` |
| `Blksize`, `Blksize2`, `Timeout`, `Utimeout`, `Tsize`, `Windowsize`, `Rollover`, `Cookie`, `Mstfwindow`, `XList`, `XMtime` (`tftp.options`) | the same with the suffix `Option`: `BlksizeOption` ... `XMtimeOption` |
| `Stats` | `TFTPStats` |
| `Route` (`tftp.relay`, the callable's type) | `RouteFunction` |
| `tftp.DEFAULT`, `tftp.STRICT`, `tftp.PXE`, `tftp.HPA`, `tftp.LEGACY` | `Profile.DEFAULT`, `Profile.STRICT`, `Profile.PXE`, `Profile.HPA`, `Profile.LEGACY` (`PROFILES[name]` is unchanged) |
| `supports_pktinfo`, `dual_stack` (properties of the servers and the relay) | `has_pktinfo`, `is_dual_stack` |
| `TransferResult.ok`, `CapturedTransfer.complete`, `Transfer.done`, `Transfer.stalled` | `is_ok`, `is_complete`, `is_done`, `is_stalled` (the `"ok"` and `"complete"` keys of the command line's and `CapturedTransfer`'s dictionaries are unchanged) |

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
