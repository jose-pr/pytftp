# Protocol coverage

| Spec | What |
| --- | --- |
| RFC 1350 | RRQ, WRQ, DATA, ACK, ERROR; `octet` and `netascii` modes (`mail` is refused) |
| RFC 1123 §4.2.3.1 | Sorcerer's Apprentice fix |
| RFC 1123 §4.2.3.2 | Exponential backoff of retransmissions |
| RFC 1123 §4.2.3.4 | Requests sent to broadcast or multicast addresses ignored |
| RFC 2347 | Option extension, OACK, ERROR 8 |
| RFC 2348 | `blksize`, 8 to 65464 |
| RFC 2349 | `timeout` (whole seconds) and `tsize` |
| RFC 7440 | `windowsize`, 1 to 65535 |
| tftp-hpa | `utimeout`, `rollover`, `blksize2`, `cookie` -- off unless a server allows them |
| Microsoft | `mstfwindow`, Windows bootmgr's variable window -- off unless allowed |
| pytftp | `x-list` and `x-mtime`: directory listings and modification times -- off unless allowed |
| RFC 3617 | `tftp://` URLs |

RFC 2090 (multicast) and PXE MTFTP are not implemented.

Tested against: iPXE (booting in QEMU), tftp-hpa 5.3 (server and client),
BusyBox 1.37 (server and client), dnsmasq 2.92, curl. What tftp-hpa 5.3's server
answered to 22 requests is recorded, and every test run replays it with no peer
installed; the [differences from tftp-hpa](https://github.com/jose-pr/pytftp#differences-from-tftp-hpa)
are listed in the README, one recorded case each.

## Negotiation

The client asks; the server answers with an OACK holding only what it
accepts. Unknown options and unusable values are ignored rather than
refused, as RFC 2347 requires, and the transfer runs on the default for
anything not acknowledged. The server clamps `blksize` and `windowsize` to
its `TFTPServerOptions` limits; the client refuses (ERROR 8) an OACK that grants
more than it asked for, an option it never requested, a `timeout` or `utimeout`
other than the one it asked for, or a number that is not ASCII digits.

A client whose request is answered with ERROR 8, 4 or 0 retries once with no
options (`fallback=True`), for servers that reject what they do not understand.

`tsize` is answered for any file whose size is known, except an empty one:
curl rejects `tsize 0` in an OACK, and the transfer shows the size anyway. A
netascii read request's `tsize` is left out: its size needs the whole file
read, which a server does not do before it answers (tftp-hpa does the same).

## Extensions and profiles

Options are handlers in a registry, so an application can add its own. A
server acknowledges the RFC options by default; tftp-hpa's extensions only
when allowed (`TFTPServerOptions(allowed=...)`), and any option can be refused
outright for firmware that asks for it and then mishandles it
(`refused={"windowsize"}`). Profiles bundle coherent settings: `pxe` (fit
`blksize` to the interface MTU so boot ROMs never see IP fragments), `hpa`,
`legacy`, `strict`.

### `mstfwindow`

Windows Boot Manager (Windows 8 and later) offers `mstfwindow=31416`; a WDS
server answers `27182` and both start with a window of 4 blocks. The client
can then resize the window through its ACKs, in a format Microsoft has not
published, so this library keeps the window at 4 (bytes after an ACK's block
number are ignored). Allow it with `--allow mstfwindow` or
`TFTPServerOptions(allowed=STANDARD_OPTIONS | {"mstfwindow"})`. Recent bootmgr
also negotiates the standard `windowsize`, which wins when both are
acknowledged.

### Listing and modification times

TFTP has no way to list a directory or learn a file's time. A pytftp server
that allows `LISTING_OPTIONS` (`pytftp serve --listing`) answers an RRQ for a
directory carrying `x-list=1` with a text listing, acknowledging `x-list` in
its OACK, and puts a file's modification time in the OACK when asked for
`x-mtime`. A server that does not know them ignores them (RFC 2347): a
directory then reads as "file not found". `TFTPClient.stat()`, `TFTPClient.listdir()`,
`pytftp ls` and `TFTPPath.iterdir()`/`walk()`/`glob()` build on this.

## Under loss

- **Retransmission** is driven only by timeouts on the sending side, backing
  off exponentially; a duplicate ACK never triggers a resend when
  `windowsize` is 1.
- **Repeated OACKs** (the server's answer when ACK 0 or DATA 1 was lost) are
  tolerated: a downloading client re-acknowledges, an uploading one waits for
  its own timeout.
- **Windows**: the receiver acknowledges every `windowsize` blocks. On a gap
  it acknowledges the last block it has in order, twice at most per gap (the
  first report may move the sender's window on; the second asks for the blocks
  from the hole), and once for a run of blocks it already has. An ACK that
  moves the window sends only the blocks that now fit; a duplicate ACK or a
  timeout resends the blocks in flight, and an ACK resends them at most once
  per two windows of progress, so one duplicated, lost or late ACK costs one
  window of DATA and not every window after it. The sender keeps the window
  in memory, so a resend never re-reads the source.
- **Stray packets**: anything from an address or port other than the peer's
  gets ERROR 5 and is otherwise ignored.
- **Dallying**: the server keeps a finished upload open for one timeout to
  re-acknowledge a repeated last block (the client can do the same with
  `dally=True`).
- **Rollover**: block numbers are tracked as unbounded integers and mapped to
  16 bits on the wire, so file size is unlimited. A receiver that did not
  negotiate `rollover` follows a sender that wraps to 1.

## Addresses

The server listens on `::` with a dual-stack socket by default. Each transfer
gets its own socket bound to the address the request was sent to, read from
pktinfo, so on a multi-homed host or a virtual IP the client hears back from
the address it used. A v4 client of the dual-stack listener is served from a
plain IPv4 socket.
