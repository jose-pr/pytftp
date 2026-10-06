# `tftp.client` — public API header

Header-file-style reference for the `tftp.client` package: the two clients, the
one-shot transfer functions, and the `tftp://` URL type that names a transfer.
Every public export with its signature, arguments, contract and gotchas, so the
package can be used without reading its source. It ships inside the package and
is self-contained; the top header is `tftp/AGENTS.md`. Development documentation
lives with the source at <https://github.com/jose-pr/pytftp>.

`AsyncTFTPClient` is bound on first access, so importing `tftp.client` never
imports `asyncio`. A sync and async pair is documented together, the differences
stated once.

## Client (`tftp.client`)

```python
TFTPClient(
    host, port=69, *, timeout=1.0, retries=5, blksize=1428, windowsize=None, tsize=True,
    rollover=None, timeout_option=True, family=0, src=None, fallback=True, dally=False,
    backoff=2.0, max_timeout=None, deadline=None, max_size=None, strict_source=True,
    utimeout=False, extra_options=None, registry=None, on_negotiated=None, trace=None
)
```

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
  starts and across the retry without options; it bounds `size()`, `stat()` and
  the wait for the server's first answer as well. `None` is unlimited. A relative duration: the client's
  `deadline` argument and attribute are not the engine's `deadline` (the
  instant it next wants `on_timeout`, see "Transfer engine").
- `max_size` — octets a download may bring; `None` is unlimited (a download
  to a file has no default bound). A server announcing a `tsize` above it is
  sent ERROR 3 before any data moves, and a block that would pass it is not
  written: ERROR 3 and `TransferTooLargeError` (code 3). A server sending more
  than the `tsize` it announced (octet mode) is `TFTPProtocolError` after
  ERROR 4. `download`, `get` and `listdir` take `max_size=` to override it for
  one call; `listdir` defaults to 16 MiB.
- The first answer to a request is an ERROR, an OACK, DATA 1 (a read) or
  ACK 0 (a write), whole: anything else (another block, a packet cut short,
  one that does not decode) is `TFTPProtocolError` after an ERROR 4.
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
- `trace` — a hook called with a `PacketEvent` for every datagram sent and received
  (`tftp/capture/AGENTS.md`); an exception it raises never reaches the transfer.
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
  and follows a server that wraps to 1. With a window above 1 it follows
  after the server's window has been sent again from block 1, one round trip
  later: a block 1 alone may be block 65537 behind a lost block 65536.
- `family` — `socket.AF_INET`/`AF_INET6` to force one; `0` is whatever the host
  resolves to first.
- `src` — `(address, port)` to send from; the address in any form
  `host` takes.
- `fallback` — if the server answers the *request* with ERROR 8 (options
  refused), 4 (illegal operation) or 0 (not defined), the errors a server
  older than the option extension sends for the extra data, ask again once
  without options (RFC 2347: the client "may" repeat it).
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

```python
TFTPClient.download(filename, dst, *, mode="octet", progress=None, max_size=None)
TFTPClient.get(filename, *, mode="octet", max_size=None)
TFTPClient.upload(filename, src, *, mode="octet", progress=None)
TFTPClient.put(filename, data, *, mode="octet")
TFTPClient.size(filename, *, mode="octet")
TFTPClient.stat(filename, *, mode="octet")
TFTPClient.listdir(dirname="", *, max_size=16777216)
TFTPClient.path(*segments, mode="octet")
```

- **`download`** — `dst` is a path or a writable binary file; a file object stays the caller's and is
  never closed, even when the transfer fails. A path is written to a hidden
  temporary file beside it (`.name.*.part`, which a process killed mid-transfer
  leaves behind) that **replaces the path only when the transfer succeeds**: a
  failed download leaves the file that was there, and the directory must be
  writable. A symlink at `dst` is replaced, not written through. A path that
  exists and is not a regular file (a device, a pipe) is written in place and
  never removed. On Windows a file another handle has open cannot be replaced:
  `PermissionError`, the file intact.
- **`get`** — download into memory; returns `bytes`.
- **`upload`** — `src` is a path, a readable binary file (`readinto` or `read`; it stays the caller's
  and is never closed) or bytes-like.
- **`put`** — upload bytes.
- **`size`** — the file's size, an `int` or `None`, without transferring it: an RRQ asking only for `tsize`, abandoned with
  ERROR 8 once the server answers (a server counts that as `declined`, not
  failed). `None` when the server reports no sizes, unless the file fits in
  the first 512-byte block. Raises like a download (`FileNotFound`...).
- **`stat`** — returns a `RemoteStat(size, mtime, is_dir)` from one probe like `size()`, also asking for `x-mtime` and `x-list`: a server
  allowing them reports the modification time (seconds since the epoch) and
  recognises a directory (`size` is then `None`); others leave `mtime`
  `None` and report a directory as `FileNotFound`. A request refused with
  ERROR 8, 4 or 0 is retried as a plain size probe (with `fallback`). `filename`
  `""` is the root.
- **`listdir`** — a list of `ListEntry(name, is_dir, size, mtime)` from a server speaking `x-list`. `NotADirectoryError`
  when the name is a file (the transfer is abandoned at once);
  `FileNotFound` when it does not exist — which is also what a server
  without listing support answers for a directory.
- **`path`** — a `TFTPPath` bound to this client; see `tftp/path/AGENTS.md` (needs the `path` extra).
- `mode` is `"octet"` or `"netascii"`; `"binary"`/`"ascii"` are aliases. Any
  other value raises `ValueError`.
- `progress(done_bytes, total_or_None)` is called after each packet that moved
  data; `total` is the negotiated `tsize`.
- Raises `RemoteError` (the server sent ERROR), `TransferTimeoutError`,
  (as the subclass for its code: `FileNotFound`, `AccessViolation`, ...),
  `TFTPProtocolError` (the server broke the protocol, e.g. an OACK with a larger
  `blksize` than requested — the client sends ERROR 8 first, or a DATA longer
  than the negotiated `blksize`), `TransferTooLargeError` (`max_size`), or the
  local exception for a local failure: an `OSError` from the file or stream
  (resolution, the local file, a send the host refuses: the asyncio client
  raises it at once and does not wait out the retries), as it was raised, after
  the server was sent the ERROR for it.

```python
download(host, filename, dst, /, *, port=69, mode="octet", progress=None, **client_options)
upload(host, filename, src, /, *, port=69, mode="octet", progress=None, **client_options)
```

The module functions **`download`** and **`upload`** — one-shot wrappers;
`client_options` go to `TFTPClient`. The operands are positional-only, so
`src=` in `client_options` is the client's source address, not the data.

**`MODES`** — `("octet", "netascii")`.

## asyncio client (`tftp.client`)

```python
AsyncTFTPClient(
    host, port=69, *, timeout=1.0, retries=5, blksize=1428, windowsize=None, tsize=True,
    rollover=None, timeout_option=True, family=0, src=None, fallback=True, dally=False,
    backoff=2.0, max_timeout=None, deadline=None, max_size=None, strict_source=True,
    utimeout=False, extra_options=None, registry=None, on_negotiated=None, trace=None
)
```

```python
async AsyncTFTPClient.download(filename, dst, *, mode="octet", progress=None, max_size=None)
async AsyncTFTPClient.get(filename, *, mode="octet", max_size=None)
async AsyncTFTPClient.upload(filename, src, *, mode="octet", progress=None)
async AsyncTFTPClient.put(filename, data, *, mode="octet")
async AsyncTFTPClient.size(filename, *, mode="octet")
async AsyncTFTPClient.stat(filename, *, mode="octet")
async AsyncTFTPClient.listdir(dirname="", *, max_size=16777216)
AsyncTFTPClient.stream(filename, *, mode="octet", buffer=1048576)
```

**`AsyncTFTPClient`** (from `tftp` and `tftp.client`) — a sibling of
`TFTPClient`, not a subclass: the same arguments and rules (options, backoff,
fallback, `trace`, `on_negotiated`); coroutine methods `download`, `get`, `upload`, `put`, `size`, `stat` (both in the executor), `listdir`, and the async generator **`stream`** yielding chunks as they arrive (a slow consumer holds ACKs
back; at most `buffer` bytes are held, and closing the generator early ends the
transfer and sends the server ERROR 0). Name resolution runs in the
executor. Destinations: a path (written on the loop — fine for local
files) or an object with `async write(data)`; `download` returns only once
that writer has taken every byte (it is not closed). Sources: a path, bytes
or an object with `async read(n)`. Cancelling the task sends the server ERROR 0. Each
attempt (including the option fallback) uses a fresh socket. A transfer owns
what it starts: when a call returns or raises, whether it succeeded, failed,
timed out or was cancelled, none of its tasks is pending and its socket is
closed, and an upload's source is read only after the server has accepted the
request. The blocking client likewise sends ERROR 0 when an exception from a
`progress` callback or a signal interrupts a transfer.

## Types (`tftp.client`)

| Name | Meaning |
| --- | --- |
| `SinkLike` | where a download goes: a path, or any object with `write(data)` |
| `SourceLike` | what an upload sends: a path, bytes, or any object with `readinto(buffer)` or `read(size)` |
| `ProgressFunction` | `progress(done_bytes, total_or_None)` |
| `AsyncSink`, `AsyncSource` | the objects `AsyncTFTPClient` writes a download to and reads an upload from |
| `RemoteStat` | what `stat()` returns: `size`, `mtime`, `is_dir` |

```python
RemoteStat(size, mtime=None, is_dir=False)
```

## URLs (`tftp`)

`TFTPURL`, `download_url` and `upload_url` are exported from `tftp`; a URL is
how a transfer is named for the clients above, the command line and `TFTPURIPath`.

```python
TFTPURL(host, port, filename, mode="octet", options=None)
TFTPURL.parse(text)
TFTPURL.try_parse(text, default=None)
download_url(url, dst, /, *, progress=None, **client_options)
upload_url(url, src, /, *, progress=None, **client_options)
```

- **`TFTPURL`** — a frozen,
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
- **`TFTPURL.parse`** — RFC 3617's `"tftp://" host "/" file [ mode ]`
  plus an optional `:port` (default 69; `0` and an empty port mean 69), `/`
  inside the file name, and options. The file name and the option names and
  values are percent-decoded as UTF-8 with `surrogateescape` (the packet
  codec's own, so any octet sequence round-trips); a `%25` zone decodes.
  Raises `TFTPValueError` for another scheme, no host or file, a fragment
  (`#`), userinfo, a non-digit or out-of-range port, a control character, a NUL
  in the file name, both option delimiters unencoded, a repeated name, an
  empty pair, a pair with no `=`, an option value its name cannot read, and
  `mode=mail` (write `%23` for a literal `#`); `TypeError` for a non-`str`.
  **`TFTPURL.try_parse`** returns `default` instead of
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
- **`download_url`**,
  **`upload_url`** — one-shot transfers by URL; the operands
  are positional-only, so `src=` in `client_options` is the client's source
  address. The URL's options become client keywords; a keyword in
  `client_options` wins over the URL's option of the same name, and
  `extra_options` merge name by name with the keyword winning. The command
  line (`pytftp get|put|ls tftp://...`) and `TFTPURIPath` follow the same
  rule: a flag or a `with_options` argument wins over the URL.
