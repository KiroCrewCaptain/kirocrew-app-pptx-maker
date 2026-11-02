"""A stand-in for the Kiro Crew gateway, so the tests need no Kiro Crew install.

``backend/_host.py`` resolves every host control through one function,
``_host._host_attr(module, name)``. :class:`FakeHost` answers it from a table of
small, offline implementations; a test that needs a different host (one without
the sandbox, one whose sandbox refuses) edits the table through the ``fake_host``
fixture.

These fakes stand in for the host's controls, they do not re-implement them: the
sandbox fake runs the argv unwrapped, the sensitive-path fake knows a handful of
names, the redaction fake knows the credential shapes these tests use. What the
tests check is that PPTX Maker routes every spawn, download, path, read and
served text through the host and refuses when the host does. The controls
themselves are tested in Kiro Crew.

:class:`FakeCtx` is the ``AppContext`` the gateway hands the hooks at App Kit
level 1, with the fields PPTX Maker reads.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiohttp import web

# ── sandbox ─────────────────────────────────────────────────────────────────


class SandboxUnavailableError(RuntimeError):
    """The host's own refusal type (``kiro_crew.sandbox.SandboxUnavailableError``)."""

    def __init__(self, message: str = "no sandbox backend", *, kind: str = "no_backend") -> None:
        super().__init__(message)
        self.kind = kind


@dataclass
class SpawnRecord:
    argv: list[str]
    mode: str
    kwargs: dict[str, Any]


class FakeSandbox:
    """The host's spawn chokepoint: records each call, returns the argv unwrapped."""

    def __init__(self) -> None:
        self.calls: list[SpawnRecord] = []
        self.refuse: BaseException | None = None

    def sandboxed_spawn_argv(
        self,
        argv: list[str],
        mode: str = "standard",
        *,
        env: dict[str, str] | None = None,
        **kw: Any,
    ) -> tuple[list[str], dict[str, str], str | None]:
        self.calls.append(SpawnRecord(list(argv), mode, dict(kw, env=env)))
        if self.refuse is not None:
            raise self.refuse
        return list(argv), dict(env if env is not None else os.environ), None


def run_limited(argv: Any, **kwargs: Any) -> subprocess.CompletedProcess:
    """``subprocess.run`` (the host adds resource limits after ``exec``)."""
    return subprocess.run(argv, **kwargs)  # noqa: S603 - test-only, fixed argv


# ── security ────────────────────────────────────────────────────────────────

#: Path components the fake's sensitive-path gate refuses wherever they appear.
SENSITIVE_COMPONENTS = frozenset({".ssh", ".aws", ".gnupg", ".local_secret", "sel_hmac.key"})


def is_sensitive_path(path: str, base_dir: str | None = None) -> bool:
    parts = {p.lower() for p in Path(path).parts}
    return bool(parts & SENSITIVE_COMPONENTS)


def path_contains_sensitive(dir_str: str, base_dir: str | None = None, **_kw: Any) -> bool:
    """True when a sensitive home location lies UNDER *dir_str* (the home itself, a parent)."""
    if not dir_str:
        return False
    try:
        root = os.path.realpath(os.path.expanduser(dir_str))
        home = os.path.realpath(os.path.expanduser("~"))
    except (OSError, ValueError):
        return True
    for name in (".ssh", ".aws", ".gnupg"):
        target = os.path.join(home, name)
        prefix = root.rstrip(os.sep) + os.sep
        if os.path.normcase(target).startswith(os.path.normcase(prefix)):
            return True
    return False


REDACTED_CREDENTIAL_TAG = "[REDACTED: credential]"

#: The credential shapes the gateway's redactor recognises that these tests use.
#: Marker tokens are matched at their real lengths, so a SHORT ``ghp_`` body
#: (which the host's redactor also ignores) is left alone, as the tests expect.
_CREDENTIAL_RE = re.compile(
    r"(?:AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}"
    r"|(?:gh[opsur]_)[A-Za-z0-9]{36}"
    r"|github_pat_[A-Za-z0-9_]{22,}"
    r"|glpat-[A-Za-z0-9_-]{20,}"
    r"|xox[bpas]-[A-Za-z0-9-]{10,}"
    r"|sk-ant-[A-Za-z0-9_-]{20,}"
    r"|sk-proj-[A-Za-z0-9_-]{20,}"
    r"|pypi-[A-Za-z0-9_-]{50,}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)

#: The bare-secret heuristic: a run of 40+ base64-alphabet characters mixing
#: case and digits reads as a raw secret key. A random raster's base64 always
#: contains one, which is why ``routes._redact_artifact`` excises real bitmaps.
_BARE_RUN_RE = re.compile(r"[A-Za-z0-9+/]{40,}")


def _looks_bare_secret(run: str) -> bool:
    return (
        any(c.islower() for c in run)
        and any(c.isupper() for c in run)
        and any(c.isdigit() for c in run)
    )


def fake_redact(text: str) -> str:
    out = _CREDENTIAL_RE.sub(REDACTED_CREDENTIAL_TAG, text)
    return _BARE_RUN_RE.sub(
        lambda m: REDACTED_CREDENTIAL_TAG if _looks_bare_secret(m.group(0)) else m.group(0), out
    )


class FileTooLargeError(Exception):
    """The host's ``kiro_crew.hooks.FileTooLargeError``."""


def fd_real_path(fd: int) -> str | None:
    """Where an open descriptor really is (``/proc/self/fd``, ``F_GETPATH``, Win32)."""
    try:
        if sys.platform.startswith("linux"):
            return os.readlink(f"/proc/self/fd/{fd}")
        if sys.platform == "darwin":
            import fcntl

            buf = fcntl.fcntl(fd, getattr(fcntl, "F_GETPATH", 50), b"\0" * 1024)
            return os.fsdecode(buf.split(b"\0", 1)[0])
        if sys.platform == "win32":
            import ctypes
            import msvcrt
            from ctypes import wintypes

            handle = msvcrt.get_osfhandle(fd)
            get = ctypes.windll.kernel32.GetFinalPathNameByHandleW  # type: ignore[attr-defined]
            get.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
            get.restype = wintypes.DWORD
            buf = ctypes.create_unicode_buffer(32768)
            n = get(handle, buf, 32768, 0)
            if not n:
                return None
            text = buf.value
            if text.startswith("\\\\?\\UNC\\"):
                return "\\\\" + text[8:]
            if text.startswith("\\\\?\\"):
                return text[4:]
            return text
    except OSError:
        return None
    return None


def safe_read_file_bytes_nolink(
    raw: str, within_root: str | None = None, *, max_bytes: int | None = None, **_kw: Any
) -> bytes | None:
    """No-follow, hard-link-refusing, contained (by the OPENED descriptor), capped read."""
    try:
        fd = os.open(raw, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:
            return None
        if within_root is not None:
            real = fd_real_path(fd)
            if real is None:
                return None
            root = os.path.realpath(within_root)
            try:
                if os.path.commonpath(
                    [os.path.normcase(real), os.path.normcase(root)]
                ) != os.path.normcase(root):
                    return None
            except ValueError:
                return None
        if is_sensitive_path(raw):
            return None
        if max_bytes is not None and st.st_size > max_bytes:
            raise FileTooLargeError(raw)
        chunks = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


# ── raster sniffing ─────────────────────────────────────────────────────────

_MAGIC: dict[str, tuple[bytes, ...]] = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (b"RIFF",),
    "image/bmp": (b"BM",),
}


def sniff_raster_mime(head: bytes) -> str | None:
    for mime, prefixes in _MAGIC.items():
        if any(head.startswith(p) for p in prefixes):
            if mime == "image/webp" and head[8:12] != b"WEBP":
                continue
            return mime
    return None


AWS_KEY_ID = "(?:AKIA|ASIA)[A-Z0-9]{16}"


# ── verified downloader ─────────────────────────────────────────────────────


def redact_url(url: str) -> str:
    """Scheme, host and port only (the host's shape)."""
    try:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        return "<unparseable url>"
    return f"{parsed.scheme}://{host}{port}"


def download_to(
    archive: Path,
    url: str,
    *,
    sha256: str,
    timeout_secs: float,
    chunk_bytes: int,
    min_bytes: int = 0,
    allow_cross_host_redirects: bool = False,
    label: str = "",
    **_kw: Any,
) -> tuple[bool, str]:
    """Stream *url* beside *archive*, hash it, rename only on a digest match. Never raises."""
    staging = archive.with_name(archive.name + ".part")
    digest = hashlib.sha256()
    size = 0
    try:
        opener = urllib.request.build_opener()
        with opener.open(url, timeout=timeout_secs) as response, open(staging, "wb") as out:
            while True:
                chunk = response.read(chunk_bytes)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        if size < min_bytes:
            staging.unlink(missing_ok=True)
            return False, f"download too small ({size} bytes) from {redact_url(url)}"
        if digest.hexdigest() != sha256:
            staging.unlink(missing_ok=True)
            return False, f"sha256 mismatch (expected {sha256[:16]}…) from {redact_url(url)}"
        os.replace(staging, archive)
        return True, ""
    except Exception as exc:  # noqa: BLE001 - the host's downloader never raises
        staging.unlink(missing_ok=True)
        return False, f"download failed ({type(exc).__name__}) from {redact_url(url)}"


# ── the host table ──────────────────────────────────────────────────────────


class FakeHost:
    """Answers ``_host._host_attr`` from :attr:`table`."""

    def __init__(self) -> None:
        self.sandbox = FakeSandbox()
        self.table: dict[tuple[str, str], Any] = {
            ("kiro_crew.sandbox", "sandboxed_spawn_argv"): self.sandbox.sandboxed_spawn_argv,
            ("kiro_crew.sandbox", "run_limited"): run_limited,
            ("kiro_crew.sandbox", "SandboxUnavailableError"): SandboxUnavailableError,
            ("kiro_crew.executors", "subprocess_executor"): lambda: None,
            ("kiro_crew.security", "is_sensitive_path"): is_sensitive_path,
            ("kiro_crew.security", "path_contains_sensitive"): path_contains_sensitive,
            ("kiro_crew.security", "REDACTED_CREDENTIAL_TAG"): REDACTED_CREDENTIAL_TAG,
            ("kiro_crew.hooks", "safe_read_file_bytes_nolink"): safe_read_file_bytes_nolink,
            ("kiro_crew.hooks", "FileTooLargeError"): FileTooLargeError,
            ("kiro_crew.messaging.raster", "sniff_raster_mime"): sniff_raster_mime,
            ("kiro_crew.credential_patterns", "AWS_KEY_ID"): AWS_KEY_ID,
            ("kiro_crew.asset_downloader", "download_to"): download_to,
            ("kiro_crew.env", "resolve_uv"): lambda: "/opt/fake-host/bin/uv",
        }

    def attr(self, module: str, name: str) -> Any | None:
        return self.table.get((module, name))

    def remove(self, module: str, name: str) -> None:
        self.table.pop((module, name), None)


# ── the app context ─────────────────────────────────────────────────────────


@dataclass
class ScrubResult:
    text: str
    credentials_removed: int = 0
    urls_removed: int = 0
    redacted: bool = False


class FakeScrub:
    """``ctx.scrub``: redacts the credential shapes the tests use; ``raises`` fails it."""

    def __init__(self) -> None:
        self.calls = 0
        self.raises: BaseException | None = None

    def outbound(self, text: str) -> ScrubResult:
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        out = fake_redact(text)
        return ScrubResult(out, redacted=out != text)


class FakeAudit:
    def __init__(self) -> None:
        self.rows: list[dict[str, str]] = []

    def record(self, operation: str, outcome: str, *, resources: str = "", error: str = "") -> None:
        self.rows.append(
            {
                "operation": f"pptx-maker.{operation}",
                "outcome": outcome,
                "resources": resources,
                "error": error,
            }
        )


class FakeHealth:
    def __init__(self) -> None:
        self.status = "healthy"
        self.issues: list[str] = []

    def mark_degraded(self, issue: str) -> None:
        self.status = "degraded"
        self.issues.append(issue)


OWNER = "owner-user"


@dataclass
class FakeCtx:
    """The gateway's ``AppContext`` at App Kit level 1, as PPTX Maker reads it.

    ``require_owner`` is the host predicate: the configured owner, as a dashboard
    subject (an empty app claim). Everyone else gets the host's 403
    ``owner_only``, and the denial is audited as ``pptx-maker.<operation>``.
    """

    data_dir: Path
    name: str = "pptx-maker"
    sdk_level: int = 1
    scrub: FakeScrub = field(default_factory=FakeScrub)
    audit: FakeAudit = field(default_factory=FakeAudit)
    health: FakeHealth = field(default_factory=FakeHealth)
    owner: str = OWNER
    denials: list[str] = field(default_factory=list)
    owner_calls: list[str] = field(default_factory=list)

    async def require_owner(
        self, request: web.Request, operation: str
    ) -> web.StreamResponse | None:
        self.owner_calls.append(operation)
        if request.get("user") == self.owner and "app" in request and request["app"] == "":
            return None
        self.denials.append(f"{self.name}.{operation}")
        return web.json_response({"error": "owner only", "code": "owner_only"}, status=403)
