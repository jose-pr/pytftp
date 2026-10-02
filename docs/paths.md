# Paths

With the `path` extra (`pip install tftp[path]`), TFTP files work with
[pathlib-next](https://github.com/jose-pr/pathlib-next):

```python
import tftp
from pathlib_next.uri import UriPath

client = tftp.Client("192.0.2.1", windowsize=8)
config = client.path("pxelinux.cfg", "default")
print(config.read_text())
config.with_name("default.bak").write_text(new_menu)

kernel = UriPath("tftp://192.0.2.1/images/vmlinuz")
print(kernel.stat().st_size)            # a size probe: nothing is transferred
kernel.copy("s3://boot-images/vmlinuz") # any pathlib-next scheme
```

TFTP moves whole files and nothing else, so `open()` (read or write),
`read_*`/`write_*`, `stat()`, `exists()` and `copy()`/`move()` work, while
listing, deleting, renaming and directories raise `NotImplementedError`.
Reads and writes stream through a bounded buffer, and errors arrive as the
usual `FileNotFoundError`, `PermissionError`, `FileExistsError`.

`TftpUriPath` is registered with pathlib-next through an entry point:
`UriPath("tftp://...")` finds it without importing `tftp`. Tune the client
with `path.with_options(blksize=8192, windowsize=16)`.
