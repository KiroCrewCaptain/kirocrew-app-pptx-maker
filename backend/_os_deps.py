"""The two host probes the LibreOffice hint used from ``kiro_crew.browser_cli.os_deps``.

Restated: they read os-release and stat ``PATH``, run nothing, and carry no
security decision. Same answers as the gateway's, so the hint names the same
package manager the gateway's own browser installer would.
"""

from __future__ import annotations

import logging
import platform
import shutil

logger = logging.getLogger("kirocrew.app.pptx-maker")

#: SUSE lineage: its packages are named differently, so no rpm command is given.
_SUSE_IDS = frozenset({"opensuse", "sles", "suse"})


def _os_release() -> dict[str, str]:
    """This host's freedesktop os-release fields, or ``{}`` when there are none."""
    try:
        return platform.freedesktop_os_release()
    except OSError:
        logger.debug("no freedesktop os-release on this host", exc_info=True)
        return {}


def os_release_ids() -> frozenset[str]:
    """Lowercased ``ID`` and ``ID_LIKE`` tokens identifying this distribution."""
    release = _os_release()
    ids: set[str] = set()
    for key in ("ID", "ID_LIKE"):
        ids.update(token for token in release.get(key, "").lower().split() if token)
    return frozenset(ids)


def rpm_package_manager() -> str | None:
    """The supported rpm-family manager on ``PATH`` (``dnf``, ``yum``, ``microdnf``)."""
    if os_release_ids() & _SUSE_IDS:
        return None
    for manager in ("dnf", "yum", "microdnf"):
        if shutil.which(manager):
            return manager
    return None
