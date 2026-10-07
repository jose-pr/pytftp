# Capture and debugging

## Tracing your own traffic

`TFTPClient`, `TFTPServer` and `TFTPRelay` take `trace=`, called with a
`tftp.capture.PacketEvent` for every datagram sent and received:

```python
import pktcap
import tftp
from tftp.capture import trace_to

def show(event):
    print(str(event))          # 12:00:00.123456 [s3] 10.0.0.5:2000 > 10.0.0.1:69 RRQ 'pxelinux.0' octet blksize=1432 tsize=0

tftp.TFTPClient("192.0.2.1", trace=show).get("pxelinux.0")

with pktcap.PcapWriter("server.pcap") as pcap:           # open it in Wireshark
    tftp.TFTPServer("/srv/tftp", trace=trace_to(pcap)).serve_forever()
```

`trace=` takes one hook: `tftp.capture.trace_to(writer)` makes one of a pktcap writer
(`pktcap.PcapWriter` for pcap, `pktcap.PcapngWriter` for pcapng; the caller closes it),
and `tftp.capture.combine_hooks(show, trace_to(pcap))` joins several (a hook that raises
does not keep the others from the event).

Events carry a `session` id per transfer, the direction, both addresses, the
raw datagram, and decoded fields (`opcode_name`, `block`, `summary`,
`to_dict()`). From the command line, `--trace` prints them and `--pcap FILE`
records them, on `get`, `put`, `serve` and `relay`.

## Reading captures

pcap and pcapng files, from tcpdump, dumpcap or Wireshark, or a live pipe, are read by
[pktcap](https://github.com/jose-pr/pktcap), a dependency of this library, and followed here:

<!-- not run: it reads a capture file the reader recorded -->
```python
from tftp.capture import analyze

analysis = analyze("boot.pcapng", filter="op=RRQ,WRQ,ERROR")
for transfer in analysis.transfers:
    print(transfer)                     # CapturedTransfer(c1 read 'bootx64.efi' ..., complete)
    print(transfer.acknowledged, transfer.retransmissions, transfer.missing_blocks)   # gaps as (first, last) ranges
    transfer.write_to("recovered")      # recovered/c1-bootx64.efi, whatever the capture called it
```

`write_to(directory)` names the file `<session>-<name>`, adds `.partial` when the
transfer is incomplete or has gaps, replaces every character outside
`A-Za-z0-9_.-` in `<name>`, and cannot leave `directory`: a capture is input
from the network, and the name in it is the peer's choice.

A live capture holds a bounded number of transfers (`FlowTracker(max_tracked=1024)`):
past it, the finished one that has been quiet longest (else the quietest) is handed
to `on_complete(transfer)` and dropped. `analyze()` keeps every transfer. Text a peer
chose (a mode, an option, an ERROR message) is escaped in a summary, so a capture
cannot send escape sequences to your terminal; a timestamp the platform cannot
convert is printed as the number.

A transfer is followed from its RRQ/WRQ to the request port, through the
server's new port, to the end: negotiated options, retransmissions, errors,
whether the last block was acknowledged, and the file itself (across
block-number rollover; netascii decoded). pktcap reads every frame of a capture
and dissects Ethernet with VLAN tags, Linux cooked captures, raw IP and loopback
captures; IP fragments -- which large blocks produce -- are reassembled.

A capture is untrusted input, so pktcap refuses what it cannot bound: `analyze` and
`pytftp capture` raise or report `pktcap.CaptureFormatError` (a `ValueError`) for a
file that is not a capture, a damaged one, a frame of more than 262,144 octets or
a block too short for its kind. `pktcap.read_datagrams(source)` gives the datagrams
of a capture, and `FlowTracker.feed` takes anything with a `time`, a `source`, a
`destination` and a `payload` (and a `truncated`, when it has one).

A frame no dissector reads is reported, never taken for "no traffic": `pytftp capture`
prints one `warning: N of M frames not read: ...` line on stderr naming the count and the
link-type numbers, and exits 2 when every frame was of an unsupported link type (802.11, for
one). A datagram the capture's snap length cut is listed as a packet but never read as the
last block of its transfer: the transfer is incomplete, its block is in `missing_blocks`, and
`--extract` writes it with `.partial`.

```bash
pytftp capture boot.pcapng --transfers
tcpdump -i eth0 -U -w - udp | pytftp capture - --filter "host=10.1.0.0/16 and op=ERROR"
sudo pytftp capture -i eth0 --extract recovered/            # Linux, live
```

## TFTP in pktcap

`tftp.capture.dissect_tftp` is the dissector pktcap's frame walk calls for the TFTP layer,
`TFTPLayer` its record, and `register_tftp_dissector` the call that puts it in a registry
(nothing registers when `tftp.capture` is imported):

```python
import pktcap
from tftp.capture import TFTPLayer, register_tftp_dissector

registry = pktcap.DissectorRegistry()
register_tftp_dissector(registry)                     # udp port 69; ports=(69, 6969) for more
dissector = pktcap.FrameDissector(registry)
for frame in pktcap.read_dissected("boot.pcapng", dissector=dissector):
    request = frame.layer(TFTPLayer)
    if request is not None:
        print(frame.datagram().source, request.opcode, request.filename)
```

The layer is on the datagrams to the request port: a transfer's DATA and ACK run between ports
chosen per transfer, which no selector names, so `FlowTracker` follows them. With the layer
registered, `pktcap.compile_capture_filter("proto=tftp", pktcap.frame_filter)` selects the
frames that have it and `pktcap.frame_record` writes its fields.

pktcap's own commands load it by name: `PKTCAP_PLUGINS=tftp.capture pktcap convert -i boot.pcapng -f
"op=RRQ and file=*.efi"`. `tftp.capture.pktcap_plugin` adds the keys `op`, `file`, `block` and `code`
(and `tftp.FIELD` for every field of `TFTPLayer`), which read the same text as `pytftp capture --filter`
does, except that `op` refuses a name that is none of the six opcodes. The layer is on the datagrams
to or from the request port only, so `block=` and `op=DATA` select what was sent there; follow a
transfer with `pytftp capture`.

## Replay

`pytftp replay FILE HOST` asks a server you name for each transfer the capture holds again, each
by a client built from what the capture's client asked for (mode, file name, options) and paced
by the capture's own times. It is **not a replay of datagrams**: after the request a transfer runs
between ports chosen anew, so the request is repeated and the client answers the server as any
client does. **Nothing is sent to an address in the capture**: `HOST` is the only destination.

```bash
pytftp replay boot.pcapng 192.0.2.1 --speed 10
pytftp replay boot.pcapng 192.0.2.1 -p 6969 --json --limit 5
```

- A **read** is asked for again and its octets are discarded. A **write** is replayed only with
  `--writes`, and then it uploads the octets the capture holds of it, **which overwrites that file
  on the server**; a write the capture holds only partly is never replayed.
- `--speed` divides each recorded gap (default 1.0, the recorded pace) and `--max-delay` bounds
  any single wait, so a capture with a jump of a year does not hang a replay.
- One line per transfer on stdout (`--json`: the transfer's result), and a count of what was
  replayed, failed and skipped on stderr. Status 0 when every transfer run succeeded, 1 when one
  failed, 2 for a file that is no capture.

```python
from tftp.capture import replay_transfers

done = replay_transfers("boot.pcapng", "192.0.2.1", speed=10.0, max_delay=1.0)
print(len(done.results), "run,", done.skipped, "skipped")
```

## Filters

`key=value` clauses joined by `and`; a comma means "any of"; `!=` negates. The grammar is
pktcap's, and there is no `or`; an expression that does not parse, an unknown key or a bad
value is a `pktcap.CaptureFilterError` (a `ValueError`) naming the clause.

| key | matches |
| --- | --- |
| `op` | opcode name: RRQ WRQ DATA ACK ERROR OACK |
| `host`, `src`, `dst` | an address, a CIDR, `addr:port`, `[v6]:port` or `:port` |
| `port` | either end's port |
| `file` | requested filename, shell pattern |
| `block`, `code` | DATA/ACK block, ERROR code |
| `session`, `leg`, `direction` | transfer id, relay side, in/out/seen |
