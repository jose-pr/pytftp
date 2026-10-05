"""The exception hierarchy: the bases each class has, and the one place every
class is defined.

A base that is wrong here is invisible to a caller until an ``except`` clause
they wrote against the builtin (or against the package base) stops matching.
"""

import ast
import builtins
import copy
import importlib
import pickle
from pathlib import Path

import pytest

import tftp
from tftp import exceptions
from tftp.exceptions import (
    AccessViolation,
    CaptureFilterError,
    CaptureFormatError,
    DiskFull,
    FileAlreadyExists,
    FileNotFound,
    IllegalOperation,
    NoSuchUser,
    OptionNegotiationError,
    RemoteError,
    TFTPDecodeError,
    TFTPError,
    TFTPProtocolError,
    TFTPValueError,
    TransferAbortedError,
    TransferTimeoutError,
    UnknownTransferID,
    WouldBlock,
)

#: Each class with the builtins a caller already catches for that failure.
_BUILTINS = [
    (TFTPError, ()),
    (TFTPProtocolError, ()),
    (RemoteError, ()),
    (FileNotFound, ()),
    (AccessViolation, ()),
    (DiskFull, ()),
    (IllegalOperation, ()),
    (UnknownTransferID, ()),
    (FileAlreadyExists, ()),
    (NoSuchUser, ()),
    (OptionNegotiationError, ()),
    (TransferTimeoutError, (TimeoutError,)),
    (TransferAbortedError, ()),
    (TFTPValueError, (ValueError,)),
    (TFTPDecodeError, (ValueError,)),
    (CaptureFormatError, (ValueError,)),
    (CaptureFilterError, (ValueError,)),
]
_IDS = [cls.__name__ for cls, _ in _BUILTINS]


@pytest.mark.parametrize("cls, builtins", _BUILTINS, ids=_IDS)
def test_every_exception_is_a_package_error_and_the_builtin_a_caller_catches(cls, builtins):
    """A dropped builtin co-parent silently breaks every caller's existing
    ``except``; a class outside the base escapes ``except TFTPError``."""
    assert issubclass(cls, TFTPError)
    for builtin in builtins:
        assert issubclass(cls, builtin)


def test_the_signal_is_the_one_exception_outside_the_base():
    """``WouldBlock`` says "nothing ready yet", which is not a failure."""
    assert issubclass(WouldBlock, BlockingIOError)
    assert not issubclass(WouldBlock, TFTPError)


def test_a_malformed_packet_is_a_decode_error_a_value_error_and_a_package_error():
    for catches in (TFTPDecodeError, TFTPValueError, ValueError, TFTPError):
        with pytest.raises(catches):
            tftp.decode(b"\x00")


def test_a_decode_error_carries_the_code_a_peer_is_answered_with():
    error = TFTPDecodeError("short")
    assert error.code == tftp.ErrorCode.ILLEGAL_OPERATION
    assert error.message == "short"


def test_a_timeout_is_caught_as_a_package_error_and_as_a_timeout_and_has_no_errno():
    """Deadline handling catches ``TimeoutError``; no OS call failed, so there
    is no errno to mistake for one."""
    error = TransferTimeoutError("slow")
    for catches in (TFTPError, TimeoutError, OSError):
        with pytest.raises(catches):
            raise error
    assert error.errno is None
    assert error.code == tftp.ErrorCode.NOT_DEFINED
    assert error.message == "slow"


@pytest.mark.parametrize("leaf", [FileNotFound, AccessViolation, FileAlreadyExists])
def test_the_leaves_describe_the_servers_file_not_a_local_one(leaf):
    assert not issubclass(leaf, OSError)


def test_a_malformed_url_is_a_value_error_and_a_package_error():
    for catches in (TFTPValueError, ValueError, TFTPError):
        with pytest.raises(catches):
            tftp.parse_url("http://host/file")


def test_every_exception_class_is_defined_in_one_module():
    """A class defined elsewhere is a second home for a name the root
    re-exports, and an import cycle waiting to happen."""
    src = Path(tftp.__file__).parent
    homes = set()
    for path in src.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or node.name.startswith("_"):
                continue  # a private marker beside the code that catches it
            bases = [base.id for base in node.bases if isinstance(base, ast.Name)]
            if any(
                isinstance(getattr(builtins, base, None), type)
                and issubclass(getattr(builtins, base), BaseException)
                or base in exceptions.__all__
                for base in bases
            ):
                homes.add(path.name)
    assert homes == {"exceptions.py"}


def test_the_old_module_is_gone():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("tftp.errors")


@pytest.mark.parametrize("cls, builtins", _BUILTINS, ids=_IDS)
def test_every_exception_is_exported_from_its_module(cls, builtins):
    assert cls.__name__ in exceptions.__all__
    assert getattr(exceptions, cls.__name__) is cls


@pytest.mark.parametrize(
    "cls", [cls for cls, _ in _BUILTINS if cls not in (CaptureFormatError, CaptureFilterError)] + [WouldBlock]
)
def test_the_root_exports_every_exception_but_the_capture_ones(cls):
    assert getattr(tftp, cls.__name__) is cls
    assert cls.__name__ in tftp.__all__


# --------------------------------------------------------------------------- #
# Copy and pickle: every class is rebuilt by type(*args).
# --------------------------------------------------------------------------- #
_INSTANCES = [
    TFTPError(2, "no"),
    TFTPError(900, "an unknown code"),
    TFTPProtocolError("bad oack", 8),
    TFTPProtocolError("bad"),
    RemoteError(3, "full"),
    RemoteError.from_code(77, "unnamed"),
    FileNotFound(message="gone"),
    AccessViolation(),
    DiskFull(),
    IllegalOperation(),
    UnknownTransferID(),
    FileAlreadyExists(),
    NoSuchUser(),
    OptionNegotiationError(message="refused"),
    TransferTimeoutError("slow"),
    TransferTimeoutError(),
    TransferAbortedError("stop"),
    TransferAbortedError(),
    TFTPValueError("not a URL"),
    TFTPDecodeError("short"),
    CaptureFormatError("truncated"),
    CaptureFilterError("bad key"),
]


def _state(error):
    return (type(error), error.args, error.code, error.message, str(error))


def test_the_instances_cover_every_class_but_the_signal():
    assert {type(error) for error in _INSTANCES} == {cls for cls, _ in _BUILTINS}


@pytest.mark.parametrize("error", _INSTANCES, ids=repr)
@pytest.mark.parametrize("how", [copy.copy, copy.deepcopy], ids=["copy", "deepcopy"])
def test_an_exception_copies(error, how):
    twin = how(error)
    assert twin is not error
    assert _state(twin) == _state(error)


@pytest.mark.parametrize("error", _INSTANCES, ids=repr)
@pytest.mark.parametrize("protocol", range(pickle.HIGHEST_PROTOCOL + 1))
def test_an_exception_pickles(error, protocol):
    twin = pickle.loads(pickle.dumps(error, protocol))
    assert _state(twin) == _state(error)


@pytest.mark.parametrize("error", _INSTANCES, ids=repr)
def test_a_transfer_result_holding_an_exception_copies_and_pickles(error):
    result = tftp.TransferResult(
        "f", "read", "octet", ("h", 1), ("l", 2), 0, 0, 0, 0.0, tftp.Negotiated(), error
    )
    for twin in (copy.copy(result), copy.deepcopy(result), pickle.loads(pickle.dumps(result))):
        assert _state(twin.error) == _state(error)


def test_a_timeout_survives_a_process_boundary_without_an_errno():
    twin = pickle.loads(pickle.dumps(TransferTimeoutError("slow")))
    assert isinstance(twin, TimeoutError)
    assert twin.errno is None
    assert twin.message == "slow"
