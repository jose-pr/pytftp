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

`Transfer` is their shared base: the state both directions carry (`is_done`,
`error`, `deadline`, `is_stalled`, `bytes`, `retransmits`) and `max_idle`, the
seconds without a datagram from the peer after which the transfer fails.
`SendFunction` is what an engine calls to send one datagram and `SupportsWrite`,
`SupportsReadinto` and `SupportsRead` are the stream shapes `as_write` and
`as_readinto` take.
