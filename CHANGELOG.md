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
- **Options are keyword-only.** A callable takes its operands by position and
  every option after them by keyword. `TFTPServerOptions`, `TFTPServerLimits`
  and `Negotiated` take every field by keyword (`TFTPServerOptions(65464, 64)` is
  a `TypeError`). `TFTPServer(root_or_handler, *, host=None, port=69, ...)`,
  `AsyncTFTPServer(root_or_handler, *, host=None, port=69, ...)` and
  `TFTPRelay(route, *, host=None, port=69, ...)` take the handler (or the
  route) alone. `encode_request(opcode, filename, *, mode, options)`,
  `TFTPRequestContext(request, peer, *, local_address, interface_index)`,
  `AtomicWriter(path, *, overwrite)`, `DirectoryListing(directory, *, root)`,
  `OptionRegistry.register(handler, *, replace)`, `AsyncReaderBridge(source, *,
  capacity, size)` and `AsyncWriterBridge(sink, *, capacity, close_sink)` follow
  the same rule. `TFTPClient(host, port=69, ...)`, `PortRange(low, high)` and
  `Upstream(host, port)` keep the conventional pair.
- **The client's whole-transfer limit is `deadline`.** `TFTPClient(max_duration=)`
  and `AsyncTFTPClient(max_duration=)` are `deadline=`: seconds, counted from
  when a transfer starts (`client.deadline` is the attribute). It is not the
  `deadline` attribute of `Sender`, `Receiver` and `RelaySession`, which is the
  instant the engine next wants to be called. The per-transfer cap of a policy
  has one name: `TFTPRelay(max_lifetime=)` is `max_duration=`, as
  `TFTPServerLimits(max_duration=)` already was (the `lifetime` reason of a
  `RelaySummary` is unchanged).
- **Argument names.** `download(..., dest)` and `download_url(url, dest)` are
  `dst`, and `upload(..., source)` and `upload_url(url, source)` are `src`, on
  both clients and the one-shot functions. The operands of the four one-shot
  functions are positional-only, so `src=` in their `client_options` is
  the client's source address, not the data. `TFTPClient(local_address=)` is
  `src=` (`client.src`), and `TFTPRelay(upstream_source=)` is `upstream_src=`.
  The command line's flags are unchanged.
- **`TFTPClient` and `AsyncTFTPClient` check their arguments when they are
  built**, where a bad one failed at the first send or was accepted: a `port`
  outside 1..65535 (it was `OverflowError` at the first send), a negative
  `retries`, a `deadline` that is not positive, a `family` other than `0`,
  `AF_INET` and `AF_INET6`, and a `src` that is not an `(address, port)` pair
  raise `ValueError`; a `str` port, a fractional `retries`, a `bool` for a
  number and a `src` of the wrong shape raise `TypeError`. `backoff` below 1 is
  a `ValueError`, where it was silently stored as 1. The host text is still
  read when the transfer starts.
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
  carry: a port outside 0..65535, a mode other than `octet` and `netascii`,
  an empty file name or one with a NUL, a host that is empty or holds `/ ? # @`
  or a space (`TFTPValueError`), and an argument of the wrong type
  (`TypeError`). It equals another `TFTPURL` only: it no longer equals, hashes
  like or unpacks as a tuple of its fields. `TFTPURL.parse(text)` and
  `TFTPURL.try_parse(text)` are the way in and `str(url)` the way back; both
  read and write the file name with the packet codec's encoding, so
  `tftp://h/caf%E9` keeps its octet where it used to become U+FFFD, and a
  `%25` IPv6 zone decodes. `str(url)` imports nothing, so formatting a URL no
  longer imports netimps and, on Windows, patches `socket`.
- **`TFTPURL.parse` refuses what a URL has no place for**, where `parse_url`
  dropped it: a fragment, userinfo, a repeated `mode` parameter (the last one
  won), an empty parameter (`f;`), a `NUL` in the file name (`%00`), a control
  character, and a bracketed host that is not an IPv6 address. A literal `#` in
  a file name is written `%23`, and a literal `?` or `;` is written `%3F` or
  `%3B`. `parse_url(None)` and `parse_url(b"...")` raised `TFTPValueError`;
  `parse` raises `TypeError`. A query is read as transfer options (below), where
  `tftp://h/f?x=1` used to name the file `f` and drop `x=1`, and port 0 is the
  default port, 69, as `parse_url` read it.
- **A `tftp://` URL carries transfer options**, in either of two spellings:
  whichever of `?` and `;` comes first after the file name decides how the rest
  is read. After `?`, `name=value` pairs separated by `&`
  (`tftp://h/f?blksize=1428&windowsize=16`); after `;`, `name=value` pairs
  separated by `;` (`tftp://h/f;blksize=1428;windowsize=16`, the same URL).
  `mode` is a name in both. `TFTPURL(host, port, filename, mode="octet",
  options=None)` gains `options`, a read-only mapping of lower-case names to
  text that is part of equality and the hash; `str(url)` writes the `;`
  spelling, `mode` first and only when it is not `octet`, then the options in
  name order, so a URL with a mode and no options is exactly RFC 3617's form and
  `TFTPURL.parse(str(url)) == url`. `blksize`, `windowsize`, `timeout`, `tsize`
  and `rollover` are read as the `TFTPClient` keyword of the same name and
  refused with `TFTPValueError` when the URL is built if their value cannot be
  read (ASCII digits only) or the client would refuse its range; any other name
  is a wire option requested verbatim (`extra_options`). Names are compared
  without case; a repeated name, an empty pair, a pair with no `=`, a fragment,
  a NUL or control character and both delimiters unencoded are refused. These
  options are this library's extension: RFC 3617 defines `;mode=` only, and
  another tool reads the text after `?` as part of the file name.
  `TFTPURL("h", 0, "f")` and `tftp://h:0/f` are port 69.
- **The consumers of a URL apply its options.** `download_url`, `upload_url`,
  `pytftp get|put|ls tftp://...` and `TFTPURIPath` turn them into `TFTPClient`
  keywords. An explicit keyword, command flag or `with_options` argument wins
  over the URL's option of the same name, and `extra_options` merge name by
  name; a client given to `TFTPURIPath.with_client` is used as it is. The
  command's `--mode`, `--blksize`, `--windowsize` and `--timeout` default to
  nothing given (the library's defaults, 1428, none and 1.0, are unchanged), so
  the URL's value is used unless the flag is spelled, and `--no-options` drops
  the URL's options too. `TFTPURIPath` reads a `;` in the last path segment as
  parameters whatever they are (a file whose name holds a `;` raises
  `TFTPValueError`; write the `;` as `%3B` in a `?` list instead), where only
  `;mode=` was read and anything else stayed in the file name.
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

- **`Upstream` is a validated, immutable value, not a tuple.** It equals only
  another `Upstream` (`Upstream("h", 69) == ("h", 69)` was `True`), is hashable,
  does not unpack, and checks its port: an `int` in 1..65535, `TypeError` for
  another type (a `(host, "70")` pair was converted), `TFTPValueError` outside
  the range. `Upstream.parse(value)` takes what `relay.upstream(value)` took.
  `ListEntry`, `RemoteStat` and `PacketEvent`, which the library only hands
  out, stay named tuples.
- `RemoteError.from_code` is a classmethod (it was a staticmethod), and
  `TFTPError.from_exception(exc)` replaces `tftp.exceptions.error_for_exception`.
- Option numbers are read in one place and as ASCII digits only, surrounding
  space ignored: a server or client no longer takes `blksize=+1428`,
  `blksize=1_428` or an Arabic-Indic numeral, which the option handlers read
  with `int()`, and the capture and relay observers no longer count one of
  those, nor a digit `int()` cannot read, as a negotiated size. `parse_int`,
  which returned `None` under the name `parse`, is no longer exported.
- `str(event)` is the human line of a `PacketEvent` (`event.format()` is gone),
  and `CapturedTransfer.size` is the transferred byte count (`.bytes` is gone;
  the `"bytes"` key of `to_dict()` is unchanged).
- **`AsyncTFTPClient` is a sibling of `TFTPClient`, not a subclass.** Both derive
  from a private base that holds the arguments, the request options and the
  answer to the first packet, and neither has the other's methods:
  `issubclass(AsyncTFTPClient, TFTPClient)` is false, and `AsyncTFTPClient` no
  longer has a `path()` that raises `TypeError` (a `TFTPPath` is synchronous;
  use `TFTPClient(...).path(...)`). The twin is exported from `tftp` and
  `tftp.client`, beside `TFTPClient`, and no longer from `tftp.aio`, which
  keeps `AsyncTFTPServer` alone. `AsyncReaderBridge`, `AsyncWriterBridge`,
  `is_async_reader` and `is_async_writer` are internal: the clients and the
  server still take an async reader, an async writer or an async iterable.
- **A host name that does not resolve is netimps' `ResolutionError`** (an
  `OSError`; `NoAnswerError` when the lookup found no such name) from both
  clients, where it was `socket.gaierror`.
- **The servers open nothing in their constructor, and share one lifecycle.**
  `TFTPServer`, `AsyncTFTPServer` and `TFTPRelay` check and store their
  arguments; `bind()` opens the sockets (idempotent, never a coroutine) and
  `start()`, `serve_forever()` and entering the context manager call it, so a
  caller can take a privileged port before dropping privileges. `server_address`
  (and `has_pktinfo`, `is_dual_stack`) is `None` before `bind()`, and a bind
  failure (`OSError`, a `ValueError` for an adapter that does not resolve)
  comes from `bind()`, `start()` or `serve_forever()`, not from the
  constructor. `shutdown()` never blocks; `wait_closed(timeout=None)` replaces
  `stop(timeout)` (blocking on the threaded server and the relay, a coroutine on
  `AsyncTFTPServer`) and returns `False` when the timeout passed first; `close()`
  is `shutdown()`, `wait_closed()` and release, final and repeatable, and
  raises `RuntimeError` when called from the thread that is serving (a handler
  calls `shutdown()`). `start()` raises what `bind()` raised and returns only
  once serving.
- **`AsyncTFTPServer.close()` is `aclose()`.** An async class has no `async def
  close()`: `await server.aclose()` releases the listening socket through
  netimps' `aclose()`, and `close()` and `stop()` are gone. `AsyncTFTPServer` is
  exported from `tftp` and `tftp.server`, and the package `tftp.aio` is gone.
  `AsyncTFTPServer.serve_forever()` iterates the listener's `datagrams()`.
- **One hook contract per server.** `TFTPServer` takes plain `open_read` and
  `open_write` and synchronous streams; `AsyncTFTPServer` takes `async def`
  hooks and asynchronous streams (`async read`/`async close`, `async
  write`/`async close`). A handler of the other kind raises `TypeError` when the
  server is built. A synchronous handler (the four built-in backends included)
  goes to `AsyncTFTPServer` through `tftp.server.ThreadedHandler(handler, *,
  executor=None)`, which opens in the executor, or on the loop for a handler
  with `opens_fast`; a directory path needs no adapter and `AsyncTFTPServer(executor=)`
  is gone. Asynchronous streams are no longer accepted as async iterables or as
  a `write` plus `async drain()` pair, and `AsyncTFTPClient.download` and
  `upload` take a path, bytes (to upload) or an object with `async write(data)`
  or `async read(n)`, not a synchronous file object.
- **`tftp` exports what the common task needs; every other name has one home,
  its role module.** No longer importable from `tftp`: `MODES` and `RemoteStat`
  (`tftp.client`), `PortRange` (`tftp.server`), `Negotiated`, `OptionHandler`,
  `OptionRegistry`, `DEFAULT_REGISTRY`, `register_option`, `PROFILES`,
  `DEFAULT_BLKSIZE`, `MIN_BLKSIZE`, `MAX_BLKSIZE`, `MAX_WINDOWSIZE`,
  `STANDARD_OPTIONS`, `EXTENSION_OPTIONS`, `LISTING_OPTIONS` and
  `SUPPORTED_OPTIONS` (`tftp.options`), and `encode_request`, `encode_data`,
  `encode_ack`, `encode_error` and `encode_oack` (`tftp.packet`). The README,
  the shipped header (which now lists every module's exports) and the package
  docstring no longer say that everything is importable from `tftp`, and the
  README's Python blocks are executed by the test suite.
- **Importing the package no longer imports `asyncio`.** `AsyncTFTPClient` and
  `AsyncTFTPServer` are bound on first access; their names and import paths are
  unchanged.
- **The contracts are `typing.Protocol`s** in `tftp.server`: `TFTPHandler`,
  `TFTPReader`, `TFTPChunkReader`, `TFTPWriter`, `AsyncTFTPHandler`,
  `AsyncTFTPReader` and `AsyncTFTPWriter`, with their optional members
  documented as such. The three marker attributes are public: `_tftp_fast_open_`
  is `opens_fast`, `_tftp_copies_` is `copies_writes` and `_tftp_listing_` is
  `lists_directories`.
- **A writable `MemoryBackend` is bounded.** `max_upload` defaults to 16 MiB
  where it was unlimited, and the new `max_entries` (default 1024, the names in
  `files` included) bounds the names: an upload past either is ERROR 3, where
  one client stored 20,000,000 octets and any number of names. A name that
  exists can still be replaced. Pass `None` for the old behaviour.
- **`HTTPBackend` fetches `http` and `https` only.** `HTTPBackend("file:///srv")`
  and any `base_url` that is not an `http` or `https` URL raise `ValueError`; a
  `url_for` URL that is not one is refused with ERROR 2; and the default opener
  carries the HTTP(S) handlers only, where urllib's own also read `file:` and
  `ftp:` and `data:` (a redirect from the origin to an `ftp:` host was fetched).
  Redirects between `http` and `https` are still followed, and `opener=` still
  lets the caller choose any scheme.
- **`HTTPBackend` sends what it declared and names itself.** Requests carry
  `User-Agent: tftp/<version>` where urllib's `Python-urllib/3.x` went, and a
  `PUT` carries `Content-Type: application/octet-stream` where urllib's form
  content type went. A header in `headers` wins over either default.

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
| `relay.upstream(value)` | `Upstream.parse(value)` |
| `tftp.exceptions.error_for_exception(exc)` | `TFTPError.from_exception(exc)` |
| `tftp.listing.parse_listing(data)`, `format_listing(entries)` | `tftp.listing.loads(data)`, `dumps(entries)` |
| `PacketEvent.format()` | `str(event)` |
| `CapturedTransfer.bytes` | `CapturedTransfer.size` |
| `tftp.options.parse_int(text)` | private (`read_decimal`) |
| `PortRange.of(value)` | `PortRange.parse(text)`; the functions take a `PortRangeLike` |
| `ServerOptions`, `ServerLimits` | `TFTPServerOptions`, `TFTPServerLimits` |
| `ServerContext`, `ClientContext` | `ServerOptionContext`, `ClientOptionContext` |
| `Blksize`, `Blksize2`, `Timeout`, `Utimeout`, `Tsize`, `Windowsize`, `Rollover`, `Cookie`, `Mstfwindow`, `XList`, `XMtime` (`tftp.options`) | the same with the suffix `Option`: `BlksizeOption` ... `XMtimeOption` |
| `Stats` | `TFTPStats` |
| `Route` (`tftp.relay`, the callable's type) | `RouteFunction` |
| `tftp.DEFAULT`, `tftp.STRICT`, `tftp.PXE`, `tftp.HPA`, `tftp.LEGACY` | `Profile.DEFAULT`, `Profile.STRICT`, `Profile.PXE`, `Profile.HPA`, `Profile.LEGACY` (`PROFILES[name]` is unchanged) |
| `supports_pktinfo`, `dual_stack` (properties of the servers and the relay) | `has_pktinfo`, `is_dual_stack` |
| `TFTPServer.stop(timeout)`, `TFTPRelay.stop(timeout)`, `await AsyncTFTPServer.stop()` | `shutdown()`, then `wait_closed(timeout)` |
| `await AsyncTFTPServer.close()` | `await AsyncTFTPServer.aclose()` |
| `tftp.aio.AsyncTFTPServer` | `tftp.AsyncTFTPServer`, `tftp.server.AsyncTFTPServer` |
| `TransferResult.ok`, `CapturedTransfer.complete`, `Transfer.done`, `Transfer.stalled` | `is_ok`, `is_complete`, `is_done`, `is_stalled` (the `"ok"` and `"complete"` keys of the command line's and `CapturedTransfer`'s dictionaries are unchanged) |

### Fixed

- **Transfers in flight when a server stops are reported.** `shutdown()` sent them ERROR 0
  but called `on_complete` for none of them and left `completed` and `failed` where they
  were, on both servers; each now yields a `TransferResult` whose `error` is
  `TransferAbortedError`, and counts as failed.
- **On Windows one file is reachable under one name.** `FilesystemBackend` refuses a name
  component ending in a dot or a space (the file system drops it, so `f.` opened `f`), one
  with a character Windows does not allow in a name, and every name
  `os.path.isreserved` reserves: `CONIN$`, `CONOUT$`, the superscript `COM` and `LPT`
  names, and a device name followed by spaces. Before Python 3.13 the same list is used.
- **A number in an option is ASCII digits, and nothing else.** A server
  acknowledged a `blksize` padded with white space as 1024, and echoed
  a `timeout` as it was written; the acknowledgement is now written from the
  number that was read (`timeout=005` is answered `5`). A number of more than 19
  significant digits is not a number. `timeout` and `utimeout` requested
  together are acknowledged both only when they name the same time, as
  tftp-hpa does (a server ran on `utimeout` while also acknowledging a
  different `timeout`), and `mstfwindow` obeys `max_window_bytes` as
  `windowsize` does (a 65464-octet limit gave a window of 261856 octets).
- **A client refuses (ERROR 8) an acknowledged `timeout` or `utimeout` that is
  not the one it asked for** (RFC 2349), where it applied any value from one
  microsecond to 10^24 seconds. The client's `tsize` and `x-mtime` readers are
  the same reader as the server's.
- **The option fallback also answers ERROR 4 and ERROR 0.** A client with
  `fallback=True` repeats a request that carried options once without them
  after ERROR 8, 4 or 0, the errors a server older than the option extension
  sends for the extra data (RFC 2347: the client "may" repeat it); `stat()`
  probes again for the size alone after the same errors. It repeated only
  after ERROR 8.
- `OptionRegistry.register` raises `ValueError` for a name that is not
  lower-case: such a handler was registered and never negotiated.
- **A transfer through a raw stream no longer reports success with octets
  missing.** `as_write` writes until a block is taken where a raw sink took
  100 octets per call and a 10247-octet transfer ended with 2007 in the sink and
  no error (a sink that takes nothing fails the transfer, and a raw sink with
  nothing ready holds the block), and `as_readinto` takes `None` from
  `readinto` or `read` as "nothing ready", where a non-blocking source ended
  the transfer after 512 octets of 10247.
- **A netascii read request asking `tsize` no longer makes the server read
  the whole file before it answers.** The size of a netascii transfer needs
  the file scanned, on the loop that serves every other transfer: after eight
  such 27-octet requests for a 768 MiB file, a small download took 4.3 s
  where an idle server takes 0.01 s. The OACK leaves `tsize` out for
  netascii, as tftp-hpa does; an octet transfer still answers it.
- **A directory listing from a server can no longer name a path.**
  `listing.loads` (and so `listdir()`, `TFTPPath.iterdir()` and a recursive
  `copy()`) skips a line whose name is empty, `.`, `..` or holds `/`, a
  backslash or a NUL, and one whose size or time is not ASCII digits: it
  returned `../up.txt`, `/abs.txt`, `a/b`, `C:\x`, negative sizes and times
  and read `1_0` as 10, which a caller joining `entry.name` to a local path
  would have followed out of its directory.
- An ERROR's text is read one way: up to its first NUL, with an octet that is
  not UTF-8 shown as U+FFFD, whether the ERROR answers a request (`decode`)
  or arrives mid-transfer. The first read left a lone surrogate in the text.
- **A windowed receiver follows a sender that wraps to block 1** (rollover
  not negotiated); at `windowsize=4` both ends timed out at block 65536. The
  engine runs a window of at most 32767 blocks whatever was negotiated: with a
  window above half the block numbers, a late ACK was taken for one that
  acknowledged the whole window, and the sender counted 65535 blocks as
  delivered. A datagram of fewer than 4 octets is dropped by `Sender` and
  `Receiver` alike; a 3-octet ACK ended a sending transfer with "unexpected
  opcode 4".

- `start()` after `close()` raises `RuntimeError` on `TFTPServer`, `AsyncTFTPServer`
  and `TFTPRelay`. The threaded server waited five seconds and returned as if it
  were serving (the thread died on the same error), and the asynchronous one
  returned at once after serving before, or never returned when it had not. A
  second `start()` raises `RuntimeError`, where the asynchronous one started a
  second serving task and made `close()` wait for ever.
- `await server.aclose()` from another task ends a running
  `AsyncTFTPServer.serve_forever()` without an exception: it raised
  `RuntimeError: endpoint is closed` from netimps.
- A server or relay whose startup fails part-way (a selector registration, a
  socket pair, a worker pool) closes what it had opened: the constructor used to
  leave the listening socket, the selector, the socket pair and the pool behind.
  A listener that cannot finish setting up closes its socket.
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
- **The client's first answer is checked.** A write is answered by a whole ACK 0
  and a read by a whole DATA 1 (or an OACK); a DATA of another block, an ACK
  cut short or a packet that does not decode is `TFTPProtocolError` after an
  ERROR 4, where the blocking client waited out its retries and
  `AsyncTFTPClient` waited for ever (or raised `IndexError` for a cut ACK)
  and an upload began from a cut ACK.
- **`download()` to a path no longer loses the file that was there.** It writes
  a hidden temporary file beside the path and replaces the path when the
  transfer succeeds; the file was opened (and so truncated) before the host
  was resolved, and removed on any failure, an unparsable host or a server's
  "not found" included. A path that exists and is not a regular file (a device,
  a pipe) is written in place and never removed. A symlink at the path is
  replaced, the directory must be writable, and on Windows a file another
  handle has open cannot be replaced (`PermissionError`, the file intact).
  `AtomicWriter(path, *, overwrite=True, mode=None)` takes the file's
  permissions with `mode`.
- **A local failure during a transfer is raised as itself**, where the client
  raised a bare `TFTPError` whose `__cause__` held it: a full disk is
  `OSError(ENOSPC)`, as the header says, and an exception from an asynchronous
  stream is that exception. The server is still sent the ERROR for it.
- **`max_size` bounds what a server can make a client hold.** `TFTPClient(
  max_size=)`, and `max_size=` on `download`, `get` and `listdir`, refuse a
  download past it with ERROR 3 and the new `TransferTooLargeError`, before
  the block that passes it is written, and a server announcing more in its
  `tsize` is refused before any data moves. A server sending more than the
  `tsize` it announced is `TFTPProtocolError` where it was accepted.
  `listdir()` is bounded at 16 MiB by default; the bound on a download is off
  unless set.
- The `tsize` announced for an upload from a wrapped file object (a
  `gzip.GzipFile`, say) is the size of what is read, where `fstat` of the file
  underneath announced the compressed size (139 octets for 100000).
- **A trace hook that raises is logged once.** `TFTPClient`, `TFTPServer` and
  `TFTPRelay` logged one traceback per datagram (nine for a 3,000-octet
  transfer, a pcap writer on a full disk wrote one per packet of every
  transfer) and `AsyncTFTPClient` discarded the exception silently; all of them
  now log the first failure of a hook with its traceback and count the rest.
  The transfer is unaffected, as before.
- **Trace events and pcaps from the clients and the relay name a real address
  as the local end.** `PacketEvent.local` was the wildcard (`0.0.0.0`, `::`) of
  an unconnected socket, so `pytftp get --trace --pcap` showed
  `0.0.0.0:55758 > 127.0.0.1:55025` and wrote 0.0.0.0 into the pcap; it is now
  the address the route to the peer uses, once per transfer for a client and
  per peer for the relay.
- **A transfer ends everything it started.** `AsyncTFTPClient` left the task
  that feeds an asynchronous stream pending for ever after a download or an
  upload failed, was refused, timed out or was cancelled (asyncio logged
  "Task was destroyed but it is pending" at exit), read up to 1 MiB from the
  source of an upload before the server had answered, and closed its socket
  twice (CPython 3.9.0 to 3.9.10 and 3.10.0 to 3.10.2 log a traceback for that
  on the Proactor loop). Every task and the socket are now ended and closed
  before the call returns, and the source is read after the server accepts the
  request. `stream(buffer=N)` holds `N` octets, where it held `N` plus a second
  1 MiB; closing it early ends the transfer. The blocking client sends ERROR 0
  when an exception from `progress` (or a signal) interrupts a transfer, where
  the server waited out its retries, and it reports progress for a file that
  arrives as DATA 1 with no OACK.
- **`deadline` bounds every call.** `size()`, `stat()` and the request phase of a
  transfer end by it (a `size()` with `deadline=0.5` took 6 seconds, a `get()`
  with a longer `timeout` waited the whole `timeout`), and the limit has one
  start across the retry without options in both clients, where
  `AsyncTFTPClient` gave the second attempt a limit of its own.
- **A `with` block that raises abandons the upload through a path.** Leaving a
  block over `path.open("wb")` by an exception used to complete the upload and
  commit what had been written: a truncated file, over a complete one if the
  name existed (a failed `copy()` left the same). The client now sends ERROR in
  place of the next block, the server discards the upload and keeps what it
  had, and the transfer thread is joined. A block that ends normally commits as
  before.
- **`copy(overwrite=True)` onto a name the server has works.** `unlink()` on a
  `TFTPPath` or `TFTPURIPath` still raises `NotImplementedError`, except for
  `unlink(missing_ok=True)` and the call `pathlib_next`'s `copy()` makes on its
  target: they do nothing, and the write that follows replaces the file.
  `move()` from a TFTP path is not supported: it copies and then raises
  `NotImplementedError` because the source cannot be deleted.
- `TFTPPath.as_uri()` keeps a leading slash (`/boot/x` is `tftp://h//boot/x`, as
  `str(TFTPURL)` writes it), so `/boot/x` and `boot/x` stay distinct. A client's
  own `on_negotiated` hook fires for reads and writes through a path (the path
  replaced it on its copy of the client), and `TFTPPath(mode=)` and
  `with_mode()` raise `ValueError` for a mode other than `octet` and
  `netascii` (`TypeError` for a value that is not text), where the path failed
  when first used. `tftp.path.TFTPURIPath` without `uritools` raises
  `ImportError` naming the `path` extra.
- **The HTTP gateway's `PUT` is exact.** A WRQ's announced `tsize` was sent as
  `Content-Length` and then every octet uploaded: a client that announced 5 and
  sent 69 was told the upload succeeded while the origin stored 5 octets, and an
  origin that ignores `Connection: close` ran the surplus as a second request.
  The last octets of the body are now sent only when the transfer ends, more
  octets than announced fail it with ERROR 3 and fewer with ERROR 0, and the
  origin stores nothing and sees one request. A netascii upload, whose announced
  size is not the size received, is chunked.
- **The HTTP gateway no longer fetches ahead of a peer that has not answered.**
  One unacknowledged request made the gateway pull the whole 1 MiB buffer from
  the origin (1.9 MB had left the origin after 2 seconds) and hold the
  connection until the session ended. A download is read ahead by about 16 KiB
  until the transfer has taken data, and by up to `buffer` as it takes more.
- A file name that is not UTF-8 reaches the origin percent-encoded as the
  octets that arrived, where it raised `UnicodeEncodeError` and logged a
  traceback per request. The response of a failed request is closed (a 404
  left a socket and a temporary file to be collected, with a `ResourceWarning`),
  and a `Content-Length` that is not ASCII digits is no size.

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
