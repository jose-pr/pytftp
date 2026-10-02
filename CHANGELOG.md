# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `Client`: download and upload over IPv4 and IPv6, to and from paths, binary
  files or memory, with `blksize`, `windowsize`, `tsize`, `timeout`/`utimeout`
  and `rollover` negotiation, progress callbacks, and a one-time retry without
  options when a server refuses them (ERROR 8).
- `Server`: one event-loop thread serving any number of transfers, each on its
  own socket bound to the address the request was sent to (pktinfo, where
  the platform reports it); dual-stack `::` listening by default; per-server option
  policy (`ServerOptions`); session limit; dallying on the last ACK of an
  upload; `on_complete` reporting for every transfer.
- `FileSystemHandler`: serves a directory with containment against `..`,
  symlinks and Windows device names; backslash separators; atomic uploads;
  `create`/`overwrite` policy; `max_upload` and free-space checks from `tsize`.
- Handler protocol (`open_read`/`open_write`) for generated content.
- Transfer engine (`Sender`/`Receiver`) with RFC 7440 windowing, the
  Sorcerer's Apprentice fix, unknown-TID handling and unlimited file size via
  block-number rollover, usable without sockets.
- Streaming netascii in both directions.
- Packet encode/decode for every RFC 1350 and RFC 2347 packet.
- `pytftp` command (`cli` extra): `get`, `put`, `serve`, with `--json`.
- Loopback throughput benchmark (`benchmarks/run.py`).

[Unreleased]: https://github.com/jose-pr/pytftp/commits/main
