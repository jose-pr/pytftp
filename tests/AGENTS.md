# Tests

Everything about running and writing the tests of `tftp`. The root `AGENTS.md`
points here; the shipped API headers are not about tests.

## Running them

Run from the root of a checkout, on the newest interpreter and on the floor (3.9, so no
`match`, no unquoted `X | Y` unions at runtime, no `dataclass(slots=)`). The venvs are the ones
the root `AGENTS.md` describes under "Environment".

```bash
.venv/3.14-nt-arm64/Scripts/python -m pytest -q -rs
.venv/3.9-nt-arm64/Scripts/python  -m pytest -q -rs     # the floor: run it before pushing
```

On POSIX the scripts live in `bin/` rather than `Scripts/`. Under WSL the venvs live
outside the checkout (`~/venvs/pytftp-3.14` and `~/venvs/pytftp-3.9` here), and the checkout is read through `/mnt/c`:

```bash
wsl.exe -d <distro> -e bash -lc 'cd /mnt/c/<path to the checkout> && PYTHONPYCACHEPREFIX=$HOME/.pyc-pytftp ~/venvs/pytftp-3.14/bin/python -m pytest -q -rs -p no:cacheprovider'
```

`pyproject.toml` puts `src/` on the path, collects `tests/` and turns every warning into an
error with no exception: a leaked socket or file, an unawaited coroutine or a thread's exception
fails the test that left it. CI runs `pytest -q -rs`, so every skip prints its reason: a skip is
not a pass, and a rising skip count beside a falling pass count is a signal, not noise.

| Marker | Meaning |
| --- | --- |
| `slow` | the 70,000-block rollover transfers; `-m "not slow"` skips them |
| `interop` | talks to another TFTP implementation found on `PATH` (curl with TFTP, tftp-hpa, BusyBox, dnsmasq); each skip names the binary that is missing, and the peer servers also need root or passwordless `sudo -n` |
| `resolves_off_host` | the test of the conftest guard that refuses a name that would leave the host |

## What is here

| File | Covers |
| --- | --- |
| `conftest.py` | the suite-wide name guard, the served-directory and server fixtures, the fake peers, the lossy path, the event-loop parametrisation; see "Fixtures" |
| `surface.py` | the public surface as a table (not a test module): each public module and its names, which `test_surface.py`, `test_hints.py` and `test_shipped_headers.py` read |
| `test_surface.py` | exactly what each public module exports, the positional arguments of each callable, and the three texts that say where a name lives |
| `test_shipped_headers.py` | the shipped `AGENTS.md` headers: every public name is in the header of its module, every header is listed by the one above it, none is over its line limit, every signature a header prints is the live one |
| `test_import_structure.py` | import direction (no module takes a name from the root), the 500-line module limit, the logger names, the public module set |
| `test_import_asyncio.py` | importing the package and using its blocking half does not import `asyncio` |
| `test_hints.py` | every public annotation resolves with `typing.get_type_hints`, except the ones naming a netimps or pktcap type |
| `test_exceptions.py` | the exception hierarchy and the one place each class is defined |
| `test_guards.py` | the name guard refuses a host name that would leave the machine; the structure-guard modules exist |
| `test_readme.py` | the README's Python blocks and `pytftp` command lines run as written |
| `test_examples.py` | the docs' Python blocks and the example scripts run as written (a block that cannot run says why in a `<!-- not run: reason -->` comment), and the command-line page names every option each command's `--help` shows |
| `test_extras.py` | a capability whose dependency is absent names the extra to install, in a fresh interpreter |
| `test_packet.py` | the codec: wire vectors from the RFCs, round trips, the refusals |
| `test_uri.py` | `TFTPURL`: RFC 3617's grammar, both option spellings, the conversion and value contracts |
| `test_options.py` | negotiation: each option handler, the policy, the client side |
| `test_option_registry.py` | the registry, tftp-hpa extensions, profiles, MTU fitting, window bounds |
| `test_engine.py` | the transfer engine over a simulated lossy link with a virtual clock: no sockets, no real time |
| `test_netascii.py` | the netascii reader and writer, the line-ending cases at every block boundary |
| `test_listing.py` | the `x-list` / `x-mtime` extension: format, server, client, paths |
| `test_filesystem_backend.py` | `FilesystemBackend` and `AtomicWriter`: containment, space, how an upload lands |
| `test_backend_contract.py` | one script of requests run against every backend, judged from the store |
| `test_transfer_contract.py` | one set of scenarios for each server and client in every pairing, on each Windows loop type |
| `test_path.py` | `TFTPPath` and `TFTPURIPath` (the `path` extra) |
| `test_path_contract.py` | pathlib_next's own contract suite over the two path classes; the capability switches that are `False` are the skips below |
| `test_capture.py` | packet events, trace hooks, reading a capture through pktcap, flow reconstruction, filters |
| `test_capture_dissector.py` | the TFTP dissector: pktcap's contract, where it registers, the record it makes |
| `test_capture_output.py` | what `pytftp capture` prints and writes for the committed captures, octet for octet |
| `capture_cases/` | the committed captures and the output recorded for each (`expected/`); `capture_cases/build.py` writes them |
| `integration/` | real sockets and processes on loopback only; `tests/conftest.py` applies to both it and the top level |
| `integration/test_client_server.py` | the clients against the server over loopback |
| `integration/test_aio.py` | the asyncio client and server on a real loop |
| `integration/test_protocol.py` | protocol behaviour on the wire with hand-driven raw sockets |
| `integration/test_robustness.py` | duplicate OACKs, backoff, limits, stalls, broadcast, fuzzing |
| `integration/test_lifecycle.py` | bind, serve, shutdown, wait_closed, close on each server; no thread or socket left behind |
| `integration/test_handler_contracts.py` | the one hook contract of each server, every shape that moves data |
| `integration/test_lossy_path.py` | both clients and the server through a path that loses, repeats, delays and reorders datagrams by a fixed seed |
| `integration/test_refused_sends.py` | a send the host refuses ends that transfer, with the reason, on every server and client |
| `integration/test_backends.py` | the memory, HTTP and upstream backends and the pipe between threads |
| `integration/test_wrappers.py` | `Remap`, `PerClient` and `CaseInsensitive` over real transfers |
| `integration/test_relay.py` | the relay end to end |
| `integration/test_replay.py` | `replay_transfers` and `pytftp replay`, judged by what the server received |
| `integration/test_addresses.py` | hosts, networks and interfaces given as objects, not only strings |
| `integration/test_host_inputs.py` | what the library does with host, port and timeout arguments it is given |
| `integration/test_deploy.py` | transfer port ranges |
| `integration/test_cli.py` | the `pytftp` command, as a process and through its parser |
| `integration/test_live_capture.py` | `pytftp capture -i` on Linux with `CAP_NET_RAW` |
| `integration/test_interop.py` | curl's TFTP client against the server |
| `integration/test_interop_peers.py` | tftp-hpa, BusyBox and dnsmasq, wherever they are found |
| `integration/firmware_boot.py` | not collected: boots iPXE (and, not yet passing, UEFI PXE) in QEMU against the server; see "Scripts" |
| `integration/ctrlc_driver.py` | not collected: raises a console event at an idle `serve` or `relay` on Windows, on a console of its own |
| `integration/smoke_installed.py` | not collected: the installed distribution; see "Scripts" |
| `conformance/` | what tftp-hpa answered, replayed against this library |
| `conformance/test_conformance.py` | each case's recorded exchange played against this library's server |
| `conformance/exchange.py` | the client script the recorder and the replay share |
| `conformance/record.py` | not collected: the recorder; see "The conformance suite" |
| `conformance/deviations.json` | the cases where this library differs from tftp-hpa on purpose, which the README table repeats |
| `typing/api.py` | the static-typing contract; never executed, checked by mypy with `typing/consumer.ini` |
| `typing/consumer.ini` | the consumer-side mypy configuration `api.py` is checked with |

## Fixtures

`conftest.py` holds what more than one file uses; a helper used by one file stays in it.

- `root` is a served directory with files of awkward sizes (empty, 1, 511, 512, 513 octets, three
  blocks of 1428, 300,001); `make_server(...)` starts servers on a free port and closes each after
  the test; `served` is a writable one, with the blocks of the README and docs it runs; `spy_server`
  records every request it opens, as the server decoded it from the wire.
- `fake_server` answers from a script for what a real server never sends; `FakePeer` is the same for
  a client; `refusing(sock, limit=)` is a socket that refuses a datagram over `limit` octets;
  `rivals()` is a server socket and a second one, on `127.0.0.2`, that answers first.
- `lossy_path(server, **schedule)` is a UDP forwarder between a real client and a real server that
  loses, duplicates, delays and reorders by a seeded schedule printed in every failure message;
  `Link` and `neg` are the simulated link and the negotiated-options helper of the engine tests.
- `loop_factory` runs an asyncio test on both of Windows' loops (the default elsewhere);
  `run_async(coro, loop_factory)` bounds it by a timeout.
- `web` is a web server over a dictionary, for the HTTP backend; `link_local` is a link-local IPv6
  address of this host with its zone (skips without one); `symlink(link, target)` skips where the
  host does not let a test create one; `free_ports(count)` is described under "Traps".
- `wait_until(predicate, timeout)` polls for a state a server reaches after it has answered.
- An autouse guard fails, naming the test, any resolution of a name that is not an address or
  `localhost` (a name under `.invalid` is answered "not known" without asking anyone); it records
  the violation as well, so a library that swallows the error still fails. Mark a test
  `resolves_off_host` only to test the guard.

## Expected skips

`pytest -rs` names the reason of every skip; these are the counts at this commit. A host with more
of the peers installed skips less.

| Platform | Skipped | Why |
| --- | --- | --- |
| Windows, 3.14 | 34 | 16 peer tests (`in.tftpd` 5, BusyBox server 4 and client 3, dnsmasq 3, the tftp-hpa client 1: each reason names the missing binary, and a host that has the binary and passwordless `sudo` runs them), 10 capability switches of the path contract (TFTP has no append, rename or directory hierarchy, for `TFTPPath` and `TFTPURIPath`), 4 console control events driven through a driver, 1 no named pipes, 1 trailing dot (the file system drops it), 1 one file for both names, 1 no `AF_PACKET` |
| Windows, 3.9 | 64 | those 34 and 30 cases of `os.path.isreserved`, which needs Python 3.13 on Windows |
| WSL (Fedora, tftp-hpa 5.3, BusyBox, dnsmasq, curl, `sudo`), 3.14 and 3.9 | 50 each | no peer test skips; 30 `os.path.isreserved` (it skips on Linux whatever the version: the probe is the function, not the platform), 10 path-contract switches, 4 Windows console events, 2 Windows event-loop types, 1 Windows file names, 1 `select()` descriptor limit (Windows only), 1 Windows-only behaviour, 1 live capture without `CAP_NET_RAW` |

## Scripts that are not tests

- `integration/smoke_installed.py` runs outside pytest against an installed distribution (CI installs
  the built wheel with and without its extras): it checks the package comes from site-packages, that
  every header the top one lists is in it, that a loopback transfer works, and that `pytftp` starts or
  names the extra it needs. `python tests/integration/smoke_installed.py [--extras]`.
- `integration/firmware_boot.py` boots real firmware in QEMU against the server and needs Linux, root,
  `qemu-system-x86_64`, the iPXE ROMs, OVMF and dnsmasq: `sudo python tests/integration/firmware_boot.py ipxe`.
  Its `uefi` scenario does not pass yet.
- `integration/ctrlc_driver.py` is started by `test_cli.py` on a console of its own, because a Windows
  console event goes to every process on the console.
- `capture_cases/build.py` writes the captures (`python tests/capture_cases/build.py`) and records the
  output of `pytftp capture` for each (`--expected [NAME]`): a capture is data, with fixed times and
  loopback and documentation addresses only, and an expected output is re-recorded only when the
  command's output is meant to change.

## The conformance suite

`conformance/cases/<name>/case.json` says what a client asks and how it carries on; `golden.json` is
the exchange tftp-hpa 5.3's server had with that client, recorded on Linux by
`python tests/conformance/record.py` (`--check` re-records in memory and reports drift; names after it
record only those cases). It needs `in.tftpd` and root or passwordless `sudo`. The suite replays each
case against this library's server with no peer installed and compares results: the options answered
and their values, the error codes, the block numbers and the octets of each block, not message texts
or the ports a reply comes from. A golden is never edited by hand; a case is written by hand
(`case.json`); a difference from tftp-hpa is one entry of `deviations.json`, which the README's table
repeats and a test compares.

## A green suite proves nothing about another platform

A claim about another platform needs a measurement there: a `ci-*` tag runs the matrix, not a test
that passes here.
A test takes its ground truth from the wire or the platform (datagrams counted, the address the peer
observed), never from the code under test. A regression test is seen to fail against the broken
code, not only to pass against the fix: plant the defect, watch the test fail, restore.

## Traps

- **Two suites on one loopback collide.** WSL2 shares loopback ports with Windows, so never run the
  Windows and the WSL suites at the same time. A test that needs a block of ports asks
  `free_ports(count)`, which picks a random start in 20000..30000, below every system's ephemeral
  range (those hand out the next port to the next bind, which would be the server's own socket),
  and probes it; the ports are not held, so a collision is rare and not impossible.
- **The bytecode cache.** Run the same checkout from Windows and from WSL with one side on its own
  cache (`PYTHONPYCACHEPREFIX=...`): pytest otherwise reuses the other's assertion-rewrite caches and
  reports Windows paths on Linux.
- **`127.0.0.2`.** The tests that send to it (a reply from the address the request was sent to, a
  rival answering first) work on Linux and Windows without configuration and skip where only
  `127.0.0.1` is configured, as on macOS.
- **Time.** A test that depends on time leaves a margin a loaded shared runner cannot eat: a second,
  not a tenth. A 100 ms margin passed on four platforms and then failed on a macOS runner.
- **Wait for the state the test asserts.** A server counts a declined request or a finished transfer
  after it has sent the reply the client was waiting for, from another thread: poll with
  `wait_until` and assert on the same state, never on the reply.
- **A refusal run as a subprocess that does start would hang on Windows**, where killing the venv
  launcher leaves the real interpreter and its open pipes. The tests that must not block run a
  `serve` that is meant to refuse with `serve_forever` patched to raise (the `serve_refused` fixture of `integration/test_cli.py`).
- **A test patches where the code reads the name**, not where it is defined: the private packages
  re-export names for their siblings, and a patch on the re-export leaves the real binding alone.
  `test_import_structure.py` keeps a function-local import from hiding such a binding.
- **A Windows rename can be refused for a moment** ("access denied" while a virus scanner or an
  indexer holds the file just written): the library retries it, so a test that sees it on a download's
  temporary file is a library defect.

## The typing check

`typing/api.py` is the documented API used as a consumer uses it, with `assert_type` on the results
that matter, so a checker proves the promises the shipped annotations make. It is never executed. It
is checked with a **consumer's** mypy configuration, `typing/consumer.ini`, and with
`--no-incremental`: two runs on one configuration share `.mypy_cache`, and the second reads a
degraded package. So do not collapse the two invocations onto one configuration, and do not read an
error in `api.py` as a contract regression before re-running it from a cold cache.

```bash
.venv/3.14-nt-arm64/Scripts/python -m mypy --no-incremental --config-file tests/typing/consumer.ini tests/typing/api.py
```
