# Paths

With the `path` extra (`pip install tftp[path]`), TFTP files work with
[pathlib-next](https://github.com/jose-pr/pathlib-next):

```python
import tftp
from pathlib_next.uri import UriPath

client = tftp.TFTPClient("192.0.2.1", windowsize=8)
config = client.path("pxelinux.cfg", "default")
print(config.read_text())
config.with_name("default.bak").write_text(new_menu)

kernel = UriPath("tftp://192.0.2.1/images/vmlinuz")
print(kernel.stat().st_size)            # a size probe: nothing is transferred
kernel.copy("s3://boot-images/vmlinuz") # any pathlib-next scheme
```

TFTP moves whole files, so `open()` (read or write), `read_*`/`write_*`,
`stat()`, `exists()` and `copy()`/`move()` work against any server.

Against a pytftp server with listing allowed (`pytftp serve --listing`, or
`TFTPServerOptions(allowed=STANDARD_OPTIONS | LISTING_OPTIONS)`), directories
work too: `iterdir()`, `is_dir()`, `walk()`, `glob("**/*.cfg")`,
`copy(dest, recursive=True)`, and `stat().st_mtime`. Each directory costs one
listing transfer, whose entries already carry type, size and time. Other
servers report a directory as missing (`FileNotFoundError`).

Deleting, renaming and creating directories raise `NotImplementedError`.
Reads and writes stream through a bounded buffer, and errors arrive as the
usual `FileNotFoundError`, `PermissionError`, `FileExistsError`.

`TFTPURIPath` is registered with pathlib-next through an entry point:
`UriPath("tftp://...")` finds it without importing `tftp`. Tune the client
with `path.with_options(blksize=8192, windowsize=16)`.

## Options in a URL

A `tftp://` URL carries transfer options in either of two spellings.
Whichever of `?` and `;` comes first after the file name decides how the rest
is read: after `?`, `name=value` pairs separated by `&`; after `;`, `name=value`
pairs separated by `;`. These two URLs are equal:

```python
from tftp import TFTPURL

a = TFTPURL.parse("tftp://192.0.2.1/images/vmlinuz?blksize=1428&windowsize=16")
b = TFTPURL.parse("tftp://192.0.2.1/images/vmlinuz;blksize=1428;windowsize=16")
assert a == b and a.options == {"blksize": "1428", "windowsize": "16"}
print(a)  # tftp://192.0.2.1/images/vmlinuz;blksize=1428;windowsize=16
```

`str(url)` always writes the `;` spelling, `mode` first and only when it is
not `octet`, then the options in name order, so a URL with a mode and no
options is exactly RFC 3617's form. Port `0` means the default port, 69.

Options in a URL are this library's extension. RFC 3617 defines `;mode=` and
nothing else, curl looks for `;mode=` only, and another tool reads the text
after `?` as part of the file name: a URL with options is for this library's
own readers (`TFTPURL`, `download_url`, `upload_url`, `pytftp get|put|ls` and
`TFTPURIPath`).

| Name | Meaning | Value |
| --- | --- | --- |
| `mode` | the transfer mode | `octet` or `netascii` |
| `blksize` | `TFTPClient(blksize=)` | `mtu`, or digits within 8..65464 |
| `windowsize` | `TFTPClient(windowsize=)` | digits, 1..65535 |
| `timeout` | `TFTPClient(timeout=)` | seconds, above 0 |
| `tsize` | `TFTPClient(tsize=)` | `1`, `0`, `true` or `false` |
| `rollover` | `TFTPClient(rollover=)` | `0` or `1` |
| any other | requested verbatim (`extra_options`) | any text |

Names are compared without case. A value is percent-encoded text: write `;`,
`&`, `?`, `=`, `#` and `%` inside a value as `%3B`, `%26`, `%3F`, `%3D`, `%23`
and `%25`. The other spelling's delimiter inside a list is refused (a literal
`?` after `;`, or a literal `;` after `?`), as are a fragment, a repeated name,
an empty pair, a pair with no `=` and a value the name cannot read.

An explicit keyword wins over the URL's option of the same name:
`download_url(url, dst, blksize=512)`, `path.with_options(blksize=512)` and
`pytftp get URL -b 512` all request 512 whatever the URL says. Extra options
merge name by name the same way.
