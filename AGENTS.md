# pytftp — contributor guide

Repository orientation for working on `tftp` from a checkout. The library's
public API reference is [`src/tftp/AGENTS.md`](src/tftp/AGENTS.md), which ships
in the wheel and must stay self-contained (no repo-relative links). This file
does not ship.

## Layout

```
src/tftp/
├── __init__.py        # the names the common task needs; every other name's home is its role module
├── AGENTS.md          # shipped API header -- update it with any API change
├── exceptions.py      # every exception, and the OSError -> ERROR code mapping
├── packet/            # wire format: _enums.py (TFTPOpcode, TFTPErrorCode), _codec.py (types, encode/decode)
├── options/           # negotiation: _handler, _builtin, _registry, _policy, _negotiate, _profiles
├── transfer/          # I/O-free engine: _engine.py (Transfer), _sender.py, _receiver.py
├── netascii.py        # streaming netascii reader/writer
├── listing.py         # x-list directory listing format (DirectoryListing, loads/dumps)
├── uri.py             # tftp:// URLs (RFC 3617)
├── result.py          # TransferResult
├── client/            # _core.py (shared base), _sync.py (TFTPClient), _asyncio.py (AsyncTFTPClient)
├── server/
│   ├── _core.py       # ServerBase: config, admission, refusal, reporting (shared by both servers)
│   ├── _sync.py       # SelectorService (the lifecycle the relay shares), TFTPServer: selectors loop, timers
│   ├── _asyncio.py    # AsyncTFTPServer
│   ├── _listener.py   # listening socket: bind (host or interface), netimps UDPEndpoint, reply sockets
│   ├── _session.py    # one transfer: PortRange, handler -> engine
│   ├── _handler.py    # TFTPHandler protocol, TFTPRequestContext, AtomicWriter
│   ├── _policy.py     # TFTPServerLimits
│   └── _stats.py      # TFTPStats counters (server and relay)
├── backends/          # _filesystem, _case, _per_client, _remap, _memory, _http (urllib gateway), _proxy (upstream TFTP), _pipe
├── relay/             # transparent relay: _core.py (loop), _session.py (per transfer), _routing.py
├── path/              # pathlib-next: _local.py (TFTPPath), _uri.py (TFTPURIPath), _stream.py
├── capture/           # _events, _filters, _flows, _hook, _text; pcap.py, frames.py, live.py leave for another package
├── cli/               # pytftp (duho): main() in __init__, _root.py (the root parser), _common.py,
│                      # _client.py, _content.py, _negotiation.py, _signals.py, one module per subcommand
├── _arguments.py      # constructor argument checks: TypeError for a type, ValueError for a value
├── _bridge.py         # async streams <-> engine (used by both asyncio drivers)
├── _loggers.py        # the loggers, named for their role (tftp.client, tftp.server, tftp.relay, tftp.backends)
├── _sockets.py        # window-sized socket buffers (everything else is netimps)
├── _streams.py        # the Protocols for the streams a transfer reads and writes
└── _text.py           # escaping what a peer wrote before it is printed
tests/                 # pytest; the top level: engine, codec, contract and guard tests, none needing a peer;
                       # integration/ real sockets and processes on loopback; conformance/ what tftp-hpa answered
                       # (cases, a recorder, a replay); typing/ the consumer-side type check
benchmarks/            # run.py, compare_tftpy.py + committed results/*.json
examples/              # runnable scripts
docs/                  # mkdocs site (hand-written index + mkdocstrings API pages)
```

A module is public only if its name has no leading underscore; `tests/test_import_structure.py`
lists the public ones, caps a module at 500 lines (the exceptions are named there with a
reason), and requires every internal import to name the module that defines the name.

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
.venv/3.14-nt-arm64/Scripts/python -m mypy --platform linux src/tftp    # then darwin, then win32
.venv/3.14-nt-arm64/Scripts/python -m mypy --no-incremental --config-file tests/typing/consumer.ini tests/typing/api.py
```

- mypy checks every platform branch whatever the host, but resolves names against the platform
  it is told to target (`--platform`): a clean run for one says nothing about the others, and a
  `# type: ignore` one platform needs is an `unused-ignore` error on another, so narrow with
  `sys.platform` instead. The checker targets 3.10 (mypy 2 refuses 3.9); the 3.9 floor is the
  test run on it, with `tests/test_hints.py`. `tests/typing/api.py` is the documented API used
  as a consumer would, never executed; it has its own configuration and no cache, because two
  runs on one configuration share `.mypy_cache` and the second reads a degraded package.

- `-m "not slow"` skips the 70,000-block rollover tests. `interop` tests run wherever the binary is
  found (curl with TFTP, tftp-hpa, BusyBox, dnsmasq) and each skip names the binary that is missing; the
  peer servers also need root or passwordless `sudo`. Run `pytest -rs` to see why a test skipped.
- `tests/conformance/record.py` re-records the goldens from a real tftp-hpa (`--check` reports drift): it
  needs `in.tftpd` and root or passwordless `sudo` on Linux. A golden is never edited; a case is hand-written
  (`case.json`) and a difference from tftp-hpa is a line of `deviations.json` that the README table repeats.
- Warnings are errors (`filterwarnings = ["error"]` in `pyproject.toml`, no exception): a leaked socket or
  file, an unawaited coroutine or a thread's exception fails the test that left it.
- A test that needs a block of ports asks `conftest.free_ports`, which picks a random block below the
  ephemeral ranges, so two suites on one host do not fight over a fixed block.
- The reply-from-request-address tests send to `127.0.0.2`, which Linux and
  Windows answer on without configuration; elsewhere they skip.
- Running the same checkout from Windows and from WSL: give one side its own
  bytecode cache (`PYTHONPYCACHEPREFIX=...`), or pytest reuses the other's
  assertion-rewrite caches and reports Windows paths on Linux.

## CI and release

Four workflows: `test.yml` (manual or `ci-*` tag; a lint and type-check job, the full matrix, a job at
the declared dependency floors, a bare-install import job, the built wheel installed with and without
its extras on the three systems, and a coverage report that gates nothing), `release.yml` (`v*` tag:
tests and floors → build → installed-wheel smoke test → strict docs gate → GitHub release → PyPI → docs
dispatch), `docs.yml` (Pages) and `benchmarks.yml` (dispatched by hand; one result file per system as an
artifact). PyPI publishing uses Trusted Publishing (OIDC): the project and the
`release.yml` workflow must be registered on PyPI before the first release.

Benchmarks are run by hand (`python benchmarks/run.py --save <name>`, or the `Benchmarks` workflow), and
the JSON is committed; a release's performance claims come from CI, not a laptop. The version in
`pyproject.toml` is the version in development.
