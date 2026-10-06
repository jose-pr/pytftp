# asyncio

`AsyncTFTPClient` and `AsyncTFTPServer` run the same engine on an event loop.

```python
import asyncio
from tftp import AsyncTFTPClient
from tftp.aio import AsyncTFTPServer

async def main():
    client = AsyncTFTPClient("192.0.2.1", windowsize=8)
    data = await client.get("pxelinux.0")
    await client.put("logs/boot.txt", b"ok\n")
    async for chunk in client.stream("images/large.img"):   # backpressure: a slow
        await sink.write(chunk)                              # consumer slows the server

asyncio.run(main())
```

Destinations and sources may be asynchronous: anything with `async
write(data)` (or `write` plus `async drain()`, such as
`asyncio.StreamWriter`) to download into, anything with `async read(n)` or an
async iterable of bytes to upload from.

## Async handlers

```python
class Images:
    async def open_read(self, context):
        record = await db.fetch_image(context.peer[0], context.filename)
        if record is None:
            raise tftp.TFTPError(tftp.TFTPErrorCode.FILE_NOT_FOUND)
        return await storage.open(record.path)          # an async reader

    async def open_write(self, context, size):
        return await storage.create(context.filename)   # an async writer

async def serve():
    async with AsyncTFTPServer(Images(), port=69) as server:
        await server.serve_forever()
```

Synchronous handlers work too: the built-in file and memory handlers open on
the loop, others (HTTP, upstream TFTP) in the loop's executor. An upload's
final ACK is sent only once an async writer has taken every byte.

The server keeps replying from the request's own address on every loop: where
the loop has no `add_reader` (Windows' default Proactor loop), the listening
socket is read by a small thread.
