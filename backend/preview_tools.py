"""PPTX Maker — installing the app-managed preview tools.

Slide thumbnails need two binaries the engine shells out to by name, and a stock
machine has neither:

* ``pdftoppm`` (poppler) — rasterizes the PDF to per-page PNGs. **Provided here,
  on every OS, with no download**: ``pypdfium2`` is already a dependency of the
  engine's own venv, so this module only has to write a launcher named
  ``pdftoppm`` that runs :mod:`.pdftoppm_shim` inside that venv.
* ``soffice`` (LibreOffice) — converts the .pptx to PDF. **Not installable from
  here**, for the cost-and-coverage reasons recorded in
  ``docs/system-specs/modules/pptx-maker.md`` § Preview tools (the authority for
  this decision): it publishes only OS installers, none of them an
  unpack-and-run tree, and the Windows ``.msi``'s cab is LZX-compressed, which
  the Python stdlib cannot decompress at all. So it is reported with a per-OS
  instruction instead (:func:`soffice_hint`), and the user's own install is
  picked up automatically.

Why one bin dir: the engine runs both tools by bare name, with no shell, inside the
``sdpm`` MCP server that kiro-cli spawns, so whatever is installed must sit on THAT
process's ``PATH``. :func:`.paths.preview_tools_bin` is that single directory, and
the agent templates append it to ``mcpServers.sdpm.env.PATH``
(:func:`.provision.mcp_tools_path`), so a real system tool still wins the
engine's own lookup.

Everything here is BLOCKING (filesystem) and must run through ``routes.off_loop``.
"""

from __future__ import annotations

import importlib.util
import io
import logging
import os
import shlex
import stat
import struct
import sys
import sysconfig
import zipfile
from functools import lru_cache
from pathlib import Path

from . import _compat as platform_compat
from . import _os_deps as os_deps
from . import paths
from ._fs import atomic_write

logger = logging.getLogger("kirocrew.app.pptx-maker")

#: The tool this module can provide.
PDFTOPPM = "pdftoppm"
#: The tool it deliberately cannot — a system package, reported with a hint.
SOFFICE = "soffice"

#: Mode for the generated launcher: OWNER-ONLY rwx. It lives under the data home and
#: is executed by the gateway's own engine children, so nothing else needs to read or
#: run it — and a file the engine execs must not be group- or world-writable.
_LAUNCHER_MODE = 0o700

#: ``MAX_PATH``: the Windows launcher reads the line naming its interpreter into a
#: buffer of this many bytes, and a longer line fails every run.
_LAUNCHER_LINE_LIMIT = 260

#: A fixed timestamp for the launcher's zip entry, so a reinstall writes the same bytes.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

#: Per-OS instruction for the one tool that cannot be auto-installed. Shown by the
#: UI verbatim; the user runs it themselves, which is the documented contract for
#: a system package. macOS and Windows each have one conventional manager; Linux
#: has several, so :func:`_linux_soffice_hint` decides it per host.
_SOFFICE_HINTS = {
    "darwin": "brew install --cask libreoffice",
    "win32": "winget install TheDocumentFoundation.LibreOffice",
}

#: Linux hints keyed by the distribution's os-release ``ID``/``ID_LIKE`` tokens.
_LINUX_APT_HINT = "sudo apt install libreoffice"
_LINUX_RPM_HINT = "sudo {manager} install libreoffice"
_APT_IDS = frozenset({"debian", "ubuntu"})
#: The Fedora/RHEL lineage, whose manager is ``dnf``, ``yum`` or ``microdnf`` by
#: generation and is probed per host. SUSE is absent on purpose: its manager is
#: ``zypper``.
_RPM_IDS = frozenset({"fedora", "rhel", "centos"})
#: Distributions with no LibreOffice package in any enabled repo. Amazon Linux
#: 2023 reports ``ID_LIKE=fedora``, yet ``dnf install libreoffice`` finds nothing
#: there, so it is checked before the rpm family can claim it.
_UNPACKAGED_IDS = frozenset({"amzn"})


@lru_cache(maxsize=1)
def _linux_soffice_hint() -> str | None:
    """Cached: the distribution does not change under a running process, and one
    caller asks from the event loop.

    The command names only what this host has: the apt family by lineage, the rpm
    family by the manager :func:`os_deps.rpm_package_manager` finds (the probe the
    browser installer uses). Any other distribution, SUSE included, gets ``None``,
    as in the browser installer.
    """
    ids = os_deps.os_release_ids()
    if ids & _UNPACKAGED_IDS:
        return None
    if ids & _APT_IDS:
        return _LINUX_APT_HINT
    if ids & _RPM_IDS:
        manager = os_deps.rpm_package_manager()
        return _LINUX_RPM_HINT.format(manager=manager) if manager else None
    return None


def soffice_hint() -> str | None:
    """The install command for LibreOffice on this host, or ``None``.

    Returned as data for the UI to display, never executed here: running a package
    manager on the operator's behalf from a browser request is the host mutation
    this app refuses.

    ``None`` means there is no command to give: this distribution packages no
    LibreOffice, Kiro Crew cannot name its package manager (SUSE, any unrecognized
    Linux), or its rpm manager is not on ``PATH``. Every surface frames the hint as
    "install it with:", so a guessed command that fails on its own first argument
    or package name is worse than none.
    """
    if sys.platform.startswith("linux"):
        return _linux_soffice_hint()
    return _SOFFICE_HINTS.get(sys.platform, "install LibreOffice for your platform")


def _engine_python() -> Path | None:
    """The engine venv interpreter that owns ``pypdfium2``, or ``None``.

    ``pypdfium2`` lives in the ENGINE's venv, not the gateway's, so the shim has to
    run under this interpreter. ``None`` means the engine is not provisioned yet,
    which the caller reports as "provision the engine first" rather than writing a
    launcher that could not work.

    The platform's venv layout is resolved by ``paths.venv_python``, which is the
    single authority: it knows both the POSIX ``bin/python`` and the Windows
    ``Scripts\\python.exe`` layout, so no caller carries a private candidate list.
    """
    candidate = paths.engine_python()
    return candidate if candidate.is_file() else None


def _launcher_paths() -> list[Path]:
    """Every launcher file installed for ``pdftoppm`` on this platform.

    Windows gets a ``.exe``. The engine runs a bare ``pdftoppm`` through
    ``subprocess.run`` with no shell, and ``CreateProcess`` appends only ``.exe`` to
    a name without an extension: ``PATHEXT`` is honoured by shells and
    ``shutil.which``, never by ``CreateProcess``, so a ``.cmd`` is not found there.
    POSIX gets the extensionless name poppler uses.
    """
    bin_dir = paths.preview_tools_bin()
    if platform_compat.IS_WINDOWS:
        return [bin_dir / f"{PDFTOPPM}.exe"]
    return [bin_dir / PDFTOPPM]


def _unreachable_launcher_paths() -> list[Path]:
    """Launcher files in the bin dir that the engine cannot run, removed on install.

    The engine never spawns a ``pdftoppm.cmd`` (see :func:`_launcher_paths`), but
    ``shutil.which`` honours ``PATHEXT`` and finds one, so ``/deps`` would keep
    reporting the managed tool present while every thumbnail failed.
    """
    if platform_compat.IS_WINDOWS:
        return [paths.preview_tools_bin() / f"{PDFTOPPM}.cmd"]
    return []


def _launcher_body(python: Path, shim: Path) -> str:
    """The POSIX launcher script text.

    It execs the engine's interpreter against the shim module FILE, not
    ``-m``: ``-m`` would need the gateway package importable from the engine's
    venv, which it deliberately is not. Arguments are forwarded verbatim so the
    engine's own ``argv`` reaches the shim unaltered.

    The engine venv's own console scripts cannot be reused for this — their
    shebangs point at the temporary staging directory the install swapped away, so
    they are unrunnable. Naming the interpreter and the file directly avoids that
    entirely.

    Both paths are QUOTED FOR THEIR SHELL, not merely wrapped in double quotes.
    ``KIROCREW_HOME`` is user-chosen, so either path can contain a character its
    shell would otherwise interpret: inside POSIX double quotes ``$`` and
    backticks still expand and ``\\`` still escapes, so a data home like
    ``/tmp/my $home/`` produced a launcher pointing at a path with the ``$home``
    segment silently deleted, and every invocation failed with "cannot execute".
    :func:`shlex.quote` (single quotes, with embedded single quotes escaped)
    removes every such expansion.
    """
    # `exec` keeps a single process so the engine's `subprocess.run` sees the
    # shim's real exit code, not a wrapper's. `"$@"` forwards argv unaltered.
    return f'#!/bin/sh\nexec {shlex.quote(str(python))} {shlex.quote(str(shim))} "$@"\n'


def _launcher_stub_name() -> str:
    """distlib's console launcher for this machine, chosen the way distlib chooses it."""
    if sysconfig.get_platform() == "win-arm64":
        return "t64-arm.exe"
    return "t64.exe" if struct.calcsize("P") == 8 else "t32.exe"


def _launcher_stub() -> bytes | None:
    """distlib's console launcher, read from the gateway's own ``pip``, or ``None``.

    pip vendors distlib and ships its launcher for every Windows architecture,
    because it writes every console script on Windows from it. Located through
    ``find_spec`` so nothing of pip is imported into the gateway.
    """
    try:
        spec = importlib.util.find_spec("pip")
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    for location in spec.submodule_search_locations:
        try:
            data = (Path(location) / "_vendor" / "distlib" / _launcher_stub_name()).read_bytes()
        except OSError:
            continue
        if data.startswith(b"MZ"):
            return data
    return None


def _windows_launcher(python: Path, shim: Path) -> tuple[bytes | None, str]:
    """``pdftoppm.exe`` as bytes, or ``(None, why)`` when no runnable one can be built.

    The layout pip writes for every Windows console script: distlib's launcher, a
    ``#!"<interpreter>"`` line, then a zip whose ``__main__.py`` runs the shim FILE.
    The launcher reads that line, runs ``"<interpreter>" "<this .exe>" <args>``,
    waits, and exits with the child's code; the interpreter runs the zip at the end
    of the ``.exe``. Nothing in it depends on the engine's Python version.

    The launcher decodes the line as strict UTF-8 and reads at most
    :data:`_LAUNCHER_LINE_LIMIT` bytes of it, so an interpreter path that breaks
    either rule is refused here instead of installing a launcher that fails every
    run.
    """
    stub = _launcher_stub()
    if stub is None:
        return None, (
            "the Windows launcher pip ships (pip/_vendor/distlib/"
            f"{_launcher_stub_name()}) is not in this Kiro Crew install, so "
            "pdftoppm.exe cannot be built"
        )
    try:
        line = f'#!"{python}"\r\n'.encode("utf-8")
    except UnicodeEncodeError:
        line = b""
    if not line or len(line) > _LAUNCHER_LINE_LIMIT:
        return None, (
            "the pdftoppm launcher cannot name the engine interpreter: its path is not "
            f"valid UTF-8 or is longer than the {_LAUNCHER_LINE_LIMIT} bytes the "
            "launcher reads. Point KIROCREW_HOME at a shorter path."
        )
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        # `repr` spells any path as a valid Python literal, quotes and backslashes
        # included, so the zip's source needs no escaping of its own.
        source = f"import runpy\nrunpy.run_path({str(shim)!r}, run_name='__main__')\n"
        bundle.writestr(zipfile.ZipInfo("__main__.py", date_time=_ZIP_EPOCH), source)
    return stub + line + archive.getvalue(), ""


def _launcher_data(python: Path, shim: Path) -> tuple[bytes | None, str]:
    """The launcher file's bytes for this platform, or ``(None, why)``.

    The POSIX script is encoded STRICTLY in UTF-8, the bytes ``/bin/sh`` passes
    through: a path that cannot be spelled there would make the launcher point at a
    path that does not exist while the install reported ready.
    """
    if platform_compat.IS_WINDOWS:
        return _windows_launcher(python, shim)
    try:
        return _launcher_body(python, shim).encode("utf-8"), ""
    except UnicodeEncodeError:
        return None, (
            "the pdftoppm launcher cannot be written in utf-8, the encoding its shell "
            "reads it in: the engine or app path holds a character that encoding cannot "
            "represent. Point KIROCREW_HOME at a path it can spell."
        )


def _remove(launchers: list[Path]) -> None:
    """Best-effort removal of launcher files that must not read as installed."""
    for launcher in launchers:
        try:
            launcher.unlink(missing_ok=True)
        except OSError:
            pass


def pdftoppm_installed() -> bool:
    """True when the managed ``pdftoppm`` launcher is present and executable."""
    for launcher in _launcher_paths():
        try:
            if not launcher.is_file():
                return False
        except OSError:
            return False
        if not platform_compat.IS_WINDOWS and not os.access(launcher, os.X_OK):
            return False
    return True


def install_pdftoppm() -> tuple[bool, str]:
    """Install the managed ``pdftoppm``. Returns ``(ok, message)``.

    Idempotent: rewrites the launcher every time, so re-running after an engine
    update re-points it at the current interpreter (the engine tree is replaced on
    each version bump, but its path is stable, so this is belt-and-braces).

    BLOCKING — call through ``routes.off_loop``.
    """
    python = _engine_python()
    if python is None:
        return False, "the presentation engine is not installed yet — install it first"

    shim = Path(__file__).resolve().parent / "pdftoppm_shim.py"
    if not shim.is_file():
        # Would mean a broken package install; reported rather than written blind.
        return False, "the bundled pdftoppm shim is missing from this install"

    bin_dir = paths.preview_tools_bin()
    try:
        bin_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"cannot create {bin_dir}: {exc}"

    _remove(_unreachable_launcher_paths())
    data, refusal = _launcher_data(python, shim)
    if data is None:
        # A launcher left from an earlier install points at a path this install
        # could not name either, so it is removed rather than reported installed.
        _remove(_launcher_paths())
        return False, refusal
    for launcher in _launcher_paths():
        try:
            # Bytes, so no newline translation applies: the file holds exactly the
            # bytes `_launcher_data` built, line endings and the `.exe` image alike.
            atomic_write(launcher, data)
            # chmod AFTER the write and before any caller can resolve it, so the
            # launcher is never observable without its exec bit.
            if not platform_compat.IS_WINDOWS:
                # 0o700 here is the EXEC bit, not secrecy: the body is generated
                # launcher text with no payload, so nothing is exposed in the
                # window before the chmod. The marker must sit on the call line.
                platform_compat.chmod_safe(launcher, _LAUNCHER_MODE)  # lockdown-ok: exec bit
        except OSError as exc:
            return False, f"cannot write {launcher.name}: {exc}"

    if not pdftoppm_installed():
        return False, "the pdftoppm launcher failed its post-install check"
    logger.info("pptx-maker: installed the managed pdftoppm launcher in %s", bin_dir)
    return True, "pdftoppm is ready"


def _mode_is_safe(path: Path) -> bool:
    """True when *path* is not group- or world-writable.

    The launcher is executed by the engine, so a writable one would be a way to
    run arbitrary code as the gateway user. Checked on the status path so a bad
    mode is visible rather than silently trusted.
    """
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return not bool(mode & (stat.S_IWGRP | stat.S_IWOTH))


def managed_status() -> dict[str, object]:
    """What the UI needs to describe the managed tools.

    BLOCKING (filesystem probes) — call through ``routes.off_loop``.
    """
    launchers = _launcher_paths()
    return {
        "pdftoppmInstalled": pdftoppm_installed(),
        "pdftoppmSecure": all(_mode_is_safe(p) for p in launchers if p.is_file()),
        "engineReady": _engine_python() is not None,
        "binDir": str(paths.preview_tools_bin()),
        "sofficeHint": soffice_hint(),
    }
