# pytftp — contributor guide

Repository orientation for working on `tftp` from a checkout. The library's
public API reference is [`src/tftp/AGENTS.md`](src/tftp/AGENTS.md), which ships
in the wheel and must stay self-contained (no repo-relative links). This file
does not ship.

## Layout

```
src/tftp/
├── __init__.py        # flat public surface (re-exports)
├── AGENTS.md          # shipped API header -- update it with any API change
├── packet/            # wire format: enums.py (TFTPOpcode, TFTPErrorCode), codec.py (types, encode/decode)
├── options/           # negotiation: base, builtin handlers, registry, policy, negotiate, profiles
├── netascii.py        # streaming netascii reader/writer
├── listing.py         # x-list directory listing format (DirectoryListing, parse/format)
├── uri.py             # tftp:// URLs (RFC 3617)
├── errors.py          # exceptions, OSError -> ERROR code mapping
├── result.py          # TransferResult
├── transfer/          # I/O-free engine: base.py, sender.py, receiver.py
├── client/            # _core.py (shared base), _sync.py (TFTPClient), _asyncio.py (AsyncTFTPClient)
├── _bridge.py         # async streams <-> engine (used by both asyncio drivers)
├── aio/               # server.py: AsyncTFTPServer
├── path/              # pathlib-next: local.py (TFTPPath), uri.py (TFTPURIPath), _stream.py
├── server/
│   ├── base.py        # ServerBase: config, admission, refusal, reporting (shared with aio)
│   ├── core.py        # TFTPServer: lifecycle, selectors loop, timers, completion
│   ├── listener.py    # listening socket: bind (host or interface), netimps UDPEndpoint, reply sockets
│   ├── session.py     # one transfer: PortRange, handler -> engine
│   ├── handler.py     # TFTPHandler protocol, TFTPRequestContext, AtomicWriter
│   ├── policy.py      # TFTPServerLimits
│   └── stats.py       # TFTPStats counters (server and relay)
├── backends/          # filesystem.py, memory.py, http.py (urllib gateway), proxy.py (upstream TFTP), pipe.py
├── relay/             # transparent relay: core.py (loop), session.py (per transfer), routing.py
├── capture/           # packet events, trace hooks, pcap decoding
├── cli/               # pytftp (duho): common.py, transfer.py (get/put/ls), serve.py, relay.py,
│                      # capture.py, handlers.py (serve's --per-client/--ignore-case/--remap)
├── _arguments.py      # constructor argument checks: TypeError for a type, ValueError for a value
└── _sockets.py        # window-sized socket buffers (everything else is netimps)
tests/                 # pytest; engine tests need no sockets
benchmarks/            # run.py, compare_tftpy.py + committed results/*.json
examples/              # runnable scripts
docs/                  # mkdocs site (hand-written index + mkdocstrings API pages)
```

Keep modules small enough to review in one sitting (a few hundred lines);
split by responsibility rather than letting one file grow.

## Design rules

- **The engine does no I/O.** `transfer/` sees bytes in, bytes out, and a
  caller-supplied clock. Anything about sockets belongs in `client.py` or
  `server/`. This is what lets `tests/test_engine.py` simulate loss.
- **The hot path stays allocation-light**: preallocated receive buffers,
  `recvfrom_into`, `struct.pack_into` into the window ring, no logging calls
  per packet, no `decode()` per DATA/ACK.
- **The server loop is single-threaded.** Handlers run on it. One heap entry
  per session; deadlines that move later are re-queued lazily.
- **netimps is imported lazily** (inside functions), so `import tftp` stays
  cheap and does not install the netimps socket patch until a transfer starts.
- Python 3.9 floor: no `match`, no runtime `X | Y`, no `dataclass(slots=)`.

## Environments and tests

Venvs live in `.venv/<version>-<os>-<arch>/` (gitignored). Develop on the
latest Python, and run the 3.9 floor before a release.

```bash
py -3.14 -m venv .venv/3.14-nt-arm64
.venv/3.14-nt-arm64/Scripts/pip install -e ".[dev]"
.venv/3.14-nt-arm64/Scripts/python -m pytest -q          # ~500 tests, under 30 s
.venv/3.14-nt-arm64/Scripts/python -m black src tests benchmarks examples
```

- `-m "not slow"` skips the 70,000-block rollover tests; `interop` tests need
  curl with TFTP on `PATH` and skip otherwise.
- The reply-from-request-address tests send to `127.0.0.2`, which Linux and
  Windows answer on without configuration; elsewhere they skip.
- Running the same checkout from Windows and from WSL: give one side its own
  bytecode cache (`PYTHONPYCACHEPREFIX=...`), or pytest reuses the other's
  assertion-rewrite caches and reports Windows paths on Linux.

## CI and release

Three workflows: `test.yml` (manual or `ci-*` tag; full matrix plus a job at
the declared dependency floors), `release.yml` (`v*` tag: tests → build →
strict docs gate → GitHub release → PyPI → docs dispatch) and `docs.yml`
(Pages). PyPI publishing uses Trusted Publishing (OIDC): the project and the
`release.yml` workflow must be registered on PyPI before the first release.

Benchmarks are run by hand (`python benchmarks/run.py --save <name>`), and the
JSON is committed; a release's performance claims come from CI, not a laptop.
