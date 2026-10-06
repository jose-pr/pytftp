# `tftp.options` — public API header

Header-file-style reference for the `tftp.options` package: the negotiation
policy, the option handlers and their registry, the built-in options, the
profiles for quirky peers and the functions that apply them. Every public export
with its signature, arguments, contract and gotchas, so the package can be used
without reading its source. It ships inside the package and is self-contained;
the top header is `tftp/AGENTS.md`. Development documentation lives with the
source at <https://github.com/jose-pr/pytftp>.

## Policy (`tftp.options`)

```python
TFTPServerOptions(
    *, max_blksize=65464, max_windowsize=64, max_window_bytes=4194304, allowed=None, refused=(),
    fit_mtu=False, registry=None
)
TFTPServerOptions.replace(**changes)
TFTPServerOptions.accepts(name)
```

The negotiation policy of a server. `TFTPServerOptions` and `Negotiated` are mutable: they compare
equal when every field does, are unhashable and print their fields.

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
- `replace(**changes)` — a new policy with every field of this one except
  those named, built through the constructor (so validated), as
  `dataclasses.replace` does; a name that is not a field is `TypeError`.
- `accepts(name)` — `name` in `allowed` and not in `refused`.

```python
Negotiated(
    *, blksize=512, windowsize=1, timeout=1.0, tsize=None, rollover=0, options=None, extra=None
)
```

**`Negotiated`** — what a transfer ran with: `blksize`, `windowsize`,
`timeout` (seconds), `tsize` (or `None`), `rollover`, and `options` (the OACK
as sent/received; empty when RFC 1350 defaults applied).

## Handlers and the registry (`tftp.options`)

```python
OptionRegistry(handlers=BUILTIN_OPTIONS)
OptionRegistry.register(handler, *, replace=False)
OptionRegistry.unregister(name)
OptionRegistry.get(name)
register_option(handler, replace=False)
```

Every option is an **`OptionHandler`** (`name`, `standard`) with
`negotiate(value, ctx) -> str | None` (server: the value to acknowledge, or
`None` to leave it out — RFC 2347's refusal; set fields on `ctx.result`) and
`accept(requested, acked, ctx)` (client: validate and apply; raise
`tftp.options.refuse(msg)` for ERROR 8). `ServerOptionContext` carries `result`,
`requested`, `acked`, `policy`, `is_read`, `size`, `mtu`, `ipv6`, `stream`
(what the handler opened for an RRQ, else `None`) and `max_blksize` (policy
limit after `fit_mtu`); `ClientOptionContext` carries
`result`, `requested`, `is_read`. Custom values go in `ctx.result.extra`.

**`OptionRegistry`** — `register(handler, *,
replace=False)` (a duplicate name raises, and so does a name that is not
lower-case: a request's names are lower-cased, so it could never match),
`unregister(name)`, `get(name)`,
`in`, iteration, `names()`, `standard()`, `copy()`. **Registration order is
negotiation order** (`windowsize` after the block size it depends on).
`DEFAULT_REGISTRY` is what servers and clients use unless given another;
`register_option(handler)` adds to it. A custom option must also be in a
server's `allowed`.

Built-ins: `blksize`, `timeout`, `tsize`, `windowsize` (standard), and
`blksize2` (largest power of two ≤ the request and the limit; ignored when
`blksize` was acknowledged), `utimeout` (10 000..255 000 000 µs; beside it `timeout` is acknowledged only
when it names the same time, as tftp-hpa does),
`rollover` (0/1), `cookie` (echoed unchanged; a client refuses a changed
one), `mstfwindow` (`MstfwindowOption`: answers `31416` with `27182` and runs a
window of 4, lowered by `max_window_bytes` like `windowsize`, unless `windowsize` was
also acknowledged; a client refuses any other answer), `x-list` (`XListOption`: acknowledged `1` only when the RRQ's stream
is a listing, `lists_directories = True`), `x-mtime` (`XMtimeOption`: an RRQ's OACK carries
the stream's `mtime` attribute or `fstat` time, whole seconds; omitted when
unknown).

The functions the engines call, for a caller that speaks the protocol itself:

```python
negotiate(requested, policy, *, is_read, timeout, size=None, mtu=None, ipv6=False, stream=None)
accept_oack(requested, oack, *, is_read, timeout, registry=None)
request_options(
    *, blksize=None, windowsize=None, timeout=None, tsize=None, rollover=None, utimeout=False,
    extra=None
)
refuse(message)
```

- `negotiate(requested, policy, *, is_read, timeout, ...)` — server side: decide
  which requested options to acknowledge; one the server does not know, does not
  allow or that carries an unusable value is left out (RFC 2347's refusal) and
  the transfer runs on its default. `size` is the file size for an RRQ's `tsize`,
  `mtu` and `ipv6` feed `fit_mtu`, `stream` is the RRQ's opened source.
- `request_options(*, blksize=None, ...)` — client side: the options to put in a
  request. `timeout` is sent as `timeout` when whole (1..255) and as `utimeout`
  only with `utimeout=True`; `tsize` is `0` for a read and the upload size for a
  write; `extra` adds options verbatim after the built-in ones.
- `accept_oack(requested, oack, *, is_read, timeout, registry=None)` — client
  side: validate a server's OACK against what was requested; it raises
  `TFTPProtocolError` with code 8 for an option that was not requested or an
  answer outside what the option's RFC allows. The caller sends that ERROR and
  abandons the transfer.
- `refuse(message)` — the ERROR 8 a client sends for an OACK it cannot accept.

The built-in handler classes are `BlksizeOption`, `Blksize2Option`,
`TimeoutOption`, `UtimeoutOption`, `TsizeOption`, `WindowsizeOption`,
`RolloverOption`, `CookieOption`, `MstfwindowOption`, `XListOption` and
`XMtimeOption`; `BUILTIN_OPTIONS` is the tuple of one of each.

## Profiles (`tftp.options`)

```python
Profile(name, server, client)
```

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

```python example
tftp.TFTPServer("/srv/tftp", options=tftp.Profile.PXE.server)
tftp.TFTPClient("192.0.2.1", **tftp.Profile.LEGACY.client)
```

## Constants (`tftp.options`)

`DEFAULT_BLKSIZE` 512, `MIN_BLKSIZE` 8, `MAX_BLKSIZE` 65464, `MAX_WINDOWSIZE`
65535, `MIN_UTIMEOUT` and `MAX_UTIMEOUT` (the `utimeout` bounds in microseconds),
`STANDARD_OPTIONS`, `EXTENSION_OPTIONS`, `LISTING_OPTIONS` (`x-list`, `x-mtime`),
`SUPPORTED_OPTIONS` (standard and extensions), `PROFILES` and `DEFAULT_REGISTRY`.
