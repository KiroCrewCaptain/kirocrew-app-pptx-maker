"""PPTX Maker — one-time provisioning of the vendored engine.

The presentation engine is a separate public OSS project, so it is not vendored
into this repository. It is fetched as a **digest-pinned tarball** into the app's
data dir on first use (see :mod:`.engine_source`) and given its own uv-managed
virtualenv.

**Nothing has to be installed by hand.** Kiro Crew is the only prerequisite:
``uv`` is one of its declared Python dependencies and is resolved through the
installed package (:func:`resolve_uv`) rather than assumed to be on ``PATH``,
and the engine arrives over plain HTTPS, so ``git`` is not required at all.

**The agents are rendered by the gateway, not here.** The shipped agent
templates (``agents/*.json``) name runtime paths through the host placeholders
every Kiro Crew app's agent templates may use (:data:`PLACEHOLDERS`): the app's
install root, its data dir, the gateway's ``uv``, the host ``PATH`` and its
separator. The gateway computes each value itself and renders the template into a
file under the data home whenever it registers the app's agents (enable, gateway
start). Writing rendered configs from here would put an executable path
(``{UV_BIN}``) in a file the agent can rewrite, and into this app's own checkout,
which an update replaces. :func:`render_agent_template` and
:func:`placeholder_values` are this app's statement of that contract (what each
placeholder means and how a value is escaped), pinned by the tests, and the
templates spell the engine and preview-tool paths out from ``{APP_DATA}`` exactly
as :mod:`.paths` derives them.

Provisioning is user-triggered (``POST /engine/provision``) and idempotent.

Everything here is BLOCKING (network, uv, file copies) and runs on the subprocess
executor via ``routes.off_loop`` — never on the gateway event loop.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import engine, engine_source, paths
from ._fs import atomic_write
from ._host import resolve_uv as _shared_resolve_uv
from ._host import run_limited, sandboxed_spawn_argv, template_host_path, template_uv_bin
from .engine import UTF8_TEXT

logger = logging.getLogger("kirocrew.app.pptx-maker")

# The engine pin lives in `engine_source` (the module that verifies it). Re-exported
# here because this module's own API — and the routes/UI that read it — predate the
# split, and the pin is one concept regardless of which file holds the constant.
ENGINE_REPO = engine_source.ENGINE_REPO
ENGINE_TAG = engine_source.ENGINE_TAG
ENGINE_COMMIT = engine_source.ENGINE_COMMIT

# Timeouts (seconds). The dependency resolve is the slow leg — a cold `uv sync`
# builds wheels for the engine's native dependencies.
UV_SYNC_TIMEOUT = 900
UV_INSTALL_TIMEOUT = 300

# The `uv` executable basename. No platform suffix is appended: the wheel's own
# locator returns a full path, and `shutil.which` applies Windows `PATHEXT`.
_UV_BASENAME = "uv"

# Placeholders in the shipped agent configs: the host set Kiro Crew computes for
# every app (app-kit-platform.md "Agent-template placeholders"). A template may use
# no other, because the gateway renders nothing else and refuses to register an
# agent with a placeholder left in it.
#: The app's install root, which for an installed app IS this checkout.
PLACEHOLDER_APP_ROOT = "{APP_ROOT}"
#: The app's data dir (``<data home>/apps/pptx-maker/data``), ``paths.app_root()``.
#: The engine (``vendor/sdpm``) and the managed preview tools
#: (``vendor/preview-tools/bin``) live under it, and the templates spell those
#: subpaths out exactly as :mod:`.paths` derives them.
PLACEHOLDER_APP_DATA = "{APP_DATA}"
#: ``uv`` is resolved for the SAME reason the engine paths are: it is a declared
#: Python dependency of Kiro Crew, so it is always installed, but not necessarily on
#: ``PATH``. A wheel install puts it in the venv's scripts dir and the gateway may
#: run with a minimal ``PATH`` (an installed launchd/systemd service), so a bare
#: ``"command": "uv"`` in an agent config resolved to nothing and every deck
#: generation failed with executable-not-found.
PLACEHOLDER_UV_BIN = "{UV_BIN}"
#: The directories the engine's tools are found in (:func:`host_path`): a fixed set
#: on a current gateway, its own ``PATH`` on an older one. The MCP server's ``PATH`` is
#: this, then ``{PATH_SEP}``, then the managed tool dir: the engine shells out to
#: ``pdftoppm``/``soffice`` by name from inside the MCP server that kiro-cli spawns
#: from the rendered config, so this is the ONLY place the app can put its managed
#: tool dir where those lookups will see it.
PLACEHOLDER_HOST_PATH = "{HOST_PATH}"
#: ``os.pathsep`` of the gateway's host, so one template serves every platform.
PLACEHOLDER_PATH_SEP = "{PATH_SEP}"

#: Every placeholder a shipped agent template may carry.
PLACEHOLDERS: tuple[str, ...] = (
    PLACEHOLDER_APP_ROOT,
    PLACEHOLDER_APP_DATA,
    PLACEHOLDER_UV_BIN,
    PLACEHOLDER_HOST_PATH,
    PLACEHOLDER_PATH_SEP,
)

#: This app's own checkout: the shipped agent templates and prompts live here.
APP_ROOT = Path(__file__).resolve().parent.parent
_AGENTS_SUBDIR = "agents"

# Max captured log characters handed back to the UI.
LOG_TAIL_CHARS = 4000


@dataclass
class ProvisionOutcome:
    """Result of one provisioning run."""

    ok: bool
    log: str
    engine_tag: str = ""

    def to_dict(self) -> dict:
        return {"ok": self.ok, "log": self.log[-LOG_TAIL_CHARS:], "engineTag": self.engine_tag}


_uv_path_cache: str | None = None
_uv_path_resolved = False


def resolve_uv() -> str | None:
    """Absolute path to a usable ``uv``, or ``None`` when genuinely absent.

    The ladder itself — ``uv.find_uv_bin()`` from the declared wheel, then
    ``shutil.which("uv")``, never raising — is the gateway's own resolver
    (``_host.resolve_uv``), so the minimal-``PATH`` case (an installed
    launchd/systemd gateway) is handled the way the gateway handles it for the
    agents' ``{UV_BIN}``.

    Cached process-wide: this runs on every provision and the answer cannot
    change within a process (the interpreter's own site-packages are fixed at
    startup).
    """
    global _uv_path_cache, _uv_path_resolved
    if _uv_path_resolved:
        return _uv_path_cache
    _uv_path_cache = _shared_resolve_uv()
    _uv_path_resolved = True
    return _uv_path_cache


def host_path() -> str:
    """What the gateway renders into ``{HOST_PATH}``.

    A current Kiro Crew renders a fixed set of directories: the account's
    tool-manager bins, the system dirs and, on Windows, LibreOffice's fixed install
    roots, never its own ``PATH`` (``_host.template_host_path``). The engine MCP
    server's launch is part of what an operator approves when they share it, and a
    ``PATH`` copied from the gateway would have that approval refused whenever the
    gateway is started from another shell. A tool found only on the gateway's own
    ``PATH`` outside those directories is therefore not on the server's ``PATH``,
    and ``/deps`` (:func:`.engine.optional_dep_path`) searches the same directories
    so it never reports one.

    An older Kiro Crew renders its own ``PATH``, plus the directory of a tool
    installed at a fixed platform install root that its installer does not put on
    ``PATH`` (LibreOffice on Windows, :func:`.engine.soffice_install_dir`), so that is
    restated here for it.

    Empty when there is nothing to put in it. The gateway then renders no
    ``{HOST_PATH}`` at all and registers none of the agents, rather than a ``PATH``
    that starts with a separator (an empty element means "the current directory" on
    POSIX).
    """
    rendered = template_host_path()
    if rendered is not None:
        return rendered
    inherited = os.environ.get("PATH", "")
    entries = [inherited] if inherited else []
    system_dir = engine.soffice_install_dir()
    if system_dir:
        entries.append(system_dir)
    return os.pathsep.join(entries)


def mcp_tools_path() -> str:
    """``PATH`` value the rendered config gives the engine MCP server.

    The engine resolves ``pdftoppm``/``soffice`` with ``shutil.which()`` inside the
    MCP server process that kiro-cli spawns, so the app's managed tool directory has
    to be on THAT process's ``PATH``. Nothing the gateway does to its own
    subprocesses affects it. The templates spell it
    ``{HOST_PATH}{PATH_SEP}{APP_DATA}/vendor/preview-tools/bin``.

    The managed directory is **appended** to :func:`host_path`, so a real system
    poppler or LibreOffice still wins the engine's own lookup and the managed
    launcher stays a fallback. It is on the ``PATH`` whether or not it exists yet: a
    missing directory matches nothing, and the launcher the first provision writes
    is reachable without waiting for the next registration.
    """
    entries = [host_path(), str(paths.preview_tools_bin())]
    return os.pathsep.join(entry for entry in entries if entry)


def reset_uv_cache() -> None:
    """Forget the resolved ``uv`` path (tests only)."""
    global _uv_path_cache, _uv_path_resolved
    _uv_path_cache = None
    _uv_path_resolved = False


def _run(argv: list[str], *, cwd: str, timeout: int) -> tuple[int, str]:
    """Run *argv* under the shared sandbox, returning ``(exit code, output)``.

    Uses the gateway's ``sandboxed_spawn_argv`` chokepoint (``_host``) so the
    child gets a CREDENTIAL-SCRUBBED environment as well as the OS sandbox.
    Provisioning runs ``uv`` against a freshly-downloaded third-party tree, so
    these spawns execute third-party code and must not inherit the gateway's raw
    ``os.environ`` (cloud credentials, ``SSH_AUTH_SOCK``, bot tokens).

    ``argv[0]`` is an ABSOLUTE path (see :func:`resolve_uv`), which the
    chokepoint passes through unchanged — so uv is found even when the scrubbed
    env's ``PATH`` does not contain it. uv itself is a static Rust binary and
    needs nothing from the cleared Python env.

    ``strip_python_env`` clears ``PYTHONPATH`` for the same reason the engine
    calls clear it: the engine pins its own native dependencies and must not
    inherit the gateway's.

    BLOCKING — callers are already off the loop.
    """
    # ``mode="strict"`` for the same reason as the engine spawns: the env scrub
    # stops this child inheriting a credential, not reading one off disk, and
    # ``standard`` leaves ``~/.aws``/``~/.ssh`` visible for the AWS CLI and
    # git-over-SSH. Provisioning runs a build backend over a freshly-downloaded
    # third-party tree, which is exactly the code least entitled to either. No
    # sandbox mode restricts network, so uv still reaches the index.
    # The chokepoint already applies the cgroup scope as its outermost layer.
    # Wrapping again nests a second ``systemd-run --scope`` that inherits the
    # first one's PID and collides on its auto-named unit.
    wrapped, env, cleanup = sandboxed_spawn_argv(argv, mode="strict", strip_python_env=True)
    try:
        proc = run_limited(  # noqa: S603 - fixed argv, no request-derived values
            wrapped,
            cwd=cwd,
            env=env,
            capture_output=True,
            **UTF8_TEXT,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 1, f"{argv[0]} timed out after {timeout}s"
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, f"{argv[0]} could not be run: {exc}"
    finally:
        if cleanup:
            try:
                os.unlink(cleanup)
            except OSError:
                pass
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def _current_tag(engine_root: Path) -> str:
    """The tag recorded for the installed engine tree, or ``"unknown"``.

    Read from the source marker a digest-verified install writes, so the reported
    tag can only ever be one whose bytes were verified — never a version this
    code merely hoped was on disk.
    """
    return engine_source.installed_tag(engine_root)


def _ensure_engine(engine_root: Path, log: list[str], uv_bin: str) -> bool:
    """Fetch and verify the pinned engine tree. See :mod:`.engine_source`.

    Kept as a thin seam (rather than calling ``engine_source`` inline) so
    :func:`provision`'s failure ladder — refuse before building, never register
    agents against a tree that did not land — stays readable in one place.
    """
    # The venv is built in the STAGED tree, before it replaces a working engine.
    # The venv lives inside the tree, so the swap discards the old one either way —
    # and if the dependency resolve then failed, the user was left with no usable
    # engine at all and the app simply unavailable. Validating first makes the worst
    # case "still on the previous version", the same degradation a refused download
    # already gives.
    #
    # `_ensure_venv` takes the root as a parameter, so pointing it at the staging
    # directory needs no other change: everything it touches is derived from that
    # argument.
    # `finalize` RE-LINKS the editable install at the final path, and it runs inside
    # the swap window so a failure restores the previous tree.
    #
    # Validating in staging is what protects a working engine, but an `--editable`
    # install records an ABSOLUTE path: the `.pth` written during validation points at
    # the staging directory, which the swap then renames and deletes. So every `sdpm`
    # import failed with ModuleNotFoundError while provisioning reported success —
    # reproduced against real `uv`, where the `.pth` held
    # `/private/tmp/.../staged/skill/src` after the tree had moved to `.../engine`.
    #
    # It is a `finalize` hook rather than a call after `install_engine` returns because
    # the swap deletes the retired tree as soon as the new one is in place — relinking
    # afterwards had no rollback left, so a failure there deleted the working engine
    # AND left the new one pointing at a removed path.
    #
    # Re-running only the editable step (not the whole resolve) is the cheap half: the
    # dependencies are already in the venv and this is a local path install with no
    # network, so it is fast and cannot fail for a reason the staging validation would
    # not already have caught.
    return engine_source.install_engine(
        engine_root,
        log,
        validate=lambda staged: _ensure_venv(staged, log, uv_bin),
        finalize=lambda final: _relink_editable_skill(final, log, uv_bin),
    )


class _Disabled(Exception):
    """The app was disabled mid-provision, so registration is skipped."""


def _venv_ready(engine_root: Path) -> bool:
    """True when *engine_root* already carries a built venv.

    Root-parameterized rather than reading ``paths.engine_python()``, so it can ask
    the same question of a STAGED tree as of the live one. Same probe
    ``engine.engine_status`` reports to the provisioning banner. The layout itself
    comes from ``paths.venv_python`` so this probe cannot disagree with the
    interpreter the install step actually writes.
    """
    return paths.venv_python(engine_root).is_file()


def _ensure_venv(engine_root: Path, log: list[str], uv_bin: str) -> bool:
    """Resolve the engine's dependencies into its own uv-managed venv.

    *uv_bin* is the ABSOLUTE path from :func:`resolve_uv` — never the bare name
    ``"uv"``, which would only work when the gateway's ``PATH`` happens to carry
    the venv's scripts dir (it does not under an installed launchd/systemd
    service, and there is no scripts dir at all in the frozen DMG bundle).
    """
    mcp_dir = engine_root / "mcp-local"
    if not mcp_dir.is_dir():
        log.append("the engine checkout has no mcp-local directory")
        return False
    log.append("resolving engine dependencies…")
    # `--frozen` installs the engine's `uv.lock` as pinned: without it uv re-resolves a
    # lock it judges stale and can pick an `mcp` major the engine cannot import. The
    # agents' `uv run` passes it for the same reason.
    code, out = _run(
        [uv_bin, "sync", "--frozen", "--project", str(mcp_dir)],
        cwd=str(engine_root),
        timeout=UV_SYNC_TIMEOUT,
    )
    if code != 0:
        log.append(f"dependency resolve failed: {out}")
        return False
    return _relink_editable_skill(engine_root, log, uv_bin)


def _relink_editable_skill(engine_root: Path, log: list[str], uv_bin: str) -> bool:
    """Install the engine's ``skill`` package EDITABLE against *engine_root*.

    A normal install copies only the Python package into site-packages and drops its
    sibling data dirs (bundled templates and example styles), which the engine resolves
    relative to the package — so the builtin styles/templates would silently be missing.
    Hence editable.

    Called TWICE, and both calls are load-bearing: once inside the staging validation, so
    a broken tree never replaces a working engine, and once after the swap, because an
    editable install records an ABSOLUTE path. The ``.pth`` written in staging names the
    staging directory, which the swap renames and then deletes — leaving every ``sdpm``
    import failing with ModuleNotFoundError while provisioning reported success.
    Reproduced against real ``uv``.

    Idempotent and cheap: the dependencies are already resolved into the venv, and this
    is a local path install with no network.
    """
    python = paths.venv_python(engine_root)
    log.append("installing the engine skill package…")
    code, out = _run(
        [
            uv_bin,
            "pip",
            "install",
            "--python",
            str(python),
            "--editable",
            str(engine_root / "skill"),
        ],
        cwd=str(engine_root),
        timeout=UV_INSTALL_TIMEOUT,
    )
    if code != 0:
        log.append(f"skill package install failed: {out}")
        return False
    return True


def _json_escape(value: str) -> str:
    """Escape *value* for interpolation into a JSON **string literal**.

    The placeholders sit inside quoted JSON strings, so the substituted
    text has to be JSON-escaped or the rendered config is not JSON at all. This
    is not cosmetic: on Windows every one of these values is an absolute path
    full of backslashes (``C:\\Users\\me\\.kiro\\crew\\...``), and ``\\U``/``\\c``
    are invalid JSON escapes — so a naive substitution made ``json.loads``
    raise for EVERY template and Windows users got no agent configs at all.

    ``json.dumps`` adds the surrounding quotes the template already provides, so
    they are stripped. Going through ``json.dumps`` rather than a hand-rolled
    ``replace`` means every other character JSON reserves is handled too.
    """
    return json.dumps(value)[1:-1]


def placeholder_values() -> dict[str, str]:
    """The value each placeholder stands for, computed the way the gateway does.

    ``{APP_ROOT}`` is this checkout: for an installed app the gateway's install
    directory IS the checkout. ``{HOST_PATH}`` is left out when :func:`host_path`
    is empty, as the gateway leaves it out, so rendering then refuses.
    """
    values = {
        PLACEHOLDER_APP_ROOT: str(APP_ROOT),
        PLACEHOLDER_APP_DATA: str(paths.app_root()),
        # Falls back to the bare name only when uv is genuinely absent, which
        # `provision` already reports as a hard failure.
        PLACEHOLDER_UV_BIN: template_uv_bin() or _UV_BASENAME,
        PLACEHOLDER_PATH_SEP: os.pathsep,
    }
    host = host_path()
    if host:
        values[PLACEHOLDER_HOST_PATH] = host
    return values


def render_agent_template(template: str, values: dict[str, str]) -> dict:
    """*template* with every placeholder substituted, parsed. Raises ``ValueError``.

    Every substituted value is JSON-escaped (:func:`_json_escape`) because the
    placeholders sit inside JSON string literals. A placeholder with no value,
    or a result that is not JSON, raises: a malformed agent config is silently
    ignored by kiro-cli, which would surface as "the mode is missing" with no
    explanation.
    """
    rendered = template
    for placeholder, value in values.items():
        rendered = rendered.replace(placeholder, _json_escape(value))
    leftover = [p for p in PLACEHOLDERS if p in rendered]
    if leftover:
        raise ValueError(f"unresolved placeholder {leftover[0]}")
    data = json.loads(rendered)
    if not isinstance(data, dict):
        raise ValueError("an agent config must be a JSON object")
    return data


def shipped_agent_templates() -> list[Path]:
    """The agent templates this app ships, in name order."""
    source_dir = APP_ROOT / _AGENTS_SUBDIR
    return sorted(source_dir.glob("*.json")) if source_dir.is_dir() else []


def _seed_deck_root(log: list[str]) -> None:
    """Give a brand-new engine install a deck directory that needs no OS grant.

    Only for a first install (no engine config yet, no decks in the engine's
    default location): the engine defaults to ``~/Documents``, which on macOS is
    behind a file-access prompt the gateway cannot answer, so a first-run deck
    would fail to write with a permission error the user cannot act on. An
    existing config or existing decks are never touched.
    """
    config_path = paths.engine_config_path()
    if config_path.exists():
        return
    default_root = Path.home() / paths.ENGINE_DEFAULT_DECK_ROOT
    try:
        if default_root.is_dir() and any(default_root.iterdir()):
            return
    except OSError:
        return
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(
            config_path,
            json.dumps({"output_dir": paths.SEEDED_DECK_ROOT}, indent=2) + "\n",
        )
        log.append(f"deck output directory set to {paths.SEEDED_DECK_ROOT}")
    except OSError as exc:
        log.append(f"deck output directory could not be set: {exc}")


def provision() -> ProvisionOutcome:
    """Provision the engine and its managed preview tool.

    Idempotent. Safe to re-run: the engine tree is re-fetched only if it does not
    already match the pin, and the venv is rebuilt only when it is missing.

    BLOCKING — call through ``routes.off_loop``.
    """
    log: list[str] = []
    engine_root = paths.engine_root()

    # `uv` ships as a declared Python dependency, so this only fails on a
    # genuinely broken install. Reported precisely (and only about uv — `git` is
    # not used) so the message is actionable rather than a guess.
    uv_bin = resolve_uv()
    if uv_bin is None:
        log.append(
            "`uv` could not be found. It ships with Kiro Crew as a Python "
            "dependency, so this usually means the install is incomplete — "
            "reinstall with `pip install --force-reinstall uv`, or install uv "
            "yourself and put it on PATH."
        )
        return ProvisionOutcome(ok=False, log="\n".join(log))

    # ONE call: `_ensure_engine` now builds the venv inside the staged tree before
    # swapping it in (see its comment), so a second `_ensure_venv` here would
    # re-resolve the same dependencies against the live tree for no benefit.
    if not _ensure_engine(engine_root, log, uv_bin):
        return ProvisionOutcome(ok=False, log="\n".join(log))
    # An already-pinned tree short-circuits the fetch, so its venv may predate this
    # install (or a user may have removed it). Build it if it is not there.
    if not _venv_ready(engine_root):
        if not _ensure_venv(engine_root, log, uv_bin):
            return ProvisionOutcome(ok=False, log="\n".join(log))

    # The managed `pdftoppm` launcher. It runs the engine venv's own `pypdfium2`,
    # so it only becomes installable once the venv above exists, and this is the
    # step the user already triggers to make the app usable. Keeping it off a
    # `/deps/install` route also preserves the app's rule that no browser request
    # installs a system package.
    #
    # The agents' MCP server `PATH` names the managed directory whether or not it
    # exists (`mcp_tools_path()`), so the launcher written here reaches the engine's
    # MCP server at its next start.
    #
    # Best effort: without it the deck still builds, only the thumbnails are
    # missing, so a failure is reported and does not fail provisioning.
    try:
        from . import preview_tools

        tool_ok, tool_msg = preview_tools.install_pdftoppm()
        log.append(tool_msg if tool_ok else f"pdftoppm could not be installed: {tool_msg}")
    except Exception as exc:  # noqa: BLE001 - provisioning must report, not raise
        log.append(f"pdftoppm setup skipped: {exc}")

    # Nothing is registered or rendered here: the gateway registers the agents and
    # the skill on enable and at start, rendering the templates itself (see the
    # module docstring). A provisioning job registering mid-run could also race a
    # disable the operator made while it ran, recreating resources the disable had
    # just removed.

    _seed_deck_root(log)

    # Analyze any template the engine has no metadata for yet — the ones it ships
    # on a fresh clone, plus anything the user dropped into the templates dir by
    # hand. Without this such a template lists with no theme colours or layout
    # count until it is re-imported through the UI. Best effort: an un-analyzed
    # template is still usable, so a failure here must not fail provisioning.
    try:
        from . import library

        # Under the same `state.json` lock as a UI template import, for the same
        # reason: `scan_new_templates` reads `template_metadata`, adds to it, and
        # writes it back, so overlapping this with an import would drop one side's
        # entry. Provisioning and an import CAN overlap — provisioning runs in the
        # background while the library page stays usable.
        with library.state_transaction():
            analyzed = engine.scan_new_templates()
        if analyzed:
            log.append(f"analyzed {len(analyzed)} template(s)")
    except Exception as exc:  # noqa: BLE001 - provisioning must report, not raise
        log.append(f"template analysis skipped: {exc}")

    tag = _current_tag(engine_root)
    log.append(f"engine ready at {tag}")
    return ProvisionOutcome(ok=True, log="\n".join(log), engine_tag=tag)
