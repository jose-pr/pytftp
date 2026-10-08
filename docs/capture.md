# Capture and debugging

Reading, writing, dissecting, filtering and replaying captures are [pktcap](https://github.com/jose-pr/pktcap)'s,
and it is the `pktcap` extra: `pip install "tftp[pktcap]"` (with `tftp[cli,pktcap]`, `pytftp capture` and
`pytftp replay` work). Transferring, serving, relaying and `trace=` hooks need none of it; a function or
a command that does prints `captures need the 'pktcap' extra: pip install "tftp[pktcap]"` (an
`ImportError` from the function; on stderr with status 1 from `pytftp capture`, `pytftp replay` and
`--pcap` on the other commands, before a socket is bound).

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
pktcap, and followed here:

<!-- not run: it reads a capture file the reader recorded -->
```python
from tftp.capture import analyze

analysis = analyze("boot.pcapng")
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

A frame no dissector reads is reported, never taken for "no traffic": the summary line
`pytftp capture` prints on stderr counts the frames of an unsupported link type (802.11, for one) and
the malformed ones, with the link-type numbers. A datagram the capture's snap length cut is never read
as the last block of its transfer: the transfer is incomplete, its block is in `missing_blocks`, and
`--extract` writes it with `.partial`.

## `pytftp capture`

```bash
pytftp capture --input boot.pcapng --transfers
tcpdump -i eth0 -U -w - udp | pytftp capture --input - --filter "host=10.1.0.0/16 and op=ERROR"
sudo pytftp capture --interface eth0 --extract recovered/   # Linux, live
pytftp capture --input boot.pcapng --format json --filter "op=DATA and tftp.session=c3"
```

The command is pktcap's capture command (`pktcap.cli.Capture`) with the `tftp.capture` plugin always
loaded: it reads a file, standard input or an interface, follows each transfer across its ports with
`follow_transfers`, and writes the TFTP packets the way pktcap writes: by default one readable line a
packet, which is pktcap's `text` output with `TFTPLayer.summary()` after the addresses,

```text
2023-11-14T22:13:20.512345Z 192.0.2.5:2000 > 192.0.2.1:69 tftp: [c1] RRQ 'boot/ipxe.efi' octet blksize=512
2023-11-14T22:13:20.549380Z 192.0.2.1:40001 > 192.0.2.5:2000 tftp: [c1] DATA 1
```

and with `--format json` pktcap's record: the `ipv4`, `udp` and `tftp` layers (the `tftp` layer carries
`session`, the id of the transfer) and the DATA's octets in `payload`. `--transfers` prints a line per
transfer on stderr at the end; a machine reads the transfers from `FlowTracker` or `analyze`.
`--extract DIR` writes each transfer's file. The summary line on stderr counts the frames read, written
and skipped. Everything else pktcap's capture command takes is the same: `--output`, `--format`
(`pcap`, `pcapng`, `json`, `yaml`, `toml`, `ini`, `text`), `--per-record`, `--max-files`, `--datagrams`,
`--append`, `--count`, `--duration`, `--hook`, `--hook-fail-fast`, `--hook-timeout`, `--load` and
`--config`; pktcap's header documents each, and its statuses are the command's: 0 done, 1 a file or
an interface that cannot be opened, 2 a file that is no capture or a bad option.

| Before | Now |
| --- | --- |
| the positional `SOURCE` | `--input`/`-i FILE` (`-` for standard input); it excludes `--interface` |
| `--interface`/`-i` | `--interface` |
| `--filter` over events: `host`, `src=ADDR:PORT`, `dst`, `leg`, `direction`, `session` | pktcap's filter: `src`, `dst`, `host` (addresses and networks), `sport`, `dport`, `port`, plus `op`, `file`, `block`, `code`, `session`; `src=ADDR:PORT` is `src=ADDR and sport=PORT`; `leg` and `direction` are gone |
| `--json`, `--payload` | `--format json`: a DATA's octets are always in the record |
| `--transfers` on standard output | one line each on standard error |
| `warning: N of M frames not read`, status 2 for a capture of an unsupported link type | the count in the summary line, status 0 |
| (hidden, refused) | `--listen`: a socket on port 69 sees requests, never a transfer's data, and takes the port a server needs |

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
chosen per transfer, which no selector names. `follow_transfers(frames, tracker)` gives them one:
it feeds the dissected frames to a `FlowTracker` and returns each datagram the tracker attributes to a
transfer with its `TFTPLayer`, whose `session` is the transfer's id; a frame it cannot attribute is
returned as it was.

<!-- not run: it reads a capture file the reader recorded -->
```python
import pktcap
from tftp.capture import FlowTracker, TFTPLayer, follow_transfers, pktcap_plugin

registry = pktcap.DissectorRegistry()
pktcap_plugin(registry)                               # the layer, its filter keys and the dissector
frames = pktcap.read_dissected("boot.pcapng", dissector=pktcap.FrameDissector(registry))
for frame in follow_transfers(frames, FlowTracker()):
    layer = frame.layer(TFTPLayer)
    if layer is not None and layer.opcode == "DATA":
        print(layer.session, layer.block, len(frame.payload))
```

With the layer registered, `pktcap.compile_capture_filter("proto=tftp", pktcap.frame_filter_for(registry))`
selects the frames that have it and `pktcap.frame_record` writes its fields. `TFTPLayer.summary()` is the
line pktcap's `text` output prints for it.

pktcap's own commands load it by name: `PKTCAP_LOAD=tftp.capture pktcap convert -i boot.pcapng -f
"op=RRQ and file=*.efi"`. `tftp.capture.pktcap_plugin` adds the keys `op` (which refuses a name that
is none of the six opcodes), `file`, `block`, `code` and `session` (and `tftp.FIELD` for every field of
`TFTPLayer`). Over frames pktcap's own commands read, the layer is on the datagrams to or from the
request port only, so `block=` and `op=DATA` select what was sent there; `pytftp capture` follows the
transfer, and over `follow_transfers`' frames `op=DATA and tftp.session=c3` selects one transfer's data.

## Replay

`pytftp replay --input FILE --to HOST[:PORT]` asks a server you name for each transfer the capture holds again, each
by a client built from what the capture's client asked for (mode, file name, options) and paced
by the capture's own times. It is **not a replay of datagrams**: after the request a transfer runs
between ports chosen anew, so the request is repeated and the client answers the server as any
client does. **Nothing is sent to an address in the capture**: `--to` is the only destination (port 69
when it names none). The command is pktcap's replay command (`pktcap.cli.Replay`) with the plugin loaded
and its datagram sending replaced by this one; pktcap's `--source-port` and `--broadcast` are not
offered, since no captured datagram is sent.

```bash
pytftp replay --input boot.pcapng --to 192.0.2.1 --speed 10
pytftp replay --input boot.pcapng --to 192.0.2.1:6969 --json --limit 5
```

- A **read** is asked for again and its octets are discarded. A **write** is replayed only with
  `--writes`, and then it uploads the octets the capture holds of it, **which overwrites that file
  on the server**; a write the capture holds only partly is never replayed.
- `--speed` divides each recorded gap (default 1.0, the recorded pace), `--no-delay` removes the waits
  and `--max-delay` bounds any single wait, so a capture with a jump of a year does not hang a replay.
  `--limit` counts transfers. `--filter` chooses which frames reach the tracker.
- One line per transfer on stdout (`--json`: the transfer's result), and a count of what was
  replayed, failed and skipped on stderr. Status 0 when every transfer run succeeded, 1 when one
  failed or the file cannot be opened, 2 for a file that is no capture.
- The positionals `SOURCE` and `HOST` and `-p`/`--port` became `--input`/`-i` and `--to HOST[:PORT]`.

```python
from tftp.capture import replay_transfers

done = replay_transfers("boot.pcapng", "192.0.2.1", speed=10.0, max_delay=1.0)
print(len(done.results), "run,", done.skipped, "skipped")
```

## Filters

A filter is pktcap's: `key=value` clauses joined by `and`, a comma for "any of", `!=` to negate, and no
`or`; `pktcap.compile_capture_filter(text, pktcap.frame_filter_for(registry))` over frames, or
`--filter` of `pytftp capture`, `pytftp replay` and pktcap's commands with the plugin loaded. An
expression that does not parse, an unknown key or a bad value is a `pktcap.CaptureFilterError` (a
`ValueError`) naming the clause. This library has no filter of its own.

| key | matches |
| --- | --- |
| `op` | opcode name: RRQ WRQ DATA ACK ERROR OACK |
| `file` | requested filename, shell pattern |
| `block`, `code` | DATA/ACK block, ERROR code |
| `session` | the id of the transfer (`tftp.session=c3`), set by `follow_transfers` |
| `src`, `dst`, `host` | pktcap's: an address or a network |
| `sport`, `dport`, `port` | pktcap's: the UDP source, destination or either port |
| `proto`, `vlan`, `linktype`, `LAYER.FIELD` | pktcap's |
