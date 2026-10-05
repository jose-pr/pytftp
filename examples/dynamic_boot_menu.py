"""A handler that generates a per-client boot menu and serves files otherwise.

Requests for ``pxelinux.cfg/default`` get a menu naming the client's address;
everything else comes from the directory. Uploads are refused.

    python examples/dynamic_boot_menu.py /srv/tftp 6969
"""

import io
import sys

import tftp

MENU = """DEFAULT linux
LABEL linux
  KERNEL vmlinuz
  APPEND initrd=initrd.img ip=%(client)s
"""


class BootMenu(tftp.FileSystemHandler):
    def open_read(self, context):
        if context.filename.lstrip("/") == "pxelinux.cfg/default":
            body = (MENU % {"client": context.peer[0]}).encode()
            return io.BytesIO(body)  # BytesIO is seekable, so tsize works
        return super().open_read(context)

    def open_write(self, context, size):
        raise tftp.TFTPError(tftp.ErrorCode.ACCESS_VIOLATION, "read-only server")


root = sys.argv[1] if len(sys.argv) > 1 else "."
port = int(sys.argv[2]) if len(sys.argv) > 2 else 69
with tftp.Server(BootMenu(root), "::", port) as server:
    server.serve_forever()
