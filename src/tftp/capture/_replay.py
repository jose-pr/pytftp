"""Asking again for the transfers a capture holds, from a server the caller names.

This is not a replay of datagrams: a transfer runs between ports chosen anew, so what is repeated
is each request, by a client that then answers the server as any client does. Importing this
module loads neither pktcap nor the client.
"""

from __future__ import annotations

import os
import time as _time
from typing import Any, BinaryIO, Dict, Iterable, List, NamedTuple, Optional, Tuple, Union

from .._result import TransferResult
from ..exceptions import TFTPError
from ..options._handler import Negotiated, read_decimal
from ._analysis import analyze
from ._flows import CapturedTransfer, DatagramLike

__all__ = ["ReplayedTransfers", "replay_transfers"]

_sleep = _time.sleep

#: Options the client has a setting for; every other one the capture's client asked for goes through as it was.
_OWN_OPTIONS = frozenset({"blksize", "windowsize", "tsize", "timeout", "utimeout", "rollover"})


class ReplayedTransfers(NamedTuple):
    """What :func:`replay_transfers` did: the transfers it ran, and how many it did not."""

    results: Tuple[TransferResult, ...]
    skipped: int


class _Step(NamedTuple):
    """A transfer with the ``time`` ``pktcap.replay_schedule`` reads."""

    time: float
    transfer: CapturedTransfer


class _Discard:
    """A sink that keeps nothing: a replayed read asks for the file again and has no use for it."""

    def write(self, data: Any) -> int:
        return len(data)


def _whole(transfer: CapturedTransfer) -> bool:
    """The capture holds every octet of this transfer's file and it ended well."""
    return transfer.is_complete and transfer.error is None and not transfer.missing_blocks


def _client_settings(requested: Dict[str, str], timeout: float) -> Dict[str, Any]:
    """The ``TFTPClient`` keywords that make the request the capture's client made."""
    rollover = read_decimal(requested.get("rollover", ""))
    return {
        "timeout": timeout,
        "blksize": read_decimal(requested.get("blksize", "")),
        "windowsize": read_decimal(requested.get("windowsize", "")),
        "tsize": "tsize" in requested,
        "rollover": rollover if rollover in (0, 1) else None,
        "timeout_option": "timeout" in requested,
        "utimeout": "utimeout" in requested,
        "extra_options": {k: v for k, v in requested.items() if k not in _OWN_OPTIONS},
    }


def _run(
    transfer: CapturedTransfer, host: Any, port: int, timeout: float, retries: int
) -> Optional[TransferResult]:
    """One transfer asked for again, or ``None`` when the client refuses what the capture held."""
    from ..client._sync import TFTPClient

    try:
        client = TFTPClient(host, port, retries=retries, **_client_settings(transfer.requested, timeout))
    except ValueError:
        return None
    started = _time.monotonic()
    try:
        if transfer.operation == "read":
            return client.download(transfer.filename, _Discard(), mode=transfer.mode)
        return client.upload(transfer.filename, transfer.data(), mode=transfer.mode)
    except TFTPError as exc:
        return TransferResult(
            transfer.filename,
            transfer.operation,
            transfer.mode,
            (host, port),
            (),
            0,
            0,
            0,
            _time.monotonic() - started,
            Negotiated(timeout=timeout),
            exc,
        )
    except ValueError:  # a mode or a name the client does not send
        return None


def replay_transfers(
    source: Union[str, "os.PathLike[str]", BinaryIO, Iterable[DatagramLike]],
    host: Any,
    port: int = 69,
    *,
    ports: Iterable[int] = (69,),
    writes: bool = False,
    speed: Optional[float] = 1.0,
    max_delay: float = 5.0,
    limit: Optional[int] = None,
    timeout: float = 1.0,
    retries: int = 5,
) -> ReplayedTransfers:
    """Ask the server at ``host`` and ``port`` for each transfer the capture ``source`` holds again.

    ``source`` is what :func:`analyze` takes; ``ports`` are its request ports. Transfers run one at
    a time in the order their requests were seen, each after the wait ``pktcap.replay_schedule``
    gives (``speed``, ``max_delay`` and ``limit`` mean what they mean there: ``speed=None`` removes
    the waits, ``max_delay`` is the longest single one, ``limit`` ends the replay after that many
    transfers and the rest are neither run nor counted). Each is requested by a client built from
    what the capture's client asked for: the mode, the options (``timeout`` is the argument's, not
    the captured value), and the file name.

    Nothing is sent to an address the capture holds: ``host`` and ``port`` are the only
    destination. **A read is asked for again and its octets are discarded; a write is replayed only
    with ``writes=True``, and it uploads the octets the capture holds, which overwrites a file on
    the server.** A write the capture holds only partly (a missing block, an ERROR, no final
    acknowledgement) is never replayed. A transfer that is not replayed, or that the client refuses
    (a mode it does not send, an option it does not accept), is counted in ``skipped``.

    A transfer that fails (an ERROR from the server, a timeout) is a :class:`~tftp.TransferResult`
    with ``error`` set and the replay goes on. ``ValueError`` for an argument out of range;
    ``pktcap.CaptureFormatError`` for a file that is not a capture; ``OSError`` when ``host`` does
    not resolve or a socket fails.
    """
    import pktcap

    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        raise ValueError("port is 1 to 65535, not %r" % (port,))
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not timeout > 0:
        raise ValueError("timeout must be positive, not %r" % (timeout,))
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
        raise ValueError("retries is 0 or more, not %r" % (retries,))
    pktcap.replay_schedule(
        [], speed=speed, max_delay=max_delay, limit=limit
    )  # the arguments, before a file is read

    transfers = analyze(source, ports=ports, keep_payloads=writes).transfers
    skipped = 0
    steps: List[_Step] = []
    for transfer in sorted(transfers, key=lambda t: t.started):
        if transfer.operation == "write" and not (writes and _whole(transfer)):
            skipped += 1
        else:
            steps.append(_Step(transfer.started, transfer))
    results: List[TransferResult] = []
    for delay, step in pktcap.replay_schedule(steps, speed=speed, max_delay=max_delay, limit=limit):
        if delay:
            _sleep(delay)
        result = _run(step.transfer, host, port, float(timeout), retries)
        if result is None:
            skipped += 1
        else:
            results.append(result)
    return ReplayedTransfers(tuple(results), skipped)
