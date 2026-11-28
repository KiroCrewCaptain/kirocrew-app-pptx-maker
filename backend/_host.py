"""Every place PPTX Maker reaches into the Kiro Crew gateway, in one module.

An external app's Python runs inside the gateway process. PPTX Maker needs two
kinds of host surface:

1. **The App Kit context** handed to its hooks (``ctx``): the data directory,
   the owner gate (``ctx.require_owner``), the audit log (``ctx.audit``),
   outbound redaction (``ctx.scrub``) and, from App Kit level 20, the verified
   download (``ctx.verified_download``). These are public seams; each is read off
   the context with ``getattr`` and has a fallback that is never MORE permissive
   than the host: no owner gate refuses everyone, no scrubber withholds the
   text, no audit sink logs locally, no verified download fetches nothing.

2. **The public backend SDK** (``kiro_crew.apps.backend_sdk``), for the security
   controls no context seam carries. PPTX Maker downloads a third-party engine,
   builds it with ``uv`` and runs it over model-authored deck content, so every
   child goes through the gateway's OS sandbox, every path through its
   sensitive-path gate, every agent-written file through its no-follow reader and
   every served text through its exfil and credential scans. Copying those
   controls into this repository would leave a security control that looks
   present and drifts from the one the gateway enforces, so they are looked up in
   the running gateway instead, and only here (``tests/test_no_host_imports.py``
   pins that). Each lookup is lazy and runs at call time through
   :func:`_backend_sdk_attr`, which also checks the App Kit level the name arrived
   at; when the gateway does not provide one, the operation that needs it is
   REFUSED (nothing is spawned, a path reads as sensitive, a file reads as
   unreadable, a picture is blanked), never run without it.

No private ``kiro_crew`` name is reached. Everything else PPTX Maker used from the
gateway is either replaced by the two surfaces above or restated in
``_compat.py``, ``_fs.py`` and ``_os_deps.py`` (small helpers that carry no
security decision).
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
    Kiro Crew install. Only :data:`BACKEND_SDK` is ever asked.
    """
    try:
        mod = importlib.import_module(module)
    except Exception:  # noqa: BLE001 - any import failure means "not provided"
        return None
    return getattr(mod, name, None)


#: The gateway's public backend SDK package (App Kit level 7 and later).
BACKEND_SDK = "kiro_crew.apps.backend_sdk"

#: The App Kit level that publishes the backend SDK package and its first names:
#: the sandboxed run, the sensitive-path checks and the no-follow read.
BACKEND_SDK_LEVEL = 7

#: The App Kit level that added the backend SDK's ``sniff_raster_mime``.
RASTER_SNIFF_SDK_LEVEL = 14

#: The App Kit level that added the backend SDK's content checks:
#: ``contains_exfiltration_url``, ``iter_credential_matches`` and
#: ``CREDENTIAL_PLACEHOLDER``.
CONTENT_CHECK_SDK_LEVEL = 17

#: The App Kit level that added the backend SDK's host tool lookups:
#: ``uv_executable`` and ``agent_template_host_values``.
HOST_TOOLS_SDK_LEVEL = 18

#: The App Kit level that added ``ctx.verified_download``.
VERIFIED_DOWNLOAD_SDK_LEVEL = 20


def _backend_sdk_attr(name: str, level: int) -> Any | None:
    """``name`` from the gateway's public backend SDK, or ``None`` below *level*.

    The package exists from App Kit level 7 and a name added later carries its own
    level, so the package's ``SDK_LEVEL`` is checked as well as the name itself:
    a gateway that reports a lower level, or none, is treated as not providing it.
    """
    sdk_level = _host_attr(BACKEND_SDK, "SDK_LEVEL")
    if not isinstance(sdk_level, int) or isinstance(sdk_level, bool) or sdk_level < level:
        return None
    return _host_attr(BACKEND_SDK, name)


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


#: What each group of backend SDK names is needed for, for the health report: the
#: issue, then each ``(name, App Kit level)`` the group needs.
_CAPABILITIES: tuple[tuple[str, tuple[tuple[str, int], ...]], ...] = (
    (
        "the engine cannot be built or run: this Kiro Crew has no sandboxed process launch",
        (("run_sandboxed", BACKEND_SDK_LEVEL),),
    ),
    (
        "every deck and library path is refused: this Kiro Crew has no sensitive-path gate",
        (
            ("is_sensitive_path", BACKEND_SDK_LEVEL),
            ("contains_sensitive_path", BACKEND_SDK_LEVEL),
        ),
    ),
    (
        "deck files and styles cannot be read: this Kiro Crew has no pinned file reader",
        (("read_bytes_nofollow", BACKEND_SDK_LEVEL),),
    ),
    (
        "deck links, deck ids and slide slugs are refused: this Kiro Crew has no exfil scan "
        f"(App Kit level {CONTENT_CHECK_SDK_LEVEL} or later provides it)",
        (("contains_exfiltration_url", CONTENT_CHECK_SDK_LEVEL),),
    ),
    (
        "pictures inside slide previews are blanked: this Kiro Crew has no credential scan "
        f"for them (App Kit level {CONTENT_CHECK_SDK_LEVEL} or later provides it)",
        (("iter_credential_matches", CONTENT_CHECK_SDK_LEVEL),),
    ),
)

#: Shown when the context has no verified download (App Kit level below 20).
NO_VERIFIED_DOWNLOAD = (
    "the engine install is refused: this Kiro Crew has no verified download "
    f"(App Kit level {VERIFIED_DOWNLOAD_SDK_LEVEL} or later provides it)"
)

#: Shown when the backend SDK has no raster sniffer (App Kit level below 14).
NO_RASTER_SNIFFER = (
    "pictures inside slide previews are blanked: this Kiro Crew has no raster sniffer "
    f"(App Kit level {RASTER_SNIFF_SDK_LEVEL} or later provides it)"
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
        if any(not callable(_backend_sdk_attr(name, level)) for name, level in names):
            issues.append(issue)
    if not callable(getattr(ctx, "verified_download", None)):
        issues.append(NO_VERIFIED_DOWNLOAD)
    if _raster_sniffer() is None:
        issues.append(NO_RASTER_SNIFFER)
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


def refuses(text: str) -> bool:
    """Whether a gate refuses *text*: the redaction changes it, or the exfil scan flags it.

    The comparison alone was the decision while the gateway's redaction rewrote a
    data-carrying URL whole; a gateway that masks only credential values leaves such
    a URL as written, so the gateway's own exfil scan (the backend SDK's
    ``contains_exfiltration_url``, App Kit level 17) decides it too. Fails closed: a
    gateway without the scan, or a scan that raises, refuses.
    """
    if redact(text) != text:
        return True
    scan = _backend_sdk_attr("contains_exfiltration_url", CONTENT_CHECK_SDK_LEVEL)
    if not callable(scan):
        return True
    try:
        return bool(scan(text))
    except Exception:  # noqa: BLE001 - fail closed: refuse, never pass unscanned
        logger.warning("pptx-maker: the exfil scan failed; refused")
        return True


def redaction_available_on(ctx: Any) -> bool:
    scrub = getattr(ctx, "scrub", None)
    return callable(getattr(scrub, "outbound", None))


def redacted_credential_tag() -> str:
    """The marker the gateway writes where it removed a credential.

    The backend SDK's ``CREDENTIAL_PLACEHOLDER`` (App Kit level 17); the same
    literal on a gateway without it.
    """
    tag = _backend_sdk_attr("CREDENTIAL_PLACEHOLDER", CONTENT_CHECK_SDK_LEVEL)
    return tag if isinstance(tag, str) and tag else "[REDACTED: credential]"


def holds_credential(text: str) -> bool:
    """Whether the gateway's credential scan finds a credential in *text*.

    The backend SDK's ``iter_credential_matches`` (App Kit level 17), which matches
    more than the redaction masks (an AWS access key id among them). ``True``
    (treat it as holding one) on a gateway without it, or when the scan raises, so
    the caller drops the text rather than serve it unscanned.
    """
    scan = _backend_sdk_attr("iter_credential_matches", CONTENT_CHECK_SDK_LEVEL)
    if not callable(scan):
        return True
    try:
        return next(iter(scan(text)), None) is not None
    except Exception:  # noqa: BLE001 - fail closed: treat as credential-bearing
        logger.warning("pptx-maker: the credential scan failed; treated as a credential")
        return True


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
    gateway has no sandboxed run at all.
    """

    def __init__(self, message: str, *, kind: str = "host_missing") -> None:
        super().__init__(message)
        self.kind = kind


def _host_sandbox_errors() -> tuple[type[BaseException], ...]:
    err = _backend_sdk_attr("SandboxUnavailableError", BACKEND_SDK_LEVEL)
    return (err,) if isinstance(err, type) and issubclass(err, BaseException) else ()


def run_sandboxed(
    argv: list[str], *, mode: str = "standard", strip_python_env: bool = False, **run_kwargs: Any
) -> Any:
    """Run *argv* to completion in the gateway's OS sandbox, under its resource limits.

    The backend SDK's ``run_sandboxed``: the gateway's spawn chokepoint (a
    credential-scrubbed environment, the sandbox tier *mode*), then its resource
    limits applied after ``exec``, then its cleanup, in one call. *run_kwargs* are
    the ``subprocess.run`` keywords it forwards (``cwd``, ``capture_output``,
    ``timeout``, ``text``, ...). Returns the ``subprocess.CompletedProcess``.

    Raises :class:`SandboxUnavailableError` when the host refuses the spawn or has
    no sandboxed run: an unsandboxed or unlimited child is not run in its place.
    Errors from the run itself (``OSError``, ``subprocess.SubprocessError``)
    propagate unchanged.
    """
    impl = _backend_sdk_attr("run_sandboxed", BACKEND_SDK_LEVEL)
    if not callable(impl):
        raise SandboxUnavailableError(NO_HOST_SANDBOX)
    try:
        return impl(argv, mode=mode, strip_python_env=strip_python_env, **run_kwargs)
    except _host_sandbox_errors() as exc:
        raise SandboxUnavailableError(
            str(exc), kind=str(getattr(exc, "kind", "") or "no_backend")
        ) from exc


# ── host tools and agent-template values ────────────────────────────────────


def resolve_uv() -> str | None:
    """Absolute path to a usable ``uv``, or ``None``.

    The gateway's own resolver first (the backend SDK's ``uv_executable``, App Kit
    level 18), because the gateway renders the same value into the agents'
    ``{UV_BIN}`` and the two must agree. On a gateway without it, the same ladder:
    the ``uv`` wheel's locator, then an absolute ``PATH`` hit.
    """
    impl = _backend_sdk_attr("uv_executable", HOST_TOOLS_SDK_LEVEL)
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


#: The two agent-template placeholders whose rendered value this app restates.
_HOST_PATH = "{HOST_PATH}"
_UV_BIN = "{UV_BIN}"


def _template_host_values() -> dict[str, Any] | None:
    """What the gateway renders for its host placeholders, or ``None`` when it does not say.

    The backend SDK's ``agent_template_host_values`` (App Kit level 18), the
    function the gateway's renderer itself calls. A value the host cannot compute
    is left out of it. ``None`` on a gateway without it, and for an answer that is
    not a mapping: the caller then restates what an older gateway renders.
    """
    impl = _backend_sdk_attr("agent_template_host_values", HOST_TOOLS_SDK_LEVEL)
    if not callable(impl):
        return None
    values = impl()
    return values if isinstance(values, dict) else None


def template_host_path() -> str | None:
    """The gateway's ``{HOST_PATH}`` value, or ``None`` on a gateway that does not offer it.

    ``""`` when the gateway offers its values but computes no ``{HOST_PATH}``: it
    then renders none and registers none of the agents. ``None`` means a gateway
    that does not say what it renders, which renders its own ``PATH``; the caller
    restates that (:func:`.provision.host_path`). A value that is not a string reads
    as ``None`` too.
    """
    values = _template_host_values()
    if values is None:
        return None
    value = values.get(_HOST_PATH, "")
    return value if isinstance(value, str) else None


def template_uv_bin() -> str | None:
    """The gateway's ``{UV_BIN}`` value, or :func:`resolve_uv` on a gateway without it.

    A gateway that offers its values renders the ``uv`` it resolves, so an update
    that moves the install does not change what the agents name. An older gateway
    renders :func:`resolve_uv` as it is.
    """
    values = _template_host_values()
    if values is None:
        return resolve_uv()
    found = values.get(_UV_BIN)
    return found if isinstance(found, str) and found else None


# ── verified download ───────────────────────────────────────────────────────


def verified_download(dest: Path, url: str, *, sha256: str, size: int) -> tuple[bool, str]:
    """Fetch *url* to *dest* through the context's verified download: ``(ok, error)``.

    ``ctx.verified_download`` (App Kit level 20): https only, the certificate
    verified, a redirect followed only on the same host, and nothing visible at
    *dest* until its length is *size* and its sha256 is *sha256*. It writes only
    inside the app's data directory, so *dest* must be there and its folder must
    already exist. Blocking: call it off the event loop.

    ``(False, reason)`` on a context without it, for a *dest* outside the data
    directory, and when the gateway installed nothing; nothing is fetched by any
    other means.
    """
    fetch = getattr(bound(), "verified_download", None)
    if not callable(fetch):
        return False, (
            "this Kiro Crew has no verified download "
            f"(App Kit level {VERIFIED_DOWNLOAD_SDK_LEVEL} or later provides it; update Kiro Crew)"
        )
    try:
        relative = Path(dest).relative_to(data_dir())
    except (RuntimeError, ValueError):
        return False, "the download must land inside the app's data directory"
    try:
        fetch(url, relative.as_posix(), sha256=sha256, size=size)
    except (ValueError, TypeError, OSError) as exc:
        # The gateway's wording names a host and a local folder, never the url's
        # path, query or a credential.
        return False, str(exc) or type(exc).__name__
    return True, ""


# ── path and read gates ─────────────────────────────────────────────────────


def is_sensitive_path(path: str) -> bool:
    """The gateway's sensitive-path gate. ``True`` (refuse) when it has none."""
    impl = _backend_sdk_attr("is_sensitive_path", BACKEND_SDK_LEVEL)
    if not callable(impl):
        return True
    return bool(impl(path))


def contains_sensitive_path(path: str) -> bool:
    """Whether *path* is an ancestor of a sensitive path. ``True`` when unknown.

    The backend SDK's ``contains_sensitive_path``.
    """
    impl = _backend_sdk_attr("contains_sensitive_path", BACKEND_SDK_LEVEL)
    if not callable(impl):
        return True
    return bool(impl(path))


def _raster_sniffer() -> Callable[[bytes], Any] | None:
    """The backend SDK's ``sniff_raster_mime``, or ``None`` on a gateway below level 14."""
    impl = _backend_sdk_attr("sniff_raster_mime", RASTER_SNIFF_SDK_LEVEL)
    return impl if callable(impl) else None


def sniff_raster_mime(head: bytes) -> str | None:
    """The raster type *head* starts with, per the gateway's own sniffer.

    The public backend SDK's ``sniff_raster_mime`` (App Kit level 14), which
    decides from the leading bytes alone. *head* is the whole candidate blob: the
    sniffer needs at least its ``RASTER_SNIFF_BYTES`` leading bytes, and a longer
    buffer answers the same, so no window is copied here. ``None`` (not a raster)
    on a gateway without it, so a ``data:image/...`` blob is never exempted from
    the redaction pass on an unverified signature.
    """
    impl = _raster_sniffer()
    if impl is None:
        return None
    found = impl(head)
    return found if isinstance(found, str) else None


class FileTooLargeError(Exception):
    """A read exceeded its byte cap (also raised for the host's own type)."""


def read_bytes_nofollow(path: str, within_root: str, **kwargs: Any) -> bytes | None:
    """The gateway's descriptor-pinned, link-refusing, contained file read.

    The backend SDK's ``read_bytes_nofollow``. ``None`` for an absent or refused
    file, as the host answers, and also on a gateway without the reader: the file
    is treated as unreadable rather than read by name.
    """
    impl = _backend_sdk_attr("read_bytes_nofollow", BACKEND_SDK_LEVEL)
    if not callable(impl):
        return None
    host_too_large = _backend_sdk_attr("FileTooLargeError", BACKEND_SDK_LEVEL)
    too_large: tuple[type[BaseException], ...] = (
        (host_too_large,)
        if isinstance(host_too_large, type) and issubclass(host_too_large, BaseException)
        else ()
    )
    try:
        return impl(path, within_root=within_root, **kwargs)
    except too_large as exc:
        raise FileTooLargeError(str(exc)) from exc
