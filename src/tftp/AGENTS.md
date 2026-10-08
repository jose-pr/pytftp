# `tftp` — public API header

Header-file-style reference for the `tftp` package: every public export with
its signature, arguments, contract and gotchas, so the package can be used
without reading its source. It ships inside the package and is self-contained;
read it (or the README beside it) with `importlib.resources.files("tftp")`.
Development documentation lives with the source at
<https://github.com/jose-pr/pytftp>.

`tftp` exports what the common task needs, listed under "Root exports". Every other
name has one home, its role module or one of the topic modules `tftp.transfer`,
`tftp.listing` and `tftp.netascii`, and is imported from it
(`from tftp.options import OptionRegistry`): "Where names live" lists every public
module's exports. Modules starting with `_` are internal.

Install with `pip install tftp`; its required dependencies are `netimps` and
`pktcap`. The `cli` extra (`pip install "tftp[cli]"`) adds `duho`, which the
`pytftp` command needs; the `path` extra (`pip install "tftp[path]"`) adds
`pathlib-next[uri]`, which `tftp.path` needs. Importing `tftp` never requires
either. Python 3.9 or newer.

`netimps` (no dependencies of its own) supplies the pktinfo receive and reply sockets
(`UDPEndpoint`, and `arecv` for `AsyncTFTPServer`), broadcast and multicast checks, MTU payload
sizing, the retransmission timer (`Backoff`), socket binding, host:port parsing, the address and
network types (`HostLike`, `IPNetworkLike`, `Host`, `Interface`: what the address-taking parameters
accept) and bind-error hints; `pktcap` reads, writes and dissects captures.

`tftp.__version__` — the installed distribution's version.

Options are keyword-only: a callable takes its operands (the file name, the
host, the handler) by position and every option after them by keyword, and a
policy or record class (`TFTPServerOptions`, `TFTPServerLimits`, `Negotiated`)
takes every field by keyword.

A large topic keeps its public names here and its detail in the header beside the code
that implements it; those headers ship in the package, at the paths below
(`importlib.resources.files("tftp")`).

| Header | Covers |
| --- | --- |
| `tftp/AGENTS.md` | this file: the root exports, the topic modules `tftp.listing` (the `x-list` format) and `tftp.netascii`, the exceptions, the protocol coverage, the environment variables, the gotchas |
| `tftp/client/AGENTS.md` | `TFTPClient`, `AsyncTFTPClient`, `download`, `upload`, and the `tftp://` URL type with `download_url` and `upload_url` |
| `tftp/server/AGENTS.md` | `TFTPServer`, `AsyncTFTPServer`, the handler contract, `TFTPRequestContext`, `AtomicWriter`, `PortRange`, `TFTPServerLimits`, the counters |
| `tftp/options/AGENTS.md` | `TFTPServerOptions`, the option handlers and registry, the built-in options, `Profile`, `Negotiated` |
| `tftp/backends/AGENTS.md` | `FilesystemBackend`, `MemoryBackend`, `HTTPBackend`, `UpstreamBackend`, `Pipe`, and the `Remap`, `PerClient` and `CaseInsensitive` wrappers |
| `tftp/relay/AGENTS.md` | `TFTPRelay`, `AsyncTFTPRelay` and their routing helpers |
| `tftp/capture/AGENTS.md` | packet events and trace hooks, `FlowTracker`, `analyze`, `replay_transfers`, TFTP as a pktcap layer, the event filters |
| `tftp/packet/AGENTS.md` | the packet types, the codec and the enums |
| `tftp/transfer/AGENTS.md` | the I/O-free engine: `Sender`, `Receiver`, `Requester`, `Transfer`, `as_readinto` and `as_write` |
| `tftp/path/AGENTS.md` | `TFTPPath` and `TFTPURIPath` (the `path` extra) |
| `tftp/cli/AGENTS.md` | the `pytftp` command: its subcommands, exit statuses, output and environment variables |

## Where names live

| Module | Exports |
| --- | --- |
| `tftp` | `AccessViolation`, `AckPacket`, `AsyncTFTPClient`, `AsyncTFTPServer`, `AtomicWriter`, `DataPacket`, `DiskFull`, `ErrorPacket`, `FileAlreadyExists`, `FileNotFound`, `FilesystemBackend`, `IllegalOperation`, `ListEntry`, `NetasciiReader`, `NetasciiWriter`, `NoSuchUser`, `OptionAckPacket`, `OptionNegotiationError`, `Profile`, `Receiver`, `RemoteError`, `RequestPacket`, `Sender`, `TFTPClient`, `TFTPDecodeError`, `TFTPError`, `TFTPErrorCode`, `TFTPHandler`, `TFTPOpcode`, `TFTPProtocolError`, `TFTPRequestContext`, `TFTPServer`, `TFTPServerLimits`, `TFTPServerOptions`, `TFTPURL`, `TFTPValueError`, `TransferAbortedError`, `TransferResult`, `TransferTimeoutError`, `TransferTooLargeError`, `UnknownTransferID`, `WouldBlock`, `decode`, `download`, `download_url`, `upload`, `upload_url` |
| `tftp.client` | `AsyncSink`, `AsyncSource`, `AsyncTFTPClient`, `MODES`, `ProgressFunction`, `RemoteStat`, `SinkLike`, `SourceLike`, `TFTPClient`, `download`, `upload` |
| `tftp.server` | `AsyncTFTPHandler`, `AsyncTFTPReader`, `AsyncTFTPServer`, `AsyncTFTPWriter`, `AtomicWriter`, `PortRange`, `PortRangeLike`, `TFTPChunkReader`, `TFTPHandler`, `TFTPReader`, `TFTPRequestContext`, `TFTPServer`, `TFTPServerLimits`, `TFTPStats`, `TFTPWriter`, `ThreadedHandler` |
| `tftp.relay` | `AsyncRouteFunction`, `AsyncTFTPRelay`, `RelaySummary`, `RouteFunction`, `RouteTable`, `TFTPRelay`, `Upstream`, `UpstreamLike`, `by_interface`, `by_prefix`, `by_subnet` |
| `tftp.capture` | `Analysis`, `CapturedTransfer`, `DatagramLike`, `DatagramWriter`, `Endpoint`, `FlowTracker`, `PacketEvent`, `ReplayedTransfers`, `TFTPLayer`, `analyze`, `combine_hooks`, `dissect_tftp`, `follow_transfers`, `new_session_id`, `pktcap_plugin`, `register_tftp_dissector`, `replay_transfers`, `summarize`, `trace_to` |
| `tftp.options` | `BUILTIN_OPTIONS`, `Blksize2Option`, `BlksizeOption`, `ClientOptionContext`, `CookieOption`, `DEFAULT_BLKSIZE`, `DEFAULT_REGISTRY`, `EXTENSION_OPTIONS`, `LISTING_OPTIONS`, `MAX_BLKSIZE`, `MAX_UTIMEOUT`, `MAX_WINDOWSIZE`, `MIN_BLKSIZE`, `MIN_UTIMEOUT`, `MstfwindowOption`, `Negotiated`, `OptionHandler`, `OptionRegistry`, `PROFILES`, `Profile`, `RolloverOption`, `STANDARD_OPTIONS`, `SUPPORTED_OPTIONS`, `ServerOptionContext`, `TFTPServerOptions`, `TimeoutOption`, `TsizeOption`, `UtimeoutOption`, `WindowsizeOption`, `XListOption`, `XMtimeOption`, `accept_oack`, `negotiate`, `refuse`, `register_option`, `request_options` |
| `tftp.packet` | `AckPacket`, `DataPacket`, `ErrorPacket`, `FILENAME_ENCODING`, `OptionAckPacket`, `RequestPacket`, `TFTPErrorCode`, `TFTPOpcode`, `TFTPPacket`, `decode`, `encode_ack`, `encode_data`, `encode_error`, `encode_oack`, `encode_request` |
| `tftp.backends` | `CaseInsensitive`, `FilesystemBackend`, `HTTPBackend`, `MemoryBackend`, `PerClient`, `Pipe`, `Remap`, `UpstreamBackend`, `normalize_name` |
| `tftp.path` | `TFTPPath`, `TFTPURIPath` |
| `tftp.exceptions` | `AccessViolation`, `DiskFull`, `FileAlreadyExists`, `FileNotFound`, `IllegalOperation`, `NoSuchUser`, `OptionNegotiationError`, `RemoteError`, `TFTPDecodeError`, `TFTPError`, `TFTPProtocolError`, `TFTPValueError`, `TransferAbortedError`, `TransferTimeoutError`, `TransferTooLargeError`, `UnknownTransferID`, `WouldBlock` |
| `tftp.cli` | `main` |
| `tftp.transfer` | `Receiver`, `Requester`, `SendFunction`, `Sender`, `SupportsRead`, `SupportsReadinto`, `SupportsWrite`, `Transfer`, `as_readinto`, `as_write` |
| `tftp.listing` | `DirectoryListing`, `LIST_OPTION`, `ListEntry`, `MTIME_OPTION`, `dumps`, `loads` |
| `tftp.netascii` | `NetasciiReader`, `NetasciiWriter`, `decode`, `encode`, `encoded_size` |

## Static typing

The package ships `py.typed` and is checked with mypy on Linux, macOS and Windows.
Each header ends its module's section with the public aliases it exports.

## Protocol coverage

| Spec | What | Notes |
| --- | --- | --- |
| RFC 1350 | RRQ, WRQ, DATA, ACK, ERROR; octet and netascii | `mail` mode is refused with ERROR 4 |
| RFC 1123 4.2.3.1 | Sorcerer's Apprentice fix | duplicate ACKs never trigger a resend (windowsize 1); with a window, an ACK that moves it on sends only the new blocks, and an ACK resends blocks in flight at most once per two windows of progress, so one duplicated, lost or late ACK costs one window of DATA, not every window after it |
| RFC 1123 4.2.3.2 | exponential backoff | each consecutive retransmission waits `backoff` times longer, capped |
| RFC 1123 4.2.3.5 | broadcast requests ignored | server, when pktinfo reports the destination |
| RFC 2347 | option extension, OACK, ERROR 8 | unknown options are ignored, as the RFC requires |
| RFC 2348 | `blksize` 8..65464 | server clamps to its `max_blksize` |
| RFC 2349 | `timeout` (1..255 s), `tsize` | `tsize` 0 is never sent in an OACK (curl rejects it), and a netascii read request's `tsize` is left out (its size needs the whole file read; tftp-hpa does the same) |
| RFC 7440 | `windowsize` 1..65535 | server clamps to its `max_windowsize` (default 64); the engine runs a window of at most 32767 whatever was negotiated, so an old ACK is never mistaken for a new one |
| tftp-hpa | `blksize2`, `utimeout`, `rollover`, `cookie` | **off unless a server allows them** (`TFTPServerOptions(allowed=...)`, a profile) |
| Microsoft | `mstfwindow` (bootmgr/WDS variable window) | off unless allowed; runs a fixed window of 4 (the in-transfer resize is unpublished) |
| pytftp | `x-list` (directory listing), `x-mtime` (modification time) | off unless allowed (`LISTING_OPTIONS`); other servers ignore them |
| — | block-number rollover | blocks wrap after 65535 (to 0, or 1 with `rollover`); file size is unlimited |
| RFC 1350 §6 | dallying | server re-ACKs a repeated last DATA for one timeout |

Not implemented: RFC 2090 multicast, PXE MTFTP.

## Root exports (`tftp`)

What `tftp` itself exports, each with the header that holds its detail.

```python
TFTPClient(
    host, port=69, *, timeout=1.0, retries=5, blksize=1428, windowsize=None, tsize=True,
    rollover=None, timeout_option=True, family=0, src=None, fallback=True, dally=False,
    backoff=2.0, max_timeout=None, deadline=None, max_size=None, strict_source=True,
    utimeout=False, extra_options=None, registry=None, on_negotiated=None, trace=None
)
AsyncTFTPClient(
    host, port=69, *, timeout=1.0, retries=5, blksize=1428, windowsize=None, tsize=True,
    rollover=None, timeout_option=True, family=0, src=None, fallback=True, dally=False,
    backoff=2.0, max_timeout=None, deadline=None, max_size=None, strict_source=True,
    utimeout=False, extra_options=None, registry=None, on_negotiated=None, trace=None
)
TFTPServer(
    root_or_handler, *, host=None, port=69, writable=False, create=True, overwrite=False,
    timeout=1.0, retries=5, options=None, max_sessions=500, reply_from_request_address=True,
    dally=True, on_complete=None, limits=None, ignore_broadcast=True, backoff=2.0,
    max_timeout=None, trace=None, open_in_thread=None, workers=8, port_range=None, interface=None
)
AsyncTFTPServer(
    root_or_handler, *, host=None, port=69, writable=False, create=True, overwrite=False,
    timeout=1.0, retries=5, options=None, max_sessions=500, reply_from_request_address=True,
    dally=True, on_complete=None, limits=None, ignore_broadcast=True, backoff=2.0,
    max_timeout=None, trace=None, port_range=None, interface=None
)
download(host, filename, dst, /, *, port=69, mode="octet", progress=None, **client_options)
upload(host, filename, src, /, *, port=69, mode="octet", progress=None, **client_options)
download_url(url, dst, /, *, progress=None, **client_options)
upload_url(url, src, /, *, progress=None, **client_options)
TFTPURL(host, port, filename, mode="octet", options=None)
```

- `TFTPClient` and `AsyncTFTPClient` — the blocking client and its asyncio sibling, with
  `download`, `get`, `upload`, `put`, `size`, `stat`, `listdir` and a path; `download` and
  `upload` are the one-shot wrappers, `download_url` and `upload_url` the same by `tftp://`
  URL, and `TFTPURL` is the validated URL (`tftp/client/AGENTS.md`).
- `TFTPServer` and `AsyncTFTPServer` — serve a directory or a handler on one thread or one
  event loop, each transfer on its own socket (`tftp/server/AGENTS.md`).

```python
TFTPServerOptions(
    *, max_blksize=65464, max_windowsize=64, max_window_bytes=4194304, allowed=None, refused=(),
    fit_mtu=False, registry=None
)
TFTPServerLimits(
    *, max_request_size=1024, max_filename_length=512, max_options=16, max_option_length=255,
    max_sessions_per_client=None, max_duration=None, max_idle=60.0
)
FilesystemBackend(
    root, *, writable=False, create=True, overwrite=False, backslash=True, max_upload=None
)
AtomicWriter(path, *, overwrite=True, mode=None)
TFTPRequestContext(request, peer, *, local_address=None, interface_index=0)
Profile(name, server, client)
```

- `TFTPServerOptions` and `Profile` — the negotiation policy and the presets for quirky peers
  (`tftp/options/AGENTS.md`); `TFTPServerLimits` bounds untrusted input.
- `TFTPHandler` — the protocol a handler implements, given a `TFTPRequestContext`;
  `FilesystemBackend` is the directory handler and `AtomicWriter` the writer that never
  leaves a partial upload (`tftp/server/AGENTS.md`, `tftp/backends/AGENTS.md`).
- `RequestPacket`, `DataPacket`, `AckPacket`, `ErrorPacket`, `OptionAckPacket`, `TFTPOpcode`,
  `TFTPErrorCode` and `decode` — the wire format (`tftp/packet/AGENTS.md`).
- `Sender` and `Receiver` — the I/O-free engine (`tftp/transfer/AGENTS.md`); `NetasciiReader`,
  `NetasciiWriter` and `ListEntry` — the netascii and listing building blocks, below. Every
  exception is exported here too.

```python
TransferResult(
    filename, operation, mode, peer, local, bytes, blocks, retransmits, duration, negotiated,
    error=None
)
```

**`TransferResult`** — `filename`, `operation` (`"read"` for RRQ, `"write"`
for WRQ, on both sides), `mode`, `peer`, `local`, `bytes` (payload bytes on
the wire; netascii-encoded size in netascii mode), `blocks`, `retransmits`,
`duration` (seconds), `negotiated`, `error` (or `None`); properties `is_ok` and
`throughput` (bytes/s). `to_dict()` is the JSON-ready form (the command's `--json`
object): `ok`, `operation`, `filename`, `mode`, `peer` (`[host, port]`), `bytes`,
`blocks`, `retransmits`, `duration` (rounded to the microsecond), `blksize`,
`windowsize`, `tsize`, `options` (a copy of the OACK) and `error` (text or
`None`).

## Directory listing (`tftp.listing`)

```python
ListEntry(name, is_dir, size, mtime=None)
dumps(entries)
loads(data)
DirectoryListing(directory, *, root=None)
```

The `x-list` format: UTF-8 lines `<f|d> <size> <mtime|-> <name>` (`%`, CR, LF in names as
`%25`, `%0D`, `%0A`). `ListEntry` is a named tuple (it is only handed out); `dumps(entries)`
returns `bytes` and `loads(data)` a list, skipping malformed lines: a name that is empty, `.`,
`..` or holds `/`, a backslash or a NUL, and a size or time that is not ASCII digits, so no
entry a server lists names anything outside its directory. `DirectoryListing` is a `BytesIO`
with `size`, `mtime` and `lists_directories`, sorted by name, which leaves out symlinks
resolving outside `root` and in-progress uploads `.name.*.part`; `LIST_OPTION` and
`MTIME_OPTION` are the option names.

## Netascii (`tftp.netascii`)

```python
NetasciiReader(raw)
NetasciiWriter(raw)
encode(data)
decode(data)
encoded_size(fileobj)
```

Local text uses LF; on the wire a line ends CR LF and a bare CR is CR NUL. The translation is
lossless both ways (a local `\r\n` travels as `\r\0\r\n`). `NetasciiReader(raw)` (`readinto`,
`close`) encodes a binary reader; `NetasciiWriter(raw)` (`write`, `flush`, `close`, `abort`)
decodes into a binary writer, holding a trailing CR until it sees what follows, and writes
through `as_write`: a raw stream's short write is completed, one that takes nothing raises
`OSError`, and one with nothing ready raises `WouldBlock` with the block held. Both handle a CR
on any block boundary. `encode`, `decode` and `encoded_size(fileobj)` (seekable; position
preserved) work on whole values.

## Exceptions (`tftp.exceptions`)

```python
TFTPError(code=0, message="")
TFTPError.from_exception(exc)
RemoteError(code=None, message="")
RemoteError.from_code(code, message="")
TFTPProtocolError(message, code=4)
TransferTimeoutError(message="timed out")
TransferAbortedError(message="transfer aborted")
TransferTooLargeError(message="transfer too large")
TFTPDecodeError(message="")
TFTPValueError(message="")
```

Exceptions, all defined in `tftp.exceptions` (every one is also importable from `tftp`): every one is a **`TFTPError`** except
`WouldBlock`, so `except TFTPError` catches what the library reports on its
own account. A caller's own mistake (a wrong
argument type, an option out of range) is plain `TypeError` or `ValueError`,
not one of these. `TFTPError` plays three roles, told apart by the subclass:

- *What a handler raises to refuse a request*: **`TFTPError`** itself, or `TFTPProtocolError`; the code and message become
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
  Also **`TFTPProtocolError`** (the peer broke the protocol;
  code 4, or 8 for option problems), **`TransferTimeoutError`** (also
  a `TimeoutError`, with `errno` `None`; retries exhausted or the client's `deadline`
  passed) and **`TransferAbortedError`** (cancelled locally:
  `abort()`, server shutdown), both with code 0, and
  **`TransferTooLargeError`** (a download past the client's
  `max_size`; code 3).
- *What `TransferResult.error` holds*: whichever of the above ended the
  transfer, or `None`.

Malformed text raises **`TFTPValueError`**, also a `ValueError`:
**`TFTPDecodeError`** (bytes that are not a packet; `.code` is 4),
`TFTPURL.parse`'s refusals. `str()` of these is the message alone. A capture that is not one and a filter that does not compile raise pktcap's `CaptureFormatError` and `CaptureFilterError`, `ValueError`s that are not a `TFTPError`. **`WouldBlock`**
is a `BlockingIOError`, a signal that a source or sink has nothing ready.

Every exception copies and pickles (`copy.copy`, `multiprocessing`), a
`TransferResult` holding one included. `TFTPError.from_exception(exc)`
(a classmethod, returning a `TFTPError`) maps any exception to the ERROR to
send: a `TFTPError` as is, an `OSError` by errno with the generic text (never
the OS text, which would disclose server paths).

## Command line

`tftp.cli.main(argv=None) -> int` runs the `pytftp` command (`python -m tftp` is equivalent) and
returns the exit status; the command classes are not API. The subcommands are `get`, `put`, `ls`,
`serve`, `relay`, `capture` and `replay`; the extra it needs, every flag, the exit statuses and the
output formats are in `tftp/cli/AGENTS.md`.

## Environment variables

The library reads one environment variable, through `netimps`; the command reads
the others in `tftp/cli/AGENTS.md`, through its argument parser.

- **`NETIMPS_SOCKET_PATCH`** — whether `import netimps` installs `recvmsg` and
  `sendmsg` on the `socket` module on Windows (it changes nothing elsewhere).
  `netimps` is imported by the first call that needs it (see "Gotchas"), so this
  is read then, once. Unset or empty means yes; `1`, `true`, `yes` and `on` mean
  yes and `0`, `false`, `no` and `off` mean no, in any case; any other value makes
  that import raise `ValueError` naming the variable. Default: installed. Pktinfo
  does not depend on the patch.

## Gotchas

- **`netimps` and `pktcap` are imported by the call that needs them**, never by `import tftp`
  or by building a client or a server: `bind()`, the first transfer and `Upstream.parse`
  import `netimps`; reading, writing or dissecting a capture imports `pktcap`.
  `asyncio` is not imported until `AsyncTFTPClient` or `AsyncTFTPServer` is first accessed, so
  the blocking half never loads it; `duho`, `pathlib_next` and `uritools` are imported by the
  command and by `tftp.path` only.
- **A signature that names a netimps type** (`HostLike`, `IPNetworkLike`,
  `Interface`) cannot be resolved by `typing.get_type_hints` at run time, because
  `netimps` is imported lazily; so are the signatures of `dissect_tftp`,
  `register_tftp_dissector` and `pktcap_plugin`, which name the `pktcap` module. Every other public
  annotation resolves on every supported Python. `host=` of the servers and the
  relay is a netimps `HostLike`: text, an `ipaddress` address or interface, a
  `netimps.Host` or `FQDN`; an `int` or `bytes` address is a `TypeError`.
- **`deadline` means two things.** The client's `deadline=` is a number of seconds
  a whole transfer may take; an engine's `deadline` attribute is the instant, in
  its caller's clock, at which it next wants `on_timeout`.
- **Loggers.** The library configures no logging; it emits on four loggers named
  for their role, which do not move when a module does: `tftp.client` (the first
  failure of a `trace` hook, at ERROR with its traceback), `tftp.server` (one INFO
  line per finished or refused transfer, WARNING for a transfer that failed or a
  socket that could not be made, ERROR with a traceback for a handler or callback
  that raised, DEBUG for the options a transfer negotiated and ignored
  broadcasts), `tftp.relay` (the same levels, one INFO line per relayed
  transfer) and `tftp.backends` (WARNING when `HTTPBackend` refuses a `url_for`
  URL). The command logs under `tftp`, adjusted by `-v`, `-q` and `--loglevel`.
- **Who closes a file object that is passed in.** A path given to `download` is
  written through a temporary file and closed; a file object given to
  `download`, `upload`, `AsyncTFTPClient.download` or a `trace_to` writer stays
  the caller's to close, and the library never closes it, even on a failed
  transfer. A reader or writer a handler returns is closed by the server after the
  last block (or `abort()`ed on a failure).
