"""Text that came from the wire, made safe to print."""

from __future__ import annotations

__all__ = ["escape", "Escaped"]


def escape(text: str) -> str:
    """``text`` with every character that is not printable written as a Python escape.

    A terminal interprets ESC and the other control characters (and bidirectional
    overrides), and a log line ends at a newline: text a peer chose, such as a
    mode, an option or an ERROR message, is passed through here before it is
    printed or logged. A character ``repr`` shows as itself is left alone; an
    octet that was not UTF-8 (a lone surrogate) is written as its escape.
    """
    if text.isprintable():
        return text
    return "".join(char if char.isprintable() else _escaped(char) for char in text)


class Escaped:
    """``escape(str(value))``, worked out only when a log record is formatted.

    A log call that passes ``Escaped(error)`` costs nothing when the record is
    dropped, and a value whose ``str()`` fails fails inside ``logging``, which
    reports it and goes on, as it does for any argument.
    """

    __slots__ = ("value",)

    def __init__(self, value: object) -> None:
        self.value = value

    def __str__(self) -> str:
        return escape(str(self.value))


def _escaped(char: str) -> str:
    number = ord(char)
    if number < 0x100:
        return "\\x%02x" % number
    if number < 0x10000:
        return "\\u%04x" % number
    return "\\U%08x" % number
