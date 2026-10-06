"""The public surface: exactly what each public module exports.

A name added, removed or renamed has to change its list in the same commit, so
the surface never moves by accident. Each list is sorted the way ``sorted``
sorts it.
"""

import importlib

import pytest

pytest.importorskip("pathlib_next")

EXPECTED = {
    "tftp": [
        "AccessViolation",
        "AckPacket",
        "AsyncTFTPClient",
        "AsyncTFTPServer",
        "AtomicWriter",
        "DEFAULT_BLKSIZE",
        "DEFAULT_REGISTRY",
        "DataPacket",
        "DiskFull",
        "EXTENSION_OPTIONS",
        "ErrorPacket",
        "FileAlreadyExists",
        "FileNotFound",
        "FilesystemBackend",
        "IllegalOperation",
        "LISTING_OPTIONS",
        "ListEntry",
        "MAX_BLKSIZE",
        "MAX_WINDOWSIZE",
        "MIN_BLKSIZE",
        "MODES",
        "Negotiated",
        "NetasciiReader",
        "NetasciiWriter",
        "NoSuchUser",
        "OptionAckPacket",
        "OptionHandler",
        "OptionNegotiationError",
        "OptionRegistry",
        "PROFILES",
        "PortRange",
        "Profile",
        "Receiver",
        "RemoteError",
        "RemoteStat",
        "RequestPacket",
        "STANDARD_OPTIONS",
        "SUPPORTED_OPTIONS",
        "Sender",
        "TFTPClient",
        "TFTPDecodeError",
        "TFTPError",
        "TFTPErrorCode",
        "TFTPHandler",
        "TFTPOpcode",
        "TFTPProtocolError",
        "TFTPRequestContext",
        "TFTPServer",
        "TFTPServerLimits",
        "TFTPServerOptions",
        "TFTPURL",
        "TFTPValueError",
        "TransferAbortedError",
        "TransferResult",
        "TransferTimeoutError",
        "UnknownTransferID",
        "WouldBlock",
        "__version__",
        "decode",
        "download",
        "download_url",
        "encode_ack",
        "encode_data",
        "encode_error",
        "encode_oack",
        "encode_request",
        "register_option",
        "upload",
        "upload_url",
    ],
    "tftp.client": [
        "AsyncTFTPClient",
        "MODES",
        "RemoteStat",
        "TFTPClient",
        "download",
        "upload",
    ],
    "tftp.server": [
        "AsyncTFTPServer",
        "AtomicWriter",
        "PortRange",
        "PortRangeLike",
        "TFTPHandler",
        "TFTPRequestContext",
        "TFTPServer",
        "TFTPServerLimits",
        "TFTPStats",
    ],
    "tftp.relay": [
        "RelaySummary",
        "RouteFunction",
        "RouteTable",
        "TFTPRelay",
        "Upstream",
        "by_interface",
        "by_prefix",
        "by_subnet",
    ],
    "tftp.capture": [
        "Analysis",
        "CaptureFilterError",
        "CaptureFormatError",
        "CapturedTransfer",
        "FILTER_KEYS",
        "FlowTracker",
        "FrameDecoder",
        "LINKTYPES",
        "PacketEvent",
        "PcapWriter",
        "UDPDatagram",
        "analyze",
        "compile_filter",
        "live_capture_supported",
        "new_session_id",
        "read_datagrams",
        "read_frames",
        "sniff",
        "summarize",
    ],
    "tftp.options": [
        "BUILTIN_OPTIONS",
        "Blksize2Option",
        "BlksizeOption",
        "ClientOptionContext",
        "CookieOption",
        "DEFAULT_BLKSIZE",
        "DEFAULT_REGISTRY",
        "EXTENSION_OPTIONS",
        "LISTING_OPTIONS",
        "MAX_BLKSIZE",
        "MAX_UTIMEOUT",
        "MAX_WINDOWSIZE",
        "MIN_BLKSIZE",
        "MIN_UTIMEOUT",
        "MstfwindowOption",
        "Negotiated",
        "OptionHandler",
        "OptionRegistry",
        "PROFILES",
        "Profile",
        "RolloverOption",
        "STANDARD_OPTIONS",
        "SUPPORTED_OPTIONS",
        "ServerOptionContext",
        "TFTPServerOptions",
        "TimeoutOption",
        "TsizeOption",
        "UtimeoutOption",
        "WindowsizeOption",
        "XListOption",
        "XMtimeOption",
        "accept_oack",
        "negotiate",
        "refuse",
        "register_option",
        "request_options",
    ],
    "tftp.packet": [
        "AckPacket",
        "DataPacket",
        "ErrorPacket",
        "FILENAME_ENCODING",
        "OptionAckPacket",
        "RequestPacket",
        "TFTPErrorCode",
        "TFTPOpcode",
        "TFTPPacket",
        "decode",
        "encode_ack",
        "encode_data",
        "encode_error",
        "encode_oack",
        "encode_request",
    ],
    "tftp.backends": [
        "FilesystemBackend",
        "HTTPBackend",
        "MemoryBackend",
        "Pipe",
        "UpstreamBackend",
        "normalize_name",
    ],
    "tftp.path": [
        "TFTPPath",
        "TFTPURIPath",
    ],
    "tftp.exceptions": [
        "AccessViolation",
        "CaptureFilterError",
        "CaptureFormatError",
        "DiskFull",
        "FileAlreadyExists",
        "FileNotFound",
        "IllegalOperation",
        "NoSuchUser",
        "OptionNegotiationError",
        "RemoteError",
        "TFTPDecodeError",
        "TFTPError",
        "TFTPProtocolError",
        "TFTPValueError",
        "TransferAbortedError",
        "TransferTimeoutError",
        "UnknownTransferID",
        "WouldBlock",
    ],
    "tftp.cli": [
        "CaptureCmd",
        "Get",
        "Ls",
        "Put",
        "Pytftp",
        "RelayCmd",
        "Serve",
        "run",
    ],
    "tftp.transfer": [
        "Receiver",
        "Sender",
        "Transfer",
        "as_readinto",
        "as_write",
    ],
    "tftp.uri": [
        "TFTPURL",
        "download_url",
        "upload_url",
    ],
    "tftp.listing": [
        "DirectoryListing",
        "LIST_OPTION",
        "ListEntry",
        "MTIME_OPTION",
        "dumps",
        "loads",
    ],
    "tftp.netascii": [
        "NetasciiReader",
        "NetasciiWriter",
        "decode",
        "encode",
        "encoded_size",
    ],
    "tftp.result": [
        "TransferResult",
    ],
}

#: Root names whose home is not a module of EXPECTED.
_NOT_REEXPORTED = {"__version__"}


@pytest.mark.parametrize("module", sorted(EXPECTED))
def test_all_is_the_pinned_surface(module):
    actual = importlib.import_module(module).__all__
    assert sorted(actual) == EXPECTED[module]
    assert len(set(actual)) == len(actual)


@pytest.mark.parametrize("module", sorted(EXPECTED))
def test_every_exported_name_exists(module):
    mod = importlib.import_module(module)
    missing = [name for name in mod.__all__ if not hasattr(mod, name)]
    assert missing == []


def test_every_root_name_is_the_object_its_home_module_exports():
    import tftp

    homes = {m: importlib.import_module(m) for m in EXPECTED if m != "tftp"}
    wrong = []
    for name in tftp.__all__:
        if name in _NOT_REEXPORTED:
            continue
        owners = [mod for mod in homes.values() if name in mod.__all__]
        if not any(getattr(mod, name) is getattr(tftp, name) for mod in owners):
            wrong.append(name)
    assert wrong == []


def test_the_old_bare_names_are_gone():
    import tftp
    import tftp.relay

    old = ("Client", "Server", "Relay", "Handler", "Request", "Data", "Ack", "Error", "Packet", "Route")
    old += ("DEFAULT", "STRICT", "PXE", "HPA", "LEGACY")
    assert [name for name in old if hasattr(tftp, name) or hasattr(tftp.relay, name)] == []


def test_the_presets_are_attributes_of_profile():
    import tftp

    for name in ("DEFAULT", "STRICT", "PXE", "HPA", "LEGACY"):
        assert tftp.PROFILES[name.lower()] is getattr(tftp.Profile, name)


#: How many positional parameters each callable accepts: its operands. Every
#: option after them is keyword-only.
POSITIONAL = {
    "tftp.TFTPServerOptions": 0,
    "tftp.TFTPServerLimits": 0,
    "tftp.Negotiated": 0,
    "tftp.encode_request": 2,
    "tftp.AtomicWriter": 1,
    "tftp.listing.DirectoryListing": 1,
    "tftp.OptionRegistry.register": 1,
    "tftp.TFTPRequestContext": 2,
    "tftp.TFTPServer": 1,
    "tftp.AsyncTFTPServer": 1,
    "tftp.relay.TFTPRelay": 1,
    "tftp.TFTPClient": 2,
    "tftp.AsyncTFTPClient": 2,
    "tftp.PortRange": 2,
    "tftp.relay.Upstream": 2,
}


def _resolve(dotted):
    parts = dotted.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:cut]))
        except ImportError:
            continue
        for part in parts[cut:]:
            obj = getattr(obj, part)
        return obj
    raise LookupError(dotted)


def _positional(obj):
    import inspect

    params = list(inspect.signature(obj).parameters.values())
    if params and params[0].name == "self":
        params = params[1:]
    return [p.name for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]


@pytest.mark.parametrize("name", sorted(POSITIONAL))
def test_a_callable_takes_only_its_operands_positionally(name):
    assert len(_positional(_resolve(name))) == POSITIONAL[name], _positional(_resolve(name))


def test_an_option_given_by_position_is_refused():
    import tftp

    with pytest.raises(TypeError):
        tftp.TFTPServerOptions(65464)
    with pytest.raises(TypeError):
        tftp.TFTPServerLimits(1024)
    with pytest.raises(TypeError):
        tftp.Negotiated(512)
    with pytest.raises(TypeError):
        tftp.encode_request(tftp.TFTPOpcode.RRQ, "f", "octet")
    with pytest.raises(TypeError):
        tftp.TFTPServer(".", "127.0.0.1")


# -- the twins ----------------------------------------------------------------------------------


def test_the_clients_are_siblings_exported_from_the_same_modules():
    import tftp
    import tftp.client

    assert tftp.client.AsyncTFTPClient is tftp.AsyncTFTPClient
    assert tftp.client.TFTPClient is tftp.TFTPClient
    assert not issubclass(tftp.AsyncTFTPClient, tftp.TFTPClient)
    assert not issubclass(tftp.TFTPClient, tftp.AsyncTFTPClient)
    assert not hasattr(tftp.AsyncTFTPClient, "path")


def test_a_coroutine_method_overrides_nothing():
    import inspect

    import tftp

    for name, member in vars(tftp.AsyncTFTPClient).items():
        if inspect.iscoroutinefunction(member):
            assert not hasattr(tftp.TFTPClient.__mro__[1], name), name


def test_no_client_method_silences_a_signature_mismatch():
    import pathlib

    import tftp.client

    package = pathlib.Path(tftp.client.__file__).parent
    for path in package.glob("*.py"):
        assert "type: ignore[override]" not in path.read_text(encoding="utf-8"), path.name
