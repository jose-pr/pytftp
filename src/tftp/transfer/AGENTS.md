# `tftp.transfer` — public API header

Header-file-style reference for the `tftp.transfer` package: the I/O-free transfer engine that the
clients and servers drive, and the adapters that turn a file object into its `read` and `write`.
Every public export with its signature, arguments, contract and gotchas, so the package can be used
without reading its source. It ships inside the package and is self-contained; the top header is
`tftp/AGENTS.md`. Development documentation lives with the source at
<https://github.com/jose-pr/pytftp>.

## Transfer engine (`tftp.transfer`)

```python
Sender(
    send, read, negotiated, retries, now, oack=None, *, backoff=2.0, max_timeout=None,
    expires=None, max_idle=None
)
Receiver(
    send, write, negotiated, retries, now, reply=None, complete=None, *, backoff=2.0,
    max_timeout=None, expires=None, max_idle=None
)
Sender.handle(packet, n, now)
Sender.on_timeout(now)
Sender.resume(now)
Sender.abort(message="transfer aborted")
as_readinto(source)
as_write(sink)
```

One side of the DATA/ACK exchange **without any I/O**: packets leave through `send(packet)`,
arrive through `handle(packet, n, now)` (`packet` a `memoryview` of the receive buffer, `n`
octets of it valid), and the caller calls `on_timeout(now)` once `deadline` passes and `resume(now)` when a stalled source or sink (`WouldBlock`) is ready again;
`expires` is the instant, in the caller's clock, at which the transfer is abandoned, and
`abort(message)` cancels. A datagram of fewer than 4 octets is dropped by both. A repeated
OACK is tolerated: a receiver re-sends its ACK 0, a sender ignores it (its own timeout resends
DATA 1). Build `read` and `write` with `as_readinto(fileobj)` and `as_write(fileobj)`:
`as_readinto` fills each block (a source that returns `None` from `readinto` or `read` has
nothing ready: `WouldBlock`), and `as_write` writes until a block is taken (a sink that takes
part of one is written again for the rest; one that takes nothing raises `OSError`; a raw
sink's `None` is `WouldBlock`). This is what the client and server drive; use it to run TFTP
over another transport or event loop.

## The client's opening (`tftp.transfer`)

```python
Requester(
    send, server, opcode, filename, retries, now, *, mode="octet", options=None, timeout=1.0,
    backoff=2.0, max_timeout=None, expires=None, registry=None, accepts=None, fallback=True,
    probe=False
)
Requester.handle(packet, n, address, now)
Requester.on_timeout(now)
```

The exchange before a transfer **without any I/O**: the constructor sends the request through
`send(packet, address)` (`server` for the request, the answer's address for a refusal) and arms
`deadline`; `handle(packet, n, address, now)` takes every datagram that arrives and
`on_timeout(now)` repeats the request, `retries` times with `backoff`, until `expires` (an instant
in the caller's clock) or the retries are spent. A datagram under 2 octets, or one `accepts(address)`
refuses, is dropped without a reply; the first other is the answer and its address is `peer`.
`accepts=None` takes the first from anywhere. An `OSError` from `send` propagates, the outcome
already recorded.

When `is_done` is set, `error` says what failed: the `RemoteError` of the server's code,
`TFTPProtocolError` for an answer no server may send (after ERROR 4) or an OACK that is not what
was asked (after ERROR 8), `TransferTimeoutError` ("transfer exceeded its time limit", or "no
response from HOST:PORT"). Otherwise `negotiated` is the answer's settlement (an OACK checked
against `options`, or the defaults for DATA 1 of a read or ACK 0 of a write), `oack` the server's
options as sent, and `first_data` the DATA 1 datagram, to be handed to the `Receiver` built next.
`retry_without_options` is true when the request carried options, `fallback` is on and the answer
was ERROR 8, 4 or 0: build a second `Requester` with `options={}` and the same `expires`. With
`probe=True` the answer is declined and `negotiated` stays `None`: ERROR 8 to an OACK (its options
in `oack`), ACK 1 to a DATA 1 under 512 octets, ERROR 0 to a full one; `first_data` holds the DATA.

`Transfer` is their shared base: the state both directions carry (`is_done`,
`error`, `deadline`, `is_stalled`, `bytes`, `retransmits`) and `max_idle`, the
seconds without a datagram from the peer after which the transfer fails.
`SendFunction` is what an engine calls to send one datagram and `SupportsWrite`,
`SupportsReadinto` and `SupportsRead` are the stream shapes `as_write` and
`as_readinto` take.
