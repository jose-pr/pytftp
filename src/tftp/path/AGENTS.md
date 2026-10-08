# `tftp.path` — public API header

Header-file-style reference for the `tftp.path` package: a TFTP server as a
`pathlib_next` path. Every public export with its signature, arguments, contract
and gotchas, so the package can be used without reading its source. It ships
inside the package and is self-contained; the top header is `tftp/AGENTS.md`.
Development documentation lives with the source at
<https://github.com/jose-pr/pytftp>.

## Paths (`tftp.path`)

`pip install "tftp[path]"` adds `pathlib-next[uri]`. Importing `tftp` never
loads it.

```python
TFTPPath(*segments, client=None, mode="octet")
TFTPPath.with_client(client)
TFTPPath.with_mode(mode)
TFTPURIPath(*args, schemesmap=None, findclass=False, **kwargs)
TFTPURIPath.with_options(**client_options)
TFTPURIPath.with_client(client)
```

**`TFTPPath`** — a `pathlib_next.Path`
bound to a `TFTPClient` (`client.path("boot", "x")`); `mode` is checked when
the path is built (`ValueError` for anything but `octet` and `netascii`,
`TypeError` for a value that is not text). The client's own `on_negotiated`
hook fires for the path's reads and writes. Joined like a
`PurePosixPath` (`\` counts as `/`); the path text is the filename sent,
so `/boot/x` and `boot/x` stay distinct. Joining onto a `TFTPPath` keeps its
client; `with_client()`, `with_mode()`; `client`, `transfer_mode`. Equality
and hashing include the server (host, port). `as_uri()` is the `tftp://`
URL, the path text unchanged (`/boot/x` is `tftp://h//boot/x`). `relative_to()` works on the path text. A path is synchronous: binding
it to an `AsyncTFTPClient` (`TFTPPath(..., client=)`, `with_client()`) raises
`TypeError`, and `AsyncTFTPClient` has no `path()`.

**`TFTPURIPath`** — the `tftp://host[:port]/path[;mode=netascii]` scheme
for `pathlib_next.uri.UriPath`, registered through the
`pathlib_next.schemes` entry point, so `UriPath("tftp://...")` returns one
without importing `tftp`. `filename`, `transfer_mode`;
`with_options(**client_options)` / `with_client(client)` choose the
`TFTPClient` (default: the URI's host and port, library defaults). The URI host
may be an address object; `TFTPClient` accepts one. The URI's transfer options
(`;name=value` on the last segment, or a `?name=value&name=value` query; the
`TFTPURL` grammar) apply to its operations: `with_options` keywords win over
them, a client given to `with_client` is used as it is, and text that grammar
refuses raises `TFTPValueError` from the operation. The path is decoded before
it is read, so a `;` in a file name cannot be told from a parameter: write such
a value in the query.

Both support what TFTP can do, plus listing against a server speaking
`x-list`:

- `open("r")` streams a download; `open("w")` streams an upload that
  completes (and raises the server's error) on `close()`; `open("x")`
  first probes the size and raises `FileExistsError` if the file exists
  (not atomic). `a` and `+` modes raise `NotImplementedError`. `open()`
  returns only once the server has answered, so `FileNotFoundError`/
  `PermissionError` surface there. At most 1 MiB is buffered; an abandoned
  read sends the server an ERROR. A `with` block over `open("wb")` that
  raises abandons the upload the same way: the server discards it and keeps
  the file it had (a failed `copy()` therefore leaves its target as it was);
  one that ends normally commits.
- `read_bytes`, `read_text`, `write_bytes`, `write_text` (text newline
  handling is pathlib's), `copy()` to and from any pathlib_next path, and
  `move()` to one: `move()` from a TFTP path copies and then raises
  `NotImplementedError`, because the source cannot be deleted.
- `stat()` is one `TFTPClient.stat()` probe (`FileStat`: `st_size` and
  `st_mtime` 0 when unknown, directory mode for a listed directory);
  `exists()`, `is_file()`, `is_dir()`.
- `iterdir()`, `walk()`, `glob()`/`rglob()` and `copy(..., recursive=True)`
  need a server allowing `x-list` (one listing per directory, with each
  entry's type, size and time, so no per-entry probe). Against other servers
  a directory raises `FileNotFoundError`; listing a file raises
  `NotADirectoryError`.
- Deleting, renaming, creating directories and permissions raise
  `NotImplementedError`. `unlink()` raises it too, except
  `unlink(missing_ok=True)`, which does nothing, because the write that
  follows replaces the file: it is what `copy(overwrite=True)` asks of its
  target. So `copy(target, overwrite=True)` onto a name the server has
  replaces it (the server must allow overwriting).
- `tftp.path.TFTPURIPath` without `uritools` raises `ImportError` naming the
  `path` extra.
- TFTP errors become pathlib's: `FileNotFoundError`, `PermissionError`,
  `FileExistsError`, `OSError(ENOSPC)`, `TimeoutError`, else `OSError(EIO)`.
