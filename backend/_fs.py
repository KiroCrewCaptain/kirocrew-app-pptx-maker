"""The atomic file write PPTX Maker took from ``kiro_crew.atomic_write``.

Restated in the narrow form this app uses (text or bytes, optional mode,
``newline`` control). PORTING.md lists what it drops.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_WINDOWS = sys.platform == "win32"
#: Windows refuses to replace a file another process holds open without
#: ``FILE_SHARE_DELETE`` (an editor, a virus scanner); the hold is brief.
_REPLACE_RETRIES = 5
_REPLACE_RETRY_SECS = 0.05


def _replace(src: str, dst: Path) -> None:
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if not _WINDOWS or attempt == _REPLACE_RETRIES - 1:
                raise
            time.sleep(_REPLACE_RETRY_SECS)


def atomic_write(
    path: Path | str,
    content: str | bytes,
    *,
    mode: int | None = None,
    newline: str | None = None,
) -> None:
    """Write *content* to *path* through a unique temp file and a rename.

    A plain ``write_text`` truncates the old file before the new bytes land, so
    an interruption mid-save leaves an empty or partial file. The temp file is
    created beside the target (same filesystem, so the rename is atomic) with
    ``mkstemp``'s ``0600`` mode; *mode* replaces it before the rename, so the file
    is never visible at its final name with the wrong bits. Removed on any
    failure.
    """
    target = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    try:
        if isinstance(content, bytes):
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                if mode is not None and not _WINDOWS:
                    os.fchmod(handle.fileno(), mode)
        else:
            with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as handle:
                handle.write(content)
                handle.flush()
                if mode is not None and not _WINDOWS:
                    os.fchmod(handle.fileno(), mode)
        _replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
