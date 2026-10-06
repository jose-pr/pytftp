# `tftp.capture` — public API header

Header-file-style reference for the `tftp.capture` package: packet events and
trace hooks, flow tracking and analysis of a capture, TFTP as a pktcap layer,
transfer replay and the event filters. Every public export with its signature,
arguments, contract and gotchas, so the package can be used without reading its
source. It ships inside the package and is self-contained; the top header is
`tftp/AGENTS.md`. Development documentation lives with the source at
<https://github.com/jose-pr/pytftp>.

## Packet events (`tftp.capture`)

```python
PacketEvent(time, direction, local, remote, data, role="capture", session=None, leg=None)
PacketEvent.decode()
PacketEvent.to_dict(payload=False)
summarize(data)
new_session_id(prefix="t")
```

`PacketEvent` — one datagram. `direction` is `"in"`/`"out"` from `role`'s view
(`"seen"` for a passive capture); `session` correlates one transfer's events;
`leg` is the relay side. Properties: `opcode`, `opcode_name`, `block`,
`payload_size`, `summary` (one line, never raises), `source`, `destination`;
methods `decode()`, `to_dict(payload=False)` (JSON-
ready metadata; DATA payloads only as hex with `payload=True`);
`str(event)` is the human line. `summarize(data)` is the one-line description on its own.


```python
combine_hooks(*hooks)
trace_to(writer)
```

**Trace hooks** — `TFTPClient(trace=)`, `AsyncTFTPClient(trace=)`, `TFTPServer(trace=)`,
`AsyncTFTPServer(trace=)` and `TFTPRelay(trace=)` take
`trace(PacketEvent)`, called for every datagram received and sent, on the
thread doing the I/O. An exception it raises never reaches the transfer: the
first one is logged with its traceback (logger `tftp.client`, `tftp.server` or
`tftp.relay`) and the later ones of that hook are counted and not logged, so a
hook that always fails costs one record. `PacketEvent.local` is the address the
route to that peer uses (the socket's own address when it has one), as plain
IPv4 for a mapped address, for the clients and the relay's two legs alike.
Roles are `"client"`, `"server"`, `"relay"`; one `session` id per transfer
(`c…`, `s…`, `r…`; `new_session_id(prefix)` makes one, unique in the process). A server traces a transfer's request as arriving at the
listening address. Requests a server refuses before a transfer exists are
not traced. Off (`None`) costs nothing per packet.

**`combine_hooks`** — one trace hook that calls each of `hooks` in
order (`None` entries skipped; `None` when no hook is left, the hook itself
when one is). A hook that raises does not keep the others from seeing the
event: the first exception is raised after all have been called, for the
guard around the combined hook to count.

**`trace_to`** — a trace hook that
appends each event to `writer`, a **`DatagramWriter`** (a `Protocol`: anything
with `write(time, source, destination, payload)`; `pktcap.PcapWriter(target)`
and `pktcap.PcapngWriter(target)` are two): it calls
`writer.write(event.time, event.source, event.destination, event.data)`.
`with pktcap.PcapWriter("t.pcap") as w: TFTPServer(..., trace=trace_to(w))`.
The writer stays the caller's to close. pktcap's writer creates its file at the
first datagram, so a path that cannot be opened is that hook's one logged
failure (see the trace hooks above) and not an error at construction;
`pytftp ... --pcap FILE` opens the file itself once the command can run, so an
unwritable path is its `error:` line, and a run that moved no datagram leaves a
file of 0 octets. What a pcap file written this way promises is its datagrams,
each with its time to the microsecond, both addresses and its payload, as raw IP
(link type 101) under synthesised IPv4 or IPv6 and UDP headers with valid
checksums, so Wireshark and tshark decode TFTP; a v4-mapped address is written
as IPv4. It does not promise its octets: the header's snap length is 262,144 and
the IPv4 identification counts every datagram. A write the format cannot hold (a
port outside 0-65535, a time outside 0 to 2**32, a payload over 65,507 octets
on IPv4 or 65,527 on IPv6, a host that is not an address) is a `ValueError`
naming the argument, raised in the hook, so it costs the transfer nothing.

**Reading captures** is pktcap's (a dependency, imported inside the functions
that use it; <https://github.com/jose-pr/pktcap>): pcap and pcapng of any
byte order and time resolution, from a path or a stream (a live
`tcpdump -i eth0 -U -w - udp` pipe), every link type read, IPv4 and IPv6
fragments reassembled, non-UDP skipped.
`pktcap.read_datagrams(source)` yields `pktcap.CapturedDatagram(time, source,
destination, payload, fragmented, truncated)`, which `FlowTracker` and
`analyze` take as they take anything with a `time`, a `source`, a
`destination` and a `payload`, and a `truncated` when it has one
(**`DatagramLike`**, a `Protocol`).
`tftp.capture` has no reader, frame decoder or live capture of its own.

## Flows and analysis (`tftp.capture`)

- **A capture is untrusted input**, and pktcap refuses what it cannot bound:
  `analyze` and the command raise `pktcap.CaptureFormatError` (a `ValueError`
  of that library, not a `TFTPError`) for a file that is not a capture or is
  damaged, for a record or block over its ceilings (a frame of more than
  262,144 octets, more than 4,096 interfaces in a section) and for a block
  too short for its kind; none of these is `struct.error` and none claims
  memory before the octets are read. An empty input is a capture with
  nothing in it. A text stream is a `TypeError`; a path that cannot be opened
  is the `OSError`. `pktcap.read_datagrams(source)` itself checks its
  arguments at the call and reads only as it is iterated.

```python
FlowTracker(ports=(69,), *, keep_payloads=True, on_complete=None, max_tracked=1024)
FlowTracker.feed(datagram)
FlowTracker.feed_all(datagrams)
CapturedTransfer.data(decode_netascii=True)
CapturedTransfer.write_to(directory)
CapturedTransfer.add_ack(wire)
CapturedTransfer.add_data(wire, payload, keep=True, cut=False)
CapturedTransfer.to_dict()
analyze(source, *, ports=(69,), filter=None, keep_payloads=True)
```

- **`FlowTracker`** — `feed(datagram: DatagramLike) ->
  PacketEvent | None` (role `"capture"`, direction `"seen"`; `None` for UDP
  that is neither TFTP traffic of a known transfer nor to/from a request
  port), `feed_all(datagrams)`, `.transfers`. A transfer starts at an
  RRQ/WRQ to a request port and follows the server's answer (its TID; from
  another address too, within 10 s) between that TID and the client's
  address/port. `::ffff:a.b.c.d` and `a.b.c.d` count as one host.
  `keep_payloads=False` drops the DATA payloads (`CapturedTransfer.data()` is
  then empty); `size`, `retransmissions` and `missing_blocks` are counted
  either way. At most `max_tracked` transfers are followed (`None`: every one;
  `analyze()` and a bound of `None` keep the whole capture): when a request
  would make one more, the finished transfer (complete or ended by an ERROR)
  quiet for longest, else the quietest, goes to `on_complete(transfer)` and is
  dropped, and a datagram of it is no longer attributed. One DATA moves a
  transfer's highest block by at most its window (64 when that is smaller): a
  block further ahead is counted as a packet and not placed. A packet the
  reading cannot take sets that transfer's `error` to
  `(0, "unreadable packet: <ExceptionType>", "capture")` and the capture goes on.
  **A datagram whose `truncated` is true** (the capture's snap length cut it;
  `DatagramLike.truncated` is optional, absent means whole) is counted as a packet of its
  transfer and is never read as a DATA: its octets are not placed, a short one is not
  the final block, and its block is in `missing_blocks` until a whole one arrives, so the
  transfer is not complete and is written as `.partial`.
- **`CapturedTransfer`** — `session`, `client`, `server`, `server_tid`,
  `filename`, `mode`, `operation`, `requested`, `acknowledged`, `blksize`,
  `windowsize`, `tsize`, `error` (`(code, message, "client"|"server")`),
  `is_complete` (final DATA seen and ACKed), `packets`, `retransmissions` (DATA
  seen again), `request_retransmissions`, `size` (the DATA bytes; `"bytes"`
  in `to_dict()`), `missing_blocks` (a tuple of inclusive `(first, last)`
  ranges of the blocks never seen whole, up to the highest seen; `missing_count`
  counts them), `started`, `ended`, `duration`; `data(decode_netascii=True)`
  returns the file up to the first gap (block numbers followed across
  rollover); `write_to(directory) -> str | None` writes it as
  `<session>-<name>` (`.partial` appended for an incomplete transfer or one
  with gaps; `<name>` is the last component of the requested name with every
  character outside `A-Za-z0-9_.-` replaced by `_`, at most 100 long, so the
  file is always inside `directory`, which is created) and returns the path,
  or `None` when there is nothing to write; `to_dict()` (`"missing_blocks"` is
  a list of `[first, last]` pairs, `"missing_count"` their total).

- **`analyze`**
  — a whole capture (a path or a stream, read by pktcap, or any iterable of
  `DatagramLike`) at once.

## TFTP as a pktcap layer (`tftp.capture`)

```python
TFTPLayer(opcode, block=None, filename=None, mode=None, options=None, code=None, message=None)
dissect_tftp(data)
register_tftp_dissector(registry=None, *, ports=(69,))
```

  `register_tftp_dissector(registry=None, *, ports=(69,)) -> None`** — TFTP as a layer
  for pktcap's dissection (`pktcap.read_dissected`, `frame.layer(TFTPLayer)`, the
  filter `proto=tftp`, `pktcap.frame_record`). **Nothing registers on import**: a
  caller registers, in `registry` (a `pktcap.DissectorRegistry`) or, given none, in
  pktcap's process-wide default one. It is registered under `("udp", port)` for each
  port, the request port only: a transfer's DATA and ACK run between ports chosen
  per transfer, which no selector names, so they carry no `TFTPLayer`; follow them
  with `FlowTracker`. A port already holding a dissector, or outside 1 to 65535, is a
  `ValueError` and this call registers nothing (a non-`int` port is a `TypeError`).
  `TFTPLayer(opcode, block=None, filename=None, mode=None, options=None, code=None,
  message=None)` is a named tuple of plain values: `opcode` is `"RRQ"`, `"WRQ"`,
  `"DATA"`, `"ACK"`, `"ERROR"` or `"OACK"`; `block` is DATA's and ACK's,
  `filename`, `mode` (lower case) and `options` (a tuple of `(name, value)` pairs,
  names lower-cased, in the order sent) a request's, `options` an OACK's too,
  `code` and `message` an ERROR's, each `None` where the packet has none. It equals
  another `TFTPLayer` with the same fields and nothing else (not the same fields
  as a plain tuple), is hashable and immutable, and its repr is a constructor
  call. `dissect_tftp` reads with the package's `decode`: a DATA's payload is what
  follows its four octets, every other packet's is empty, nothing follows a
  TFTP packet, and octets that are not a TFTP packet raise `pktcap.DissectError`
  with the text `not a TFTP packet` and none of the octets (a datagram to the
  request port that is not TFTP is therefore a frame with that `error` and no
  `TFTPLayer`). It passes `pktcap.check_dissector`.

## Replay (`tftp.capture`)

```python
replay_transfers(
    source, host, port=69, *, ports=(69,), writes=False, speed=1.0, max_delay=5.0, limit=None,
    timeout=1.0, retries=5
)
```

  — asks the server at `host` and `port` for each transfer the capture `source` (what `analyze`
  takes) holds again, with a `TFTPClient` built from what the capture's client asked for (its
  mode, file name and options; `timeout` is the argument's, not the captured value). **It is not a
  replay of datagrams** (a transfer runs between ports chosen anew), and **nothing is sent to an
  address the capture holds**: `host` and `port` are the only destination. Transfers run one at a
  time, in the order their requests were seen, after the wait `pktcap.replay_schedule` gives
  (`speed=None` removes the waits, `max_delay` is the longest single one, `limit` ends the
  replay after that many transfers; the rest are neither run nor counted). **A read is asked for
  again and its octets are discarded; a write is replayed only with `writes=True`, and then it
  uploads the octets the capture holds and overwrites a file on the server.** A write the capture
  holds only partly (a missing block, an ERROR, no final acknowledgement) is never replayed. `results`
  is the `TransferResult` of each transfer run (a failure, an ERROR or a timeout, is one with
  `error` set and the replay goes on); `skipped` counts the writes not replayed and the transfers
  the client refuses (a mode it does not send). `ValueError` for an argument out of range,
  `pktcap.CaptureFormatError` for a file that is not a capture, `OSError` when `host` does not
  resolve or a socket fails.

## Live capture and filters (`tftp.capture`)

- **Live capture** is `pktcap.sniff(interface=None, *, stop=None,
  dissector=None)`, Linux only (`AF_PACKET`, needs `CAP_NET_RAW`;
  `pktcap.has_live_capture()` asks the platform). Elsewhere pipe
  `tcpdump`/`dumpcap -w -` into `pytftp capture -` or `pktcap.read_datagrams`.

```python
compile_filter(text)
```

**Filters** — **`compile_filter`**: the grammar is
pktcap's (`pktcap.parse_capture_filter`, `compile_capture_filter`): `key=value`
clauses joined by `and` (any letter case, a space on each side), `,` for "any
of", `!=` to negate; empty or `None` matches all. There is no `or`: the word
alone is refused, so a value cannot contain ` or ` or ` and `, and a trailing
`and` is refused. The first `=` ends the key, so a value may hold `=` and `!`
(`file=a!=b` is the pattern `a!=b`). What a key means is this library's:
`op` (opcode name), `host`/`src`/`dst` (address, CIDR, `addr:port`,
`[v6]:port`, `:port`; mapped v4 matches v4), `port`, `file` (shell pattern,
requests only), `block`, `code` (ERROR code), `session`, `leg`, `direction`
(`FILTER_KEYS`). `pktcap.CaptureFilterError` (a `ValueError`, not a
`TFTPError`), naming the clause, for an expression that does not parse, an
unknown key or a value that does not convert (a port that is not ASCII digits
or is over 65535, text that is no address), once, when the filter is compiled.

## Types (`tftp.capture`)

| Name | Meaning |
| --- | --- |
| `EventPredicate` | what `compile_filter` returns: `predicate(event) -> bool` |
| `Endpoint` | `(host, port)` |
| `DatagramLike` | what `FlowTracker.feed` takes: anything with `time`, `source`, `destination` and `payload`, as `pktcap.CapturedDatagram` has |
| `DatagramWriter` | what `trace_to` takes: anything with `write(time, source, destination, payload)`, as the pktcap writers have |
| `Analysis`, `ReplayedTransfers` | the named results of `analyze` and `replay_transfers` |
| `FILTER_KEYS` | the keys `compile_filter` understands |
