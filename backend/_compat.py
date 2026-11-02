"""Small platform helpers PPTX Maker used from ``kiro_crew.platform_compat``.

Restated here: they are a few lines each, carry no security decision, and are
exercised by this repository's tests on every OS the manifest names.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

logger = logging.getLogger("kirocrew.app.pptx-maker")

IS_WINDOWS: bool = sys.platform == "win32"
IS_POSIX: bool = not IS_WINDOWS


def strip_extended_length_prefix(path: Any) -> Any:
    r"""*path* without Windows' extended-length prefix; unchanged elsewhere.

    ``Path.resolve()`` can return ``\\?\C:\...`` for one side of a comparison and
    the plain spelling for the other, and the lone prefix then reads as an
    escape. LEXICAL on purpose: resolving again could bless a redirect.
    """
    text = str(path)
    if text.startswith("\\\\?\\UNC\\"):
        return type(path)("\\\\" + text[8:])
    if text.startswith("\\\\?\\"):
        return type(path)(text[4:])
    return path


def chmod_safe(path: str | os.PathLike, mode: int) -> None:
    """Apply *mode* to *path*; logs on failure. A no-op on Windows."""
    if IS_POSIX:
        try:
            os.chmod(path, mode)
        except OSError:
            logger.warning("pptx-maker: cannot set permissions on %s", path)
