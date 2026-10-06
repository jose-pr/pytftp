"""A filesystem backend that finds files whatever the case of the requested name."""

from __future__ import annotations

import os

from ._filesystem import FilesystemBackend

__all__ = ["CaseInsensitive"]


class CaseInsensitive(FilesystemBackend):
    """A :class:`FilesystemBackend` that matches names regardless of case.

    The exact name wins when it exists; otherwise each component is looked
    up case-insensitively (firmware asking for ``\\Boot\\BCD`` finds
    ``boot/bcd``). An upload's final component keeps the requested case. The
    corrected name goes through the normal containment checks, so nothing a
    client names leaves ``root``. Takes the arguments of
    :class:`FilesystemBackend`.

    Looking a name up lists each directory on the way, which costs a request
    more than the exact lookup does.
    """

    def resolve(self, filename: str) -> str:
        path = super().resolve(filename)  # refuses .. and escapes first
        if os.path.exists(path):
            return path
        name = filename.replace("\\", "/") if self.backslash else filename
        parts = [part for part in name.split("/") if part not in ("", ".")]
        current, fixed = self.root, []
        for index, part in enumerate(parts):
            try:
                entries = os.listdir(current)
            except OSError:
                return path
            folded = part.casefold()
            match = part if part in entries else None
            if match is None:
                match = next((entry for entry in entries if entry.casefold() == folded), None)
            if match is None:
                if index < len(parts) - 1:
                    return path
                match = part
            fixed.append(match)
            current = os.path.join(current, match)
        return super().resolve("/".join(fixed))
