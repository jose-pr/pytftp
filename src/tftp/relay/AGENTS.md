# `tftp.relay` — public API header

Header-file-style reference for the `tftp.relay` package: the transparent relay
and its routing helpers. Every public export with its signature, arguments,
contract and gotchas, so the package can be used without reading its source. It
ships inside the package and is self-contained; the top header is
`tftp/AGENTS.md`. Development documentation lives with the source at
<https://github.com/jose-pr/pytftp>.

## Relay (`tftp.relay`)

```python
TFTPRelay(
    route, *, host=None, port=69, idle_timeout=30.0, max_duration=3600.0, linger=2.0,
    upstream_src=None, limits=None, max_sessions=None, ignore_broadcast=True,
    reply_from_request_address=True, trace=None, on_session_end=None, port_range=None,
    interface=None
)
```

`TFTPRelay` — a transparent application relay (there is no standard TFTP relay). The
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


## Routing (`tftp.relay`)

```python
Upstream(host, port=69)
Upstream.parse(value)
by_subnet(table)
by_prefix(table)
by_interface(table)
RouteTable(routes, default=None)
```

Routing helpers (`tftp.relay`): **`Upstream`** — a frozen,
hashable value that equals only another `Upstream` (`port` an `int` in
1..65535: `TypeError`, or `TFTPValueError` outside the range) and
**`Upstream.parse`**, which takes a host (`host` read by
`netimps.split_host`: brackets dropped, a `"host:port"` split, the port ASCII
digits only), an address or `netimps.Host` (port 69), `(host, port)` or an
`Upstream`;
**`by_subnet`** (keys: anything `netimps.parse(...,
IPNetwork)` takes — CIDR strings, `ipaddress` networks, interfaces, addresses
as /32 or /128, `(address, prefix)`; client address, longest prefix; mapped
v4 matches v4 networks); **`by_prefix`** (filename,
longest prefix, `\` = `/`, leading separators ignored); **`by_interface`** (keys: an interface index, a `netimps.Interface`, or the local
address the request was sent to — string, `ipaddress` address or interface,
`netimps.Host`; needs pktinfo); **`RouteTable`** (first
match wins).

## Types (`tftp.relay`)

| Name | Meaning |
| --- | --- |
| `UpstreamLike` | what a route may return: an `Upstream`, a host, or `(host, port)` |
| `RouteFunction` | the type of a `route(request, context)` callable |
| `RelaySummary` | what `on_session_end` receives, one per relayed transfer (fields above); `to_dict()` is the command's `--json` object |
