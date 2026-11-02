"""Every place PPTX Maker reaches into the Kiro Crew gateway, in one module.

An external app's Python runs inside the gateway process. PPTX Maker needs two
kinds of host surface:

1. **The App Kit context** handed to its hooks (``ctx``): the data directory,
   the owner gate (``ctx.require_owner``), the audit log (``ctx.audit``) and
   outbound redaction (``ctx.scrub``). These are public seams; each is read off
   the context with ``getattr`` and has a fallback that is never MORE permissive
   than the host: no owner gate refuses everyone, no scrubber withholds the
   text, no audit sink logs locally.

2. **Security controls that have no App Kit seam yet.** PPTX Maker downloads a
   third-party engine, builds it with ``uv`` and runs it over model-authored
   deck content, so every child goes through the gateway's OS sandbox
   chokepoint, the download through its verified downloader, every path through
   its sensitive-path gate and every agent-written file through its pinned,
   link-refusing reader. Copying those controls into this repository would leave
   a security control that looks present and drifts from the one the gateway
   enforces, so they are looked up in the running gateway instead, and only here
   (``tests/test_no_host_imports.py`` pins that). Each lookup is lazy and runs at
   call time through :func:`_host_attr`; when the gateway does not provide one,
   the operation that needs it is REFUSED (nothing is spawned, the download is
   refused, a path reads as sensitive, a file reads as unreadable), never run
   without it. ``PORTING.md`` lists each one as a gap.

Everything else PPTX Maker used from the gateway is either replaced by the
context above or restated in ``_compat.py``, ``_fs.py`` and ``_os_deps.py``
(small helpers that carry no security decision).
"""

from __future__ import annotations

import importlib
import logging
import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger("kirocrew.app.pptx-maker")

APP_NAME = "pptx-maker"

#: The App Kit seam level this app is written against.
REQUIRED_SDK_LEVEL = 1

#: Shown when the gateway lacks the sandboxed spawn chain. Names the cause and
#: what the operator can do, without an internal module path.
NO_HOST_SANDBOX = (
    "This Kiro Crew does not provide the sandboxed process launch PPTX Maker needs "
    "to run the presentation engine safely, so nothing was run. Update Kiro Crew."
)

#: What a text the gateway could not scan is replaced with. Never the text itself.
WITHHELD = "[withheld: this Kiro Crew could not scan it]"


# ── host resolution ─────────────────────────────────────────────────────────


def _host_attr(module: str, name: str) -> Any | None:
    """``module.name`` from the running gateway, or ``None`` when it has none.

    The one seam tests replace: a fake host answers here, so no test needs a
    Kiro Crew install.
    """
    try:
        mod = importlib.import_module(module)
    except Exception:  # noqa: BLE001 - any import failure means "not provided"
        return None
    return getattr(mod, name, None)


@dataclass(frozen=True)
class Route:
    """One ``hooks.routes`` record, accepted structurally by the gateway.

    A level-1 ``RouteRegistry`` takes any record with a str ``method``, a str
    ``path`` (relative to ``/api/apps/pptx-maker``) and a callable
    ``handler(request, ctx)``.
    """

    method: str
    path: str
    handler: Callable[[Any, Any], Awaitable[Any]]


#: What each group of host controls is needed for, for the health report.
_CAPABILITIES: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "the engine cannot be built or run: this Kiro Crew has no sandboxed process launch",
        (
            ("kiro_crew.sandbox", "sandboxed_spawn_argv"),
            ("kiro_crew.sandbox", "run_limited"),
        ),
    ),
    (
        "the engine install is refused: this Kiro Crew has no verified downloader",
        (("kiro_crew.asset_downloader", "download_to"),),
    ),
    (
        "every deck and library path is refused: this Kiro Crew has no sensitive-path gate",
        (
            ("kiro_crew.security", "is_sensitive_path"),
            ("kiro_crew.security", "path_contains_sensitive"),
        ),
    ),
    (
        "deck files and styles cannot be read: this Kiro Crew has no pinned file reader",
        (("kiro_crew.hooks", "safe_read_file_bytes_nolink"),),
    ),
    (
        "pictures inside slide previews are blanked: this Kiro Crew has no raster sniffer",
        (("kiro_crew.messaging.raster", "sniff_raster_mime"),),
    ),
)


def capability_issues(ctx: Any) -> list[str]:
    """Why this gateway serves PPTX Maker only in part; ``[]`` when it serves all of it."""
    issues: list[str] = []
    if sdk_level(ctx) < REQUIRED_SDK_LEVEL or not callable(getattr(ctx, "require_owner", None)):
        issues.append(
            "every change is refused: this Kiro Crew has no owner gate for apps "
            f"(App Kit level {sdk_level(ctx)}, PPTX Maker needs {REQUIRED_SDK_LEVEL})"
        )
    if not redaction_available_on(ctx):
        issues.append("deck content is withheld: this Kiro Crew has no redaction seam")
    for issue, names in _CAPABILITIES:
        if any(not callable(_host_attr(module, name)) for module, name in names):
            issues.append(issue)
    return issues


# ── the bound app context ───────────────────────────────────────────────────

_ctx: Any | None = None
_ctx_lock = threading.Lock()


def bind(ctx: Any) -> None:
    """Remember the context the gateway handed to ``register_routes``.

    PPTX Maker's work reaches the context from places no request passes through
    (the engine and icon-pack provisioning threads), so the context is held
    here. A later context replaces an earlier one: an update or a re-enable
    hands a fresh one.
    """
    global _ctx
    with _ctx_lock:
        _ctx = ctx


def bound() -> Any | None:
    with _ctx_lock:
        return _ctx


def data_dir() -> Path:
    """The app's data directory (``<data home>/apps/pptx-maker/data``).

    Raises when no context is bound rather than guessing a location: a guess
    would put the engine somewhere the gateway does not manage.
    """
    path = getattr(bound(), "data_dir", None)
    if path is None:
        raise RuntimeError("pptx-maker: no app context is bound")
    return Path(path)


def sdk_level(ctx: Any) -> int:
    """The seam level the gateway reports; 0 for a gateway that predates the field."""
    level = getattr(ctx, "sdk_level", 0)
    return level if isinstance(level, int) and not isinstance(level, bool) else 0


# ── owner gate ──────────────────────────────────────────────────────────────


async def require_owner(ctx: Any, request: Any, operation: str) -> Any | None:
    """``None`` when the caller is the dashboard owner, else the refusal to return.

    *operation* is bare (``"set_deck_root"``): the host prefixes the app name when
    it audits a denial. A gateway without the seam refuses everyone.
    """
    gate = getattr(ctx, "require_owner", None)
    if not callable(gate):
        from aiohttp import web

        return web.json_response(
            {"error": "owner authorization required", "code": "owner_only"}, status=403
        )
    return await gate(request, operation)


# ── outbound redaction ──────────────────────────────────────────────────────


def redact(text: str) -> str:
    """*text* through the gateway's active redaction policy.

    :data:`WITHHELD` when the text cannot be scanned: the context has no
    scrubber, or the scrubber raised (a host whose companion policy could not
    compose raises on purpose, and the answer to that is not to show the text
    unscanned). Every caller compares the result with its input before trusting
    it, so a withheld text also reads as "this held a credential" and is refused.
    """
    scrub = getattr(bound(), "scrub", None)
    outbound = getattr(scrub, "outbound", None)
    if not callable(outbound):
        return WITHHELD
    try:
        result = outbound(text)
    except Exception:  # noqa: BLE001 - fail closed: withhold, never forward unscanned
        logger.warning("pptx-maker: redaction failed; text withheld")
        return WITHHELD
    cleaned = getattr(result, "text", None)
    return cleaned if isinstance(cleaned, str) else WITHHELD


def redaction_available_on(ctx: Any) -> bool:
    scrub = getattr(ctx, "scrub", None)
    return callable(getattr(scrub, "outbound", None))


def redacted_credential_tag() -> str:
    """The marker the gateway writes where it removed a credential."""
    tag = _host_attr("kiro_crew.security", "REDACTED_CREDENTIAL_TAG")
    return tag if isinstance(tag, str) and tag else "[REDACTED: credential]"


# ── audit ───────────────────────────────────────────────────────────────────


def audit(operation: str, outcome: str, *, resources: str = "", error: str = "") -> None:
    """One security-event row through ``ctx.audit``. Fire-and-forget.

    *operation* is bare; the host records it as ``pptx-maker.<operation>``. Both
    free-text fields are redacted here first, then truncated, as the built-in did
    (truncating first could cut a credential in half so neither part matches).
    """
    sink = getattr(bound(), "audit", None)
    record = getattr(sink, "record", None)
    if not callable(record):
        logger.info("pptx-maker: %s %s (no audit sink)", operation, outcome)
        return
    try:
        record(
            operation,
            outcome,
            resources=redact(resources)[:200],
            error=redact(error)[:200] if error else "",
        )
    except Exception:  # noqa: BLE001 - auditing must never break the operation
        logger.debug("pptx-maker: audit emit failed", exc_info=True)


# ── sandboxed child processes ───────────────────────────────────────────────


class SandboxUnavailableError(RuntimeError):
    """No child was started: the gateway could not (or cannot) sandbox it.

    ``kind`` is the host's own classification when it refused
    (``"no_backend"``, ``"transient"``, ...), or ``"host_missing"`` when the
    gateway has no sandboxed spawn chain at all.
    """

    def __init__(self, message: str, *, kind: str = "host_missing") -> None:
        super().__init__(message)
        self.kind = kind


def _host_sandbox_errors() -> tuple[type[BaseException], ...]:
    err = _host_attr("kiro_crew.sandbox", "SandboxUnavailableError")
    return (err,) if isinstance(err, type) and issubclass(err, BaseException) else ()


def sandboxed_spawn_argv(
    argv: list[str], mode: str = "standard", **kwargs: Any
) -> tuple[list[str], dict[str, str], str | None]:
    """The gateway's sandbox chokepoint: ``(wrapped argv, scrubbed env, cleanup path)``.

    Raises :class:`SandboxUnavailableError` when the host refuses or lacks it.
    """
    impl = _host_attr("kiro_crew.sandbox", "sandboxed_spawn_argv")
    if not callable(impl):
        raise SandboxUnavailableError(NO_HOST_SANDBOX)
    try:
        return impl(argv, mode, **kwargs)
    except _host_sandbox_errors() as exc:
        raise SandboxUnavailableError(
            str(exc), kind=str(getattr(exc, "kind", "") or "no_backend")
        ) from exc


def run_limited(argv: Any, **kwargs: Any) -> Any:
    """``subprocess.run`` with the gateway's resource limits applied after ``exec``.

    Raises :class:`SandboxUnavailableError` on a gateway without it: an
    unlimited child is not run in its place.
    """
    impl = _host_attr("kiro_crew.sandbox", "run_limited")
    if not callable(impl):
        raise SandboxUnavailableError(NO_HOST_SANDBOX)
    return impl(argv, **kwargs)


def subprocess_executor() -> Any:
    """The gateway's bounded pool for subprocess-related blocking work.

    ``None`` (the loop's default pool) on a gateway without one.
    """
    impl = _host_attr("kiro_crew.executors", "subprocess_executor")
    return impl() if callable(impl) else None


def resolve_uv() -> str | None:
    """Absolute path to a usable ``uv``, or ``None``.

    The gateway's own resolver first, because the gateway renders the same value
    into the agents' ``{UV_BIN}`` and the two must agree. On a gateway without it,
    the same ladder: the ``uv`` wheel's locator, then an absolute ``PATH`` hit.
    """
    impl = _host_attr("kiro_crew.env", "resolve_uv")
    if callable(impl):
        found = impl()
        return found if isinstance(found, str) and found else None
    try:
        import uv  # type: ignore[import-not-found]

        located = uv.find_uv_bin()
        if located and os.path.isfile(located):
            return os.path.abspath(located)
    except Exception:  # noqa: BLE001 - an absent or broken wheel means "not this way"
        pass
    found = shutil.which("uv")
    return found if found and os.path.isabs(found) else None


# ── verified download ───────────────────────────────────────────────────────


def download_to(dest: Path, url: str, **kwargs: Any) -> tuple[bool, str]:
    """The gateway's sha256-verifying, https-only downloader: ``(ok, error)``.

    ``(False, reason)`` on a gateway without it; nothing is fetched by any other
    means.
    """
    impl = _host_attr("kiro_crew.asset_downloader", "download_to")
    if not callable(impl):
        return False, "this Kiro Crew has no verified downloader (update Kiro Crew)"
    return impl(dest, url, **kwargs)


# ── path and read gates ─────────────────────────────────────────────────────


def is_sensitive_path(path: str) -> bool:
    """The gateway's sensitive-path gate. ``True`` (refuse) when it has none."""
    impl = _host_attr("kiro_crew.security", "is_sensitive_path")
    if not callable(impl):
        return True
    return bool(impl(path))


def path_contains_sensitive(path: str) -> bool:
    """Whether *path* is an ancestor of a sensitive path. ``True`` when unknown."""
    impl = _host_attr("kiro_crew.security", "path_contains_sensitive")
    if not callable(impl):
        return True
    return bool(impl(path))


#: Leading bytes :func:`sniff_raster_mime` needs, the gateway's own value.
SNIFF_BYTES = 16


def sniff_raster_mime(head: bytes) -> str | None:
    """The raster type *head* starts with, per the gateway's shared sniffer.

    ``None`` (not a raster) on a gateway without it, so a ``data:image/...``
    blob is never exempted from the redaction pass on an unverified signature.
    """
    impl = _host_attr("kiro_crew.messaging.raster", "sniff_raster_mime")
    if not callable(impl):
        return None
    found = impl(head)
    return found if isinstance(found, str) else None


def aws_key_id_pattern() -> str | None:
    """The gateway's AWS access key id pattern, or ``None`` when it has none.

    ``None`` makes the caller treat every inline bitmap as credential-bearing,
    which blanks the picture rather than serving an unscanned body.
    """
    pattern = _host_attr("kiro_crew.credential_patterns", "AWS_KEY_ID")
    return pattern if isinstance(pattern, str) and pattern else None


class FileTooLargeError(Exception):
    """A read exceeded its byte cap (also raised for the host's own type)."""


def safe_read_file_bytes_nolink(path: str, within_root: str, **kwargs: Any) -> bytes | None:
    """The gateway's descriptor-pinned, link-refusing, contained file read.

    ``None`` for an absent or refused file, as the host answers, and also on a
    gateway without the reader: the file is treated as unreadable rather than
    read by name.
    """
    impl = _host_attr("kiro_crew.hooks", "safe_read_file_bytes_nolink")
    if not callable(impl):
        return None
    host_too_large = _host_attr("kiro_crew.hooks", "FileTooLargeError")
    too_large: tuple[type[BaseException], ...] = (
        (host_too_large,)
        if isinstance(host_too_large, type) and issubclass(host_too_large, BaseException)
        else ()
    )
    try:
        return impl(path, within_root=within_root, **kwargs)
    except too_large as exc:
        raise FileTooLargeError(str(exc)) from exc
