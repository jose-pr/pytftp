"""TFTP as a pktcap plugin: the layer's filter keys, and the hook that registers them.

pktcap is not imported here: the hook is handed the registry, and the pktcap names below are
for the type checker only.
"""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING, Any, Callable, Sequence, Set

from .._extras import require_pktcap
from ..packet._enums import TFTPOpcode
from ._dissector import TFTPLayer, register_tftp_dissector

if TYPE_CHECKING:
    import pktcap

__all__ = ["pktcap_plugin"]

LayerTest = Callable[[Any], bool]
_OPCODES = tuple(member.name for member in TFTPOpcode)


def _opcode_names(values: Sequence[str]) -> Set[str]:
    """The opcode names a clause asks for, upper-cased; the text is not checked."""
    return {v.upper() for v in values}


def _numbers(key: str, values: Sequence[str]) -> Set[int]:
    """The block or error numbers a clause asks for: ``int`` text, each 0 to 65535 (two octets)."""
    try:
        numbers = {int(v) for v in values}
    except ValueError as exc:
        raise ValueError("%s must be a number" % key) from exc
    for number in numbers:
        if not 0 <= number <= 65535:
            raise ValueError("%s is 0 to 65535, not %d" % (key, number))
    return numbers


def _name_matches(name: str, patterns: Sequence[str]) -> bool:
    """Whether the file name ``name`` fits any shell-style pattern, case-sensitively."""
    return any(fnmatch.fnmatchcase(name, v) for v in patterns)


def _op(clause: "pktcap.FilterClause") -> LayerTest:
    names = _opcode_names(clause.values)
    unknown = sorted(names.difference(_OPCODES))
    if unknown:
        raise ValueError("op is one of %s, not %r" % (", ".join(_OPCODES), unknown[0]))
    return lambda layer: layer.opcode in names


def _file(clause: "pktcap.FilterClause") -> LayerTest:
    patterns: Sequence[str] = clause.values
    return lambda layer: layer.filename is not None and _name_matches(layer.filename, patterns)


def _session(clause: "pktcap.FilterClause") -> LayerTest:
    wanted = set(clause.values)
    return lambda layer: layer.session in wanted


def _number(field: str) -> Callable[["pktcap.FilterClause"], LayerTest]:
    def build(clause: "pktcap.FilterClause") -> LayerTest:
        numbers = _numbers(clause.key, clause.values)
        return lambda layer: getattr(layer, field) in numbers

    return build


def pktcap_plugin(registry: "pktcap.DissectorRegistry") -> None:
    """Declare :class:`TFTPLayer` and its filter keys in ``registry``, then register the dissector.

    The keys are ``op``, ``file``, ``block``, ``code`` and ``session`` (the transfer's id, as
    :func:`follow_transfers` sets it, compared exactly), and ``TFTPLayer`` fields as ``tftp.FIELD``.
    A call that raises leaves ``registry`` as it found it: ``ValueError`` for a layer name or a
    port that is taken.
    """
    require_pktcap()
    registry.register_layer(
        TFTPLayer,
        keys={
            "op": _op,
            "file": _file,
            "block": _number("block"),
            "code": _number("code"),
            "session": _session,
        },
    )
    try:
        register_tftp_dissector(registry)
    except Exception:
        registry.unregister_layer("tftp")
        raise
