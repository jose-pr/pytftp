"""``python -m tftp``: the ``pytftp`` command line (needs the ``cli`` extra)."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
