# Capture and debugging

## Tracing your own traffic

`TFTPClient`, `TFTPServer` and `TFTPRelay` take `trace=`, called with a
`tftp.capture.PacketEvent` for every datagram sent and received:

```python
import tftp
from tftp.capture import PcapWriter

def show(event):
    print(str(event))          # 12:00:00.123 [s3] 10.0.0.5:2000 > 10.0.0.1:69 RRQ 'pxelinux.0' octet blksize=1432 tsize=0

tftp.TFTPClient("192.0.2.1", trace=show).get("pxelinux.0")

with PcapWriter("server.pcap") as pcap:                  # open it in Wireshark
    tftp.TFTPServer("/srv/tftp", trace=pcap).serve_forever()
```

`trace=` takes one hook: `tftp.capture.combine_hooks(show, pcap)` joins several
(a hook that raises does not keep the others from the event).

Events carry a `session` id per transfer, the direction, both addresses, the
raw datagram, and decoded fields (`opcode_name`, `block`, `summary`,
`to_dict()`). From the command line, `--trace` prints them and `--pcap FILE`
records them, on `get`, `put`, `serve` and `relay`.

## Reading captures

pcap and pcapng files, from tcpdump, dumpcap or Wireshark, or a live pipe:

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
block-number rollover; netascii decoded). Ethernet with VLAN tags, Linux
cooked captures, raw IP and loopback captures are understood, and IP fragments
-- which large blocks produce -- are reassembled.

```bash
pytftp capture boot.pcapng --transfers
tcpdump -i eth0 -U -w - udp | pytftp capture - --filter "host=10.1.0.0/16 and op=ERROR"
sudo pytftp capture -i eth0 --extract recovered/            # Linux, live
```

## Filters

`key=value` clauses joined by `and`; a comma means "any of"; `!=` negates.

| key | matches |
| --- | --- |
| `op` | opcode name: RRQ WRQ DATA ACK ERROR OACK |
| `host`, `src`, `dst` | an address, a CIDR, `addr:port`, `[v6]:port` or `:port` |
| `port` | either end's port |
| `file` | requested filename, shell pattern |
| `block`, `code` | DATA/ACK block, ERROR code |
| `session`, `leg`, `direction` | transfer id, relay side, in/out/seen |
