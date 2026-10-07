# Relaying

There is no standard TFTP relay (nothing like DHCP's), so `tftp.relay.TFTPRelay`
is an application-level one. The request is forwarded **byte for byte** to an
upstream server from a fresh socket; the upstream's transfer ID is learned
from its first answer; then datagrams cross unchanged:

```
client:C  --RRQ-->  relay:69                (request, forwarded untouched)
relay:U   --RRQ-->  upstream:69
upstream:S  <->  relay:U                    (the upstream's TID, learned)
relay:D     <->  client:C                   (a fresh client-side TID)
```

Because nothing is re-encoded, options and vendor extensions this library has
never heard of still work end to end, and the client negotiates directly with
the upstream.

```python
from tftp.relay import TFTPRelay, RouteTable, by_prefix, by_subnet

route = RouteTable(
    [
        by_prefix({"windows/": "wds.lan"}),
        by_subnet({"10.1.0.0/16": "10.1.0.5", "10.2.0.0/16": "10.2.0.5"}),
    ],
    default="10.0.0.20",
)
TFTPRelay(route, on_session_end=print).serve_forever()
```

A route is any plain callable `route(request, context)` returning an upstream
(`"host"`, `"host:port"`, `(host, port)`) or `None` to refuse (ERROR 2).
`context` has the client's address, and the address and interface the request
arrived on.

## When does a relayed transfer end?

A transparent relay cannot understand every extension, so it uses several
signals: an ERROR in either direction, the final DATA/ACK exchange (then a
short linger for retransmissions), no traffic for `idle_timeout`, or
`max_duration`. Each ends with a `RelaySummary` (bytes each way, reason,
error).

## Relay or proxy?

| | `TFTPRelay` | `UpstreamBackend` + `TFTPServer` |
| --- | --- | --- |
| packets | forwarded unchanged | each side is its own transfer |
| options | client and upstream negotiate directly | each side negotiates its own |
| unknown extensions | pass through | not understood, so not forwarded |
| different block/window sizes per side | no | yes |
| backpressure | end to end | through a bounded buffer |

Use the proxy when an old boot ROM at 512-byte lock-step should still get the
upstream at 8 KiB windows, or to put policy (and caching) in between; use the
relay to reach servers on another network without touching the protocol.

Firewall/NAT "TFTP helpers" are a third, different thing: they open
connection-tracking state for the server's new port. This library does not
touch OS firewall state.
