"""The transfer engine: one side of a DATA/ACK exchange, without any I/O.

:class:`Sender` produces DATA and consumes ACKs; :class:`Receiver` consumes
DATA and produces ACKs. Neither touches a socket or a clock: packets go out
through a ``send`` callable, packets come in through :meth:`Transfer.handle`,
and the driver calls :meth:`Transfer.on_timeout` once ``deadline`` passes.
The client and the server drive the same two classes, and the tests drive
them over a simulated lossy link.

Behaviour worth knowing:

- **Windowing (RFC 7440).** A sender keeps a whole window of DATA in memory,
  so a retransmission never re-reads the source. An ACK inside the window
  restarts sending right after the acknowledged block.
- **Sorcerer's Apprentice (RFC 1123 4.2.3.1).** A duplicate ACK never
  triggers a retransmission when ``windowsize`` is 1; only a timeout does.
  With a larger window the first duplicate of a given ACK resends the window
  (it is how a receiver reports a lost block) and later duplicates are
  ignored.
- **Block number rollover.** Block numbers are tracked as unbounded integers
  and mapped to the 16-bit wire value, wrapping after 65535 to ``rollover``
  (0 by default, 1 when negotiated), so file size is unlimited.
"""

from __future__ import annotations

from .base import Transfer, WouldBlock, as_readinto, as_write
from .receiver import Receiver
from .sender import Sender

__all__ = ["Transfer", "Sender", "Receiver", "WouldBlock", "as_readinto", "as_write"]
