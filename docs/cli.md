# Command line

`pip install "tftp[cli]"` installs the `pytftp` command (`python -m tftp` is the same). Without the
extra the command prints one line naming it and exits 1. `pytftp --help` and `pytftp COMMAND --help`
show every option with its default, and `AGENT_HELP=1 pytftp --help` prints the whole command tree as
one JSON document.

| Command | What it does |
| --- | --- |
| `get` | download a file (RRQ) |
| `put` | upload a file (WRQ) |
| `ls` | list a directory, with pytftp's `x-list` extension |
| `serve` | serve a directory, an HTTP(S) base URL or another TFTP server |
| `relay` | forward requests to upstream servers, packets unchanged |
| `capture` | show the TFTP in a capture, rebuild its transfers, extract their files |
| `replay` | ask a server for each transfer a capture holds again |

`-v`, `-q` and `--loglevel [NAME:]LEVEL` adjust logging and go before or after the command.

## Exit status and output

- **0** success; **1** the transfer failed, the peer refused, the port could not be bound or a local
  file or name failed (one `error: ...` line on stderr, with the characters a terminal would interpret
  escaped, and no traceback); **2** the invocation was wrong (a bad value, a missing or unrecognised
  capture file, a bad filter, an argument that would be ignored). A reader that closes stdout
  (`pytftp capture f | head -1`) ends a printing command quietly with status 1.
- A result goes to stdout and diagnostics to stderr. `get` and `put` print one line (`received N bytes
  in S s (...)` or `sent ...`), on stderr when `get HOST FILE -` writes the file to stdout; `-q` drops
  it. `ls` prints `type size time name` per entry.
- `--json` prints one JSON object on one line instead, for every command and whatever `-q` says: a
  transfer's (the keys of `TransferResult.to_dict()`), an entry list's (`ls`: one array), a relayed
  transfer's (`relay`) or a packet's (`capture`). `get ... - --json` is a usage error: stdout holds
  the file.

## Client: `get`, `put`, `ls`

```bash
pytftp get 192.0.2.1 pxelinux.0                       # writes ./pxelinux.0
pytftp get 192.0.2.1 pxelinux.0 - > boot.bin          # to stdout
pytftp get "tftp://192.0.2.1/images/vmlinuz?blksize=1428&windowsize=16" vmlinuz
pytftp put 192.0.2.1 firmware.bin
pytftp put 192.0.2.1 - config.txt < config.txt        # from stdin
pytftp ls 192.0.2.1 boot/                              # a pytftp server with --listing
```

| Option | Meaning |
| --- | --- |
| `-p`, `--port` | the server's UDP port (default 69) |
| `-m`, `--mode` | `octet` or `netascii` (default: the URL's, else `octet`) |
| `-b`, `--blksize` | block size to request, 8 to 65464; `0` requests none, which is 512 (default 1428) |
| `-w`, `--windowsize` | RFC 7440 window; `0` requests none, which is 1 (default none) |
| `-t`, `--timeout` | seconds before a retransmission (default 1.0) |
| `-r`, `--retries` | retransmissions of one packet before giving up (default 5) |
| `--no-tsize` | do not request or announce the transfer size |
| `--no-options` | send a plain RFC 1350 request with no options at all |
| `--compat PROFILE` | `strict`, `default`, `pxe`, `hpa` or `legacy`: the profile's option settings, which replace `-b`, `-w`, `--no-tsize` and `--no-options` |
| `-4`, `-6` | IPv4 or IPv6 only |
| `--json`, `--trace`, `--pcap FILE` | one JSON object per result; every datagram on stderr; every datagram into a pcap file Wireshark opens |

A `tftp://` URL replaces HOST and the remote name, sets the mode with `;mode=netascii` and may carry
transfer options in either spelling (`?blksize=1428&windowsize=16` or `;blksize=1428;windowsize=16`;
quote the `?` and `&` for the shell). A flag wins over the URL's option of the same name, and
`--no-options` drops the URL's options too. `get` writes to the remote file's basename by default,
`put` from `-` needs a remote name, and a directory given to `put` as the file is refused.
`--trace` and `--pcap` create their file only once the command can run, so a command that fails first
leaves an existing file as it was.

## Server: `serve`

```bash
pytftp serve /srv/tftp                                  # read-only, port 69
pytftp serve /srv/tftp --port 6969 --write --json
pytftp serve /srv/tftp --write --max-upload 1000000 --max-duration 600
pytftp serve --http https://images.example.com/pxe/ --compat pxe
pytftp serve --upstream 10.0.0.20 --write
```

| Option | Meaning |
| --- | --- |
| `ROOT` | the directory to serve (default `.`); not used with `--http` or `--upstream` |
| `--http URL`, `--upstream HOST[:PORT]` | serve from an HTTP(S) base URL, or as a terminating proxy to another TFTP server |
| `-l`, `--listen ADDRESS`, `--interface NIC` | the address to listen on (default `::`, IPv6 and IPv4 where the host can), or one network adapter (its IPv4 address; `-l ::` for its IPv6 one) |
| `-p`, `--port` | the UDP port (default 69) |
| `-W`, `--write`, `--overwrite`, `--no-create` | accept uploads; let them replace a file; let them only replace |
| `--max-upload BYTES`, `--max-duration SECONDS` | refuse an upload announced above, or growing past, BYTES (ERROR 3; a directory only); end a transfer that runs longer. No limit by default |
| `--max-sessions N`, `--max-per-client N` | concurrent transfers, 500 by default and `0` for no limit (510 at most on Windows); and per client address |
| `-t`, `--timeout`, `-r`, `--retries` | the retransmission timer and count of each transfer |
| `--port-range LOW:HIGH` | take transfer ports from a range, for a firewall |
| `--compat PROFILE` | the negotiation profile; it replaces the four option flags below (`--listing` may still be given) |
| `--max-blksize N`, `--max-windowsize N`, `--allow OPTION`, `--refuse OPTION`, `--fit-mtu` | the negotiation policy: the largest values granted, extension options to accept (`blksize2`, `utimeout`, `rollover`, `cookie`, `mstfwindow`; repeatable), options never to acknowledge, a block size that fits the arrival interface's MTU |
| `--listing` | allow `x-list` and `x-mtime`, which `pytftp ls` and `TFTPPath.iterdir()` need |
| `--per-client`, `--per-client-only` | serve `ROOT/<client address>/` (IPv6 `:` written `-`) to a client that has one and `ROOT` to the rest. **This is not isolation**: a client with no directory reads every other client's. `--per-client-only` answers such a client ERROR 1 to everything |
| `--ignore-case`, `--remap REGEX=REPLACEMENT` | find names whatever their case (the exact name wins); rewrite requested names with the first matching rule (repeatable; any source) |
| `--json`, `--trace`, `--pcap FILE` | one JSON object per transfer; every datagram on stderr; every datagram into a pcap file |

`--per-client`, `--ignore-case` and `--remap` are `PerClient`, `CaseInsensitive` and `Remap` of
`tftp.backends`, and the first two serve a directory only. The server logs each transfer at INFO on
stderr and its final counters when stopped; Ctrl-C, Ctrl-Break and SIGTERM stop it, idle or not, with
status 0.

## Relay: `relay`

```bash
pytftp relay 10.0.0.20 --route-prefix windows/=wds.lan --trace
pytftp relay --route-subnet 10.1.0.0/16=10.1.0.5 --route-subnet 10.2.0.0/16=10.2.0.5
```

`UPSTREAM` is the default route for a request no route matches; without one an unmatched request is
refused. `--route-subnet CIDR=HOST[:PORT]` sends the clients in a network to a host (the longest prefix
wins) and `--route-prefix PREFIX=HOST[:PORT]` the files whose name starts with PREFIX; prefix routes are
tried before subnet routes. `--listen`, `--interface`, `--port`, `--port-range`, `--max-sessions` (0 for
no limit), `--idle-timeout` (seconds without traffic before a relayed transfer ends, 30 by default),
`--json`, `--trace` and `--pcap` work as for `serve`.

## Captures: `capture` and `replay`

Both are pktcap's commands with TFTP loaded, so they need the `pktcap` extra (`pip install
"tftp[pktcap]"`); without it they print `captures need the 'pktcap' extra: pip install
"tftp[pktcap]"` on stderr and end with status 1, as `--pcap` does on the other commands. The rest of
the command line works without it.

```bash
pytftp capture --input boot.pcapng --transfers
tcpdump -i eth0 -U -w - udp | pytftp capture --input - --filter "op=RRQ,ERROR"
sudo pytftp capture --interface eth0 --extract recovered/   # Linux, live
pytftp capture --input boot.pcapng --format json --filter "op=DATA and tftp.session=c3"
pytftp replay --input boot.pcapng --to 192.0.2.1 --speed 10
```

`capture` reads a pcap or pcapng file or a live pipe on stdin (`--input`/`-i`, `-` for stdin), or
(Linux) an interface (`--interface`); the two exclude each other. It writes what pktcap's capture
command writes (`--output`, `--format`, `--per-record`, `--max-files`, `--datagrams`, `--append`,
`--count`, `--duration`, `--hook`, `--hook-fail-fast`, `--hook-timeout`, `--load` and `--config`: pktcap's
header lists them), filtered to the TFTP packets of the transfers it follows, and by default as one
readable line a packet. `--format json` is pktcap's record, with the `tftp` layer, its `session` and a
DATA's octets. `--transfers` prints a line per transfer on stderr at the end, `--no-packets` writes no
packets, `--extract DIR` writes each transfer's file as `<session>-<name>` (`.partial` when incomplete),
`-p`/`--port` (repeatable) names the port a request is sent to (default 69) and `-f`/`--filter` selects
packets, e.g. `'op=RRQ,ERROR and host=10.0.0.0/8'`, with the keys `op`, `file`, `block`, `code` and
`session`, and pktcap's `src`, `dst`, `host`, `sport`, `dport` and `port`. A summary line on stderr counts
the frames read, written and skipped, and each kind of frame nothing here dissects. The status is
pktcap's: 0 done, 1 a file or an interface that cannot be opened, 2 a file that is no capture or a bad
filter or option.

`replay` asks `--to HOST[:PORT]` (port 69 when left out), the only address anything is sent to, for each
transfer the capture holds again: one line per transfer on stdout (`--json`: its
`TransferResult.to_dict()`) and a `replayed N transfers, F failed, S skipped` line on stderr. It asks for
reads only; `--writes` also uploads what the capture holds of each write, **which overwrites that file
on the server**. `--input`/`-i` is the capture, `--request-port` the capture's request port; `--speed`
divides the recorded gap between transfers, `--no-delay` removes the waits, `--max-delay` bounds one
wait, `--limit` ends the replay after that many transfers, `--filter`, `--load` and `--config` are
pktcap's, and `-t` and `-r` are the clients' timeout and retries. No datagram of the capture is sent,
so pktcap's `--source-port` and `--broadcast` are not offered. It exits 0 when every transfer run
succeeded (none run is 0), 1 when one failed, when `HOST` does not resolve or the file cannot be opened,
and 2 for a file that is no capture or a value out of range.
