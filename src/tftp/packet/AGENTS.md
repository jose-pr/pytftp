# `tftp.packet` — public API header

Header-file-style reference for the `tftp.packet` package: the wire format, the
packet value types and the codec. Every public export with its signature,
arguments, contract and gotchas, so the package can be used without reading its
source. It ships inside the package and is self-contained; the top header is
`tftp/AGENTS.md`. Development documentation lives with the source at
<https://github.com/jose-pr/pytftp>.

## Wire format (`tftp.packet`)

`TFTPOpcode` (`RRQ`=1 … `OACK`=6) and `TFTPErrorCode` (`NOT_DEFINED`=0 …
`OPTION_REFUSED`=8) are `IntEnum`s; `TFTPErrorCode(n)` for any other `n` in
0..65535 is an unnamed member (`.name` is `CODE_<n>`) carrying the number, so
a peer's code is forwarded as it came, and `ValueError` outside the range.

```python
RequestPacket(opcode, filename, mode, options={}, raw=b"")
DataPacket(block, data)
AckPacket(block)
ErrorPacket(code, message)
OptionAckPacket(options)
RequestPacket.decode(data)
DataPacket.decode(data)
AckPacket.decode(data)
ErrorPacket.decode(data)
OptionAckPacket.decode(data)
```

The packet types are frozen, hashable values that equal only their own type
(a packet is not a tuple, and `AckPacket(5) != (5,)`): `RequestPacket(opcode,
filename, mode, options={}, raw=b"")`, `DataPacket(block, data)`,
`AckPacket(block)`, `ErrorPacket(code, message)` (`code` a `TFTPErrorCode`),
`OptionAckPacket(options)`. Each has a `decode(data)` classmethod (a datagram
of another kind raises `TFTPDecodeError`), `encode()` and `bytes(packet)`:
`bytes(AckPacket(5)) == b"\x00\x04\x00\x05"`. `options` is a read-only
mapping with lower-cased names and text values (an `int` value is stored as
its decimal text); a block is an `int` in 0..65535 (`ValueError`, or
`TypeError` for a non-`int`). A request has `.is_read`; its mode is
lower-cased; `raw` is the datagram as received and is not part of equality;
`.raw_options` lists every pair as sent (original case, order, duplicates) and
`.encode()` returns `raw` or a fresh encoding, so a relay forwards unknown
options untouched. `repr()` of a packet is a constructor call.

```python
decode(packet)
encode_request(opcode, filename, *, mode="octet", options=None)
encode_data(block, data)
encode_ack(block)
encode_error(code, message="")
encode_oack(options)
```

- **`decode`** — dispatches on the opcode; raises
  `TFTPDecodeError`. Liberal: tolerates a missing final NUL, drops a dangling
  option name, accepts any mode and any error code.
- **`encode_request`**,
  **`encode_data`**, **`encode_ack`**,
  **`encode_error`**, **`encode_oack`** — strict,
  and what the packet types' `encode()` call; they build the bytes without
  a packet object, for a caller on a hot path. `ValueError` for an empty file or option name, a NUL in a string, a mode
  other than `netascii`, `octet` or `mail`, a request over 512 octets (RFC
  2347), an OACK with no options, a block or error code outside 0..65535 and
  an error message over 512 octets; `TypeError` for a wrong type, an option
  value that is not text or an `int` (`None`, `True`, a float) included.
- Strings are UTF-8 with `surrogateescape`, so any byte sequence round-trips
  and real UTF-8 names decode naturally. An ERROR's message is the exception:
  it is read up to its first NUL with `replace`, so an octet that is not UTF-8
  shows as U+FFFD, in `decode` and in a transfer's `error` alike.

`FILENAME_ENCODING` is the codec name that rule uses (`"utf-8"` with the
`surrogateescape` handler), and `TFTPPacket` is the union of the five packet
types. The option-name constants (`STANDARD_OPTIONS` and the rest) are
`tftp.options`'.
