# pytftp — contributor guide

Orientation for working on `tftp` from a checkout. It does not ship; the library's
own reference is the shipped headers below, which are inside the installed package
and must stay self-contained (no repo-relative links).

| Header | Covers |
| --- | --- |
| [`src/tftp/AGENTS.md`](src/tftp/AGENTS.md) | the top API header: every root name with its signature, the topic modules, the exceptions, the protocol coverage, the environment variables, the gotchas. It lists the per-package headers beside it (`src/tftp/*/AGENTS.md`), which hold the detail of each package |
| [`tests/AGENTS.md`](tests/AGENTS.md) | running and writing the tests: every test file, the fixtures, the expected skips, the traps |

A public API change updates its shipped header in the same commit.

## Layout

```
src/tftp/      the package, below
tests/         the suite; integration/ (real sockets and processes), conformance/ (what tftp-hpa answered),
               capture_cases/ and typing/ (see tests/AGENTS.md)
docs/          the published site, with mkdocs.yml; built strictly as a release gate
benchmarks/    run on demand, never in CI; results/ is tracked
examples/      runnable scripts
.github/       the workflows (test, release, docs, benchmarks)
pyproject.toml, README.md, CHANGELOG.md, RELEASENOTES.md, LICENSE
```

Under `src/tftp/`, one line per package:

- `__init__.py`, `__main__.py` — the root exports and `python -m tftp`.
- `client/` — `TFTPClient` and `AsyncTFTPClient` over a private shared base.
- `server/` — the two servers, the listening socket, one transfer's session, the handler contract.
- `relay/` — the transparent relay (blocking and asyncio over one core) and its routing.
- `options/` — negotiation: the handlers, the registry, the policy, the profiles.
- `backends/` — the handlers a server serves from, and the wrappers over them.
- `packet/` — the wire format: enums and the codec.
- `transfer/` — the I/O-free engine.
- `capture/` — packet events, trace hooks, flow tracking, analysis, replay and TFTP as a pktcap layer.
- `path/` — `TFTPPath` and `TFTPURIPath` for pathlib-next (the `path` extra).
- `cli/` — the `pytftp` command (the `cli` extra): one module per subcommand.
- `exceptions.py`, `listing.py`, `netascii.py` — the single-module topics.

The private modules at the root: `_uri.py` (`TFTPURL`, the URL one-shots), `_result.py`, `_arguments.py`
(constructor checks), `_bridge.py` (async streams to the engine), `_loggers.py`, `_sockets.py`, `_streams.py`, `_text.py`.

## Environment

Venvs are named `.venv/<version>-<os>-<arch>/`, one per interpreter this project is
tested against: the latest (3.14) and the floor (3.9), which is what CI's oldest job runs.
The suffix is what the interpreter was **built** for, not what the host is. `netimps` 0.4 and `pktcap` 0.1
are not on PyPI yet: install them first from their repositories, as CI does (`pip install --no-deps -e
../netimps -e ../pktcap` from sibling checkouts).

```bash
py -3.14 -m venv .venv/3.14-nt-arm64
py -3.9  -m venv .venv/3.9-nt-arm64
.venv/3.14-nt-arm64/Scripts/pip install -e ".[dev,docs]"
.venv/3.9-nt-arm64/Scripts/pip install -e ".[dev]"
```

On POSIX the scripts live in `bin/` rather than `Scripts/`, and the name is e.g.
`.venv/3.14-posix-x86_64`. The `dev` extra installs the `cli` and `path` extras, so the tests
that depend on them run rather than skip.

## Checks

CI runs on a `ci-*` tag (a throwaway tag) and on `workflow_dispatch`; the release workflow runs the same
gates on a `v*` tag: lint and type-check, the test matrix, the declared dependency floors, a bare-install
import, the built wheel with and without its extras on three systems, and a coverage report that gates nothing.

- `black --check src tests benchmarks examples`, `target-version = py39`, 110 columns. Run
  `black src tests benchmarks examples` before committing.
- `mypy --platform linux src/tftp`, then `--platform darwin`, then `--platform win32`. mypy
  checks every platform branch whatever host it runs on, but resolves names against the platform
  it is told to target: a clean run for one says nothing about the others, and a `# type: ignore`
  one platform needs is an `unused-ignore` error on another, so narrow with `sys.platform`. The
  checker targets 3.10 (mypy 2 refuses 3.9); the 3.9 floor is the test run on it.
- `mypy --no-incremental --config-file tests/typing/consumer.ini tests/typing/api.py`, the typing
  contract as a consumer sees it (`tests/AGENTS.md`, "The typing check").
- `pytest -q -rs` on both venvs; the commands, the files and the expected skips are in
  `tests/AGENTS.md`.
- `mkdocs build --strict`, a release gate, built by `docs.yml` on a docs-affecting push and after
  a release.

Benchmarks run by hand (`python benchmarks/run.py --save <name>`, or the `Benchmarks` workflow); the
JSON is committed, and a release's performance claims come from CI, not a laptop.

## Conventions

- **The engine does no I/O.** `transfer/` sees bytes in, bytes out and a caller-supplied clock;
  anything about sockets belongs in `client/` or `server/`. This is what lets `test_engine.py`
  simulate loss.
- **The hot path stays allocation-light**: preallocated receive buffers, `recvfrom_into`,
  `struct.pack_into` into the window ring, no logging call per packet, no `decode()` per DATA or ACK.
- **The server loop is single-threaded.** A handler's streams run on it; its `open_read` and
  `open_write` do too when it has `opens_fast = True`, else in a worker thread.
- **`netimps` and `pktcap` are imported lazily**, inside functions, so `import tftp` stays cheap and
  does not install the netimps socket patch until a transfer starts. `duho`, `pathlib_next` and
  `uritools` are imported only by `cli/` and `path/`; the library never requires an extra.
- **A private module imports a name from the module that owns it, never from the root.**
  `tests/test_import_structure.py` pins the direction, the 500-line module limit (the exceptions are named there, each with its reason) and the logger
  names; a public module is only one with no leading underscore.
- **A new public name** is exported from the module that owns its role, listed in `tests/surface.py`,
  described in that package's shipped header (a test fails otherwise) and noted in `CHANGELOG.md`; the
  root exports only what the common task needs. Python 3.9 floor: no `match`, no runtime `X | Y`.
- **A platform fact is measured, not reasoned**: the code that depends on one carries the measurement,
  and a `ci-*` run proves another platform. **Comments describe the code as it is**, in a line or three:
  the constraint, the reason, the unit; no history. **Line endings are LF** (`.gitattributes`).

## Releasing

This project follows [Semantic Versioning](https://semver.org/) and keeps a
[`CHANGELOG.md`](CHANGELOG.md) and a [`RELEASENOTES.md`](RELEASENOTES.md). Pre-1.0, MINOR means
"the documented API broke" and nothing else: additions and fixes are PATCH. The version in
`pyproject.toml` is the version in development. Pushing a tag matching `v*` triggers `release.yml`:
test gate and floors, then build (the tag must name the version built), the installed-wheel smoke
test, a strict docs build as a gate, the GitHub release and PyPI (Trusted Publishing, with
`skip-existing: true` so a run that fails partway can be re-run); its last job dispatches `docs.yml`,
which owns every Pages deploy. The project and `release.yml` must be registered on PyPI before the
first release.
