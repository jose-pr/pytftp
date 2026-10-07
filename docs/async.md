# asyncio

`AsyncTFTPClient` and `AsyncTFTPServer` run the same engine on an event loop, and
`AsyncTFTPRelay` relays between them (see [Relaying](relay.md)).

<!-- not run: the example streams into a sink the reader supplies -->
```python
import asyncio
from tftp import AsyncTFTPClient, AsyncTFTPServer

async def main():
    client = AsyncTFTPClient("192.0.2.1", windowsize=8)
    data = await client.get("pxelinux.0")
    await client.put("logs/boot.txt", b"ok\n")
    async for chunk in client.stream("images/large.img"):   # backpressure: a slow
        await sink.write(chunk)                              # consumer slows the server

asyncio.run(main())
```

A destination is a path or anything with `async write(data)` to download into;
a source is a path, `bytes` or anything with `async read(n)` to upload from.

## Async handlers

<!-- not run: db and storage are the reader's own -->
```python
class Images:
    async def open_read(self, context):
        record = await db.fetch_image(context.peer[0], context.filename)
        if record is None:
            raise tftp.TFTPError(tftp.TFTPErrorCode.FILE_NOT_FOUND)
        return await storage.open(record.path)          # an AsyncTFTPReader

    async def open_write(self, context, size):
        return await storage.create(context.filename)   # an AsyncTFTPWriter

async def serve():
    async with AsyncTFTPServer(Images(), port=69) as server:
        await server.serve_forever()
```

`async with` binds on entry and awaits `aclose()` on exit. `await server.start()`
serves in a background task and returns once listening; `shutdown()` (safe from any
thread) and `await server.wait_closed()` stop and wait; `await server.aclose()`
releases the socket and is final.

The hooks are coroutines and the streams are asynchronous: an
`AsyncTFTPReader` has `async read(size)` and `async close()`, an
`AsyncTFTPWriter` has `async write(data)` and `async close()`. An upload's final
ACK is sent only once the writer has taken every byte and `close` has returned.

A synchronous handler, such as the built-in backends, is given through the one
adapter, `tftp.server.ThreadedHandler`: the backends that open quickly
(`opens_fast`) open on the loop, the others (HTTP, upstream TFTP) in the loop's
executor. A synchronous handler given without it raises `TypeError` when the
server is built; a directory path needs no adapter.

```python
from tftp.backends import MemoryBackend
from tftp.server import ThreadedHandler

server = AsyncTFTPServer(ThreadedHandler(MemoryBackend({"hello": b"hi"})), port=69)
```

The server keeps replying from the request's own address on every loop: where
the loop has no `add_reader` (Windows' default Proactor loop), the listening
socket is read by a small thread.
