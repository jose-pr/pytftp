"""TFTP as a pktcap plugin: the layer's filter keys, and the hook that registers them.

pktcap is not imported here: the hook is handed the registry, and the pktcap names below are
for the type checker only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Sequence

from ..packet._enums import TFTPOpcode
from ._dissector import TFTPLayer, register_tftp_dissector
from ._filters import _name_matches, _numbers, _opcode_names

if TYPE_CHECKING:
    import pktcap

__all__ = ["pktcap_plugin"]

LayerTest = Callable[[Any], bool]
_OPCODES = tuple(member.name for member in TFTPOpcode)


def _op(clause: "pktcap.FilterClause") -> LayerTest:
    names = _opcode_names(clause.values)
    unknown = sorted(names.difference(_OPCODES))
    if unknown:
        raise ValueError("op is one of %s, not %r" % (", ".join(_OPCODES), unknown[0]))
    return lambda layer: layer.opcode in names


def _file(clause: "pktcap.FilterClause") -> LayerTest:
    patterns: Sequence[str] = clause.values
    return lambda layer: layer.filename is not None and _name_matches(layer.filename, patterns)


def _number(field: str) -> Callable[["pktcap.FilterClause"], LayerTest]:
    def build(clause: "pktcap.FilterClause") -> LayerTest:
        numbers = _numbers(clause.key, clause.values)
        return lambda layer: getattr(layer, field) in numbers

    return build


def pktcap_plugin(registry: "pktcap.DissectorRegistry") -> None:
    """Declare :class:`TFTPLayer` and its filter keys in ``registry``, then register the dissector.

    The keys are ``op``, ``file``, ``block`` and ``code``, and ``TFTPLayer`` fields as ``tftp.FIELD``.
    A call that raises leaves ``registry`` as it found it: ``ValueError`` for a layer name or a
    port that is taken.
    """
    registry.register_layer(
        TFTPLayer, keys={"op": _op, "file": _file, "block": _number("block"), "code": _number("code")}
    )
    try:
        register_tftp_dissector(registry)
    except Exception:
        registry.unregister_layer("tftp")
        raise
