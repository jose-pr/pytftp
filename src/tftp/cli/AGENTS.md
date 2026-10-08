# `tftp.cli` — public API header

Header-file-style reference for the `tftp.cli` package: the `pytftp` command, its
subcommands, exit statuses and output formats. Every public export with its
signature, arguments, contract and gotchas, so it can be used without reading
its source. It ships inside the package and is self-contained; the top header is
`tftp/AGENTS.md`. Development documentation lives with the source at
<https://github.com/jose-pr/pytftp>.

## Command line (`tftp.cli`, `cli` extra)

`pip install tftp[cli]` adds duho. The `pytftp` console script is installed
either way; without the extra it prints one line naming the extra and exits 1.
`python -m tftp` is equivalent. `tftp.cli.main(argv=None) -> int` runs it and
returns the exit status (a usage error from the parser raises `SystemExit(2)`);
the command classes are not API.

```python
main(argv=None)
```

```
pytftp get HOST REMOTE [LOCAL|-]       [-p PORT] [-m octet|netascii] [-b BLKSIZE] [-w WINDOW]
pytftp get tftp://HOST[:PORT]/FILE [LOCAL|-]      [-t TIMEOUT] [-r RETRIES] [--no-tsize] [--no-options]
                                       [--compat PROFILE] [-4|-6] [--json] [--trace] [--pcap FILE]
pytftp put HOST LOCAL|- [REMOTE]       (same options; or put tftp://HOST/FILE LOCAL|-)
pytftp ls HOST [DIR] | tftp://HOST/DIR (same options) [--json]
pytftp serve [ROOT] [--http URL | --upstream HOST[:PORT]] [-l ADDRESS | --interface NIC] [-p PORT] [-W/--write]
             [--overwrite] [--no-create] [--max-upload BYTES] [--max-duration SECONDS]
             [--compat PROFILE | --max-blksize N --max-windowsize N --allow OPTION... --refuse OPTION... --fit-mtu]
             [--listing] [--max-sessions N] [--max-per-client N] [--port-range LOW:HIGH]
             [--per-client | --per-client-only] [--ignore-case] [--remap REGEX=REPLACEMENT]...
             [--json] [--trace] [--pcap FILE]
pytftp relay [UPSTREAM] [--route-subnet CIDR=HOST[:PORT]]... [--route-prefix PREFIX=HOST[:PORT]]...
             [-l ADDRESS | --interface NIC] [-p PORT] [--idle-timeout S] [--max-sessions N] [--port-range LOW:HIGH]
             [--json] [--trace] [--pcap FILE]
pytftp capture [-i FILE|- | --interface NIC] [-p PORT]... [-f FILTER] [--no-packets] [--transfers]
             [--extract DIR] [-o TARGET] [--format FMT] [--per-record] [--max-files N] [--datagrams] [--append]
             [--count N] [-d SECONDS] [--hook COMMAND] [--hook-fail-fast] [--hook-timeout S] [--load PLUGIN]...
             [-c FILE]
pytftp replay -i FILE|- --to HOST[:PORT] [--request-port PORT]... [--writes] [--speed X | --no-delay]
             [--max-delay S] [--limit N] [-f FILTER] [-t S] [-r N] [--json] [--load PLUGIN]... [-c FILE]
```

`-v`, `-q` and `--loglevel` go before or after the subcommand. `pytftp --help`
shows each option's default; `AGENT_HELP=1 pytftp --help` prints the whole
command tree as one JSON document. `PYTFTP_MCP` is not read: the command is not
served as a tool.

- **Exit status**: 0 success; 1 the transfer failed, the peer refused, the
  port could not be bound or a local file or name failed (one `error: ...`
  line on stderr, with the characters a terminal would interpret escaped, and
  no traceback); 2 the invocation was wrong (a bad value, a missing or
  unrecognised capture file, a bad filter, an argument that would be ignored).
  A reader that closes stdout (`pytftp capture -i f | head -1`) ends a printing
  command quietly with status 1. `capture` and `replay` end as pktcap's commands
  do: a file that cannot be opened, an interface that cannot be captured on and a
  destination that does not resolve are status 1 (not 2), a file that is no capture is 2.
- **Output**: a result goes to stdout and diagnostics to stderr. `get` and `put`
  print one line (`received N bytes in S s (...)` or `sent ...`); it is on
  stderr when `get HOST FILE -` writes the file to stdout, and `-q` drops it.
  `ls` prints `type size time name` per entry, which `-q` does not drop.
  `--json` prints one JSON object on one line instead, for every command and
  whatever `-q` says: a transfer's (`get`, `put`, `serve`; the keys of
  `TransferResult.to_dict()`), an entry list's (`ls`: one array), a relayed
  transfer's (`relay`: `RelaySummary.to_dict()`) or a packet's (`capture`;
  `{"transfer": ...}` with `--transfers`). `get ... - --json` is a usage error:
  stdout holds the file.
- **Refused, not ignored** (status 2): `-4` with `-6`; `-b`, `-w`, `--no-tsize` or
  `--no-options` beside `--compat` (the profile replaces them), and on `serve`
  `--max-blksize`, `--max-windowsize`, `--allow`, `--refuse` or `--fit-mtu`
  beside `--compat` (`--listing` may); a third argument after a `tftp://` URL; a
  directory given to `put` as the file.
- `-b 0` / `-w 0` request no `blksize` / `windowsize`; without a flag the
  URL's option is used, else 1428 and none. A flag wins over a URL's option
  of the same name (`-m`, `-b`, `-w`, `-t`), and `--no-options` drops the
  URL's options too.
  `--compat PROFILE` (`strict`, `default`, `pxe`, `hpa`, `legacy`) replaces the
  option flags with the profile's settings (client and server alike).
- `get` writes to the remote file's basename by default; `-` is stdout.
  `put` from `-` (stdin) needs a remote name. A `tftp://` URL replaces HOST and
  the remote name, sets the mode with `;mode=netascii` and may carry transfer
  options (`"tftp://h/f?blksize=1024&windowsize=8"`; quote the `?` and `&`
  for the shell).
- `--trace` prints every datagram on stderr; `--pcap FILE` writes them as a
  capture (Wireshark-readable). The file is created only once the command can
  run (arguments accepted, port bound): a command that fails first leaves an
  existing file as it was.
- `serve --http URL` is the HTTP gateway, `serve --upstream` the terminating
  proxy; `--write` enables uploads for both. `--allow` adds extension options
  to the standard four; `--refuse` removes any.
- `serve --max-upload BYTES` refuses an upload announced above it and one that
  grows past it (ERROR 3); `--max-duration SECONDS` ends a transfer that has run
  that long; both default to no limit, and `--max-upload` serves a directory
  only. `--max-sessions N` defaults to 500, 0 is unlimited (510 at most on
  Windows); `--max-per-client N` bounds one address.
- `--interface NIC` (serve, relay) listens on that adapter's IPv4 address
  (`-l ::` for its IPv6 one).
- `serve --listing` allows `x-list`/`x-mtime` (with `--compat` too), which
  `pytftp ls` and `TFTPPath.iterdir()` need. `--port-range` pins transfer
  ports. Directory serving only: `--per-client` serves `ROOT/<client
  address>/` when it exists (IPv6 `:` written `-`), else `ROOT` -- **not
  isolation**: a client with no directory of its own reads every other
  client's; `--per-client-only` (implies `--per-client`) answers such a client
  ERROR 1 to everything. `--ignore-case` finds names whatever their case (the
  exact name wins). `--remap REGEX=REPLACEMENT` (repeatable, split at the first
  `=`) rewrites requested names with the first matching rule, for any source.
  They are `PerClient`, `CaseInsensitive` and `Remap` of `tftp.backends`.
- `relay` needs an UPSTREAM (the default route) or routes; prefix routes are
  tried before subnet routes.
- `capture` and `replay` need the `pktcap` extra and are pktcap's commands, subclassed (its header
  `pktcap/cli/AGENTS.md` lists the options they inherit); without it either prints `captures need the
  'pktcap' extra: pip install "tftp[pktcap]"` and ends with status 1, as `--pcap` does on `get`, `put`,
  `serve` and `relay`, before a socket is bound. `--help` still lists them.
- `capture` reads a pcap/pcapng file or a live pipe on stdin (`tcpdump -i eth0 -U -w - udp |
  pytftp capture --input -`), or (Linux) an interface (`--interface`, which excludes `--input`). It writes
  the TFTP packets of the transfers it follows (pktcap's `proto=tftp`, ANDed with `--filter`) with
  pktcap's writer: by default one readable line a packet on stdout (`--format text`, the
  `TFTPLayer.summary()` after the addresses), `--format json` the record with the `tftp` layer, its
  `session` and a DATA's octets. `--transfers` prints a line per transfer on stderr at the end;
  `--no-packets` writes no packets; `--extract DIR` writes each transfer's file as
  `<session>-<name>` (`.partial` when incomplete). Ctrl-C ends a live capture with status 0 and still
  finishes the transfers. A summary line on stderr counts the frames read, written and skipped and
  each kind pktcap could not dissect, and `-q` drops it. A datagram the capture's snap length cut is
  followed (its transfer is incomplete) and not written. `--listen`, `--json` and `--payload` are not
  options of it.
- `replay` runs `replay_transfers` over a capture and asks `--to HOST[:PORT]` (port 69 when left out; the
  only address anything is sent to) for each transfer in it again: one line per transfer on stdout
  (`--json`: its `TransferResult.to_dict()`), and a `replayed N transfers, F failed, S skipped` line on
  stderr. It asks for reads only: `--writes` also uploads what the capture holds of each write, which
  overwrites that file on the server, and the help says so. `--request-port` is the capture's request
  port; `--source-port` and `--broadcast` are not options of it. Status 0 when every transfer run
  succeeded (none run is 0), 1 when one failed, `HOST` does not resolve or the file cannot be
  opened, 2 for a file that is no capture or a value out of range.
- `serve` and `relay` log each transfer at INFO on stderr (`-v`/`-q` adjust)
  and their final counters when stopped. Ctrl-C, Ctrl-Break and SIGTERM stop
  them, idle or not (the signal wakes the loop through its wake socket; there
  is no polling), with exit status 0 and the counters logged.

## Environment variables

The command reads these through its argument parser (`duho`) when it starts; the
library reads none of its own. `NETIMPS_SOCKET_PATCH` is the top header's.

- **`AGENT_HELP`** and **`AGENTS_HELP`** — either one true makes `--help` print
  the command tree as one JSON document instead of text. False: empty, `0`,
  `false`, `no`, `off`, `n` and `f`, in any case; any other value is true.
  Default: unset, text help.
- **`NO_COLOR`** — set to anything, an empty value included, it turns the colour of the
  help text off, and it wins over `FORCE_COLOR`.
- **`FORCE_COLOR`** — `1`, `true`, `yes`, `on`, `y` or `t`, in any case, turns the colour
  of the help text on even when stdout is not a terminal; any other value is read as
  unset. Default: colour only on a terminal.
- **`PYTFTP_MCP`** — not read: the commands are not served as tools.
