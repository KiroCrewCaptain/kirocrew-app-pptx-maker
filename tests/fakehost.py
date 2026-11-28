"""A stand-in for the Kiro Crew gateway, so the tests need no Kiro Crew install.

``backend/_host.py`` resolves every backend SDK name through one function,
``_host._host_attr(module, name)``. :class:`FakeHost` answers it from a table of
small, offline implementations keyed ``(BACKEND_SDK, name)``; a test that needs a
different host (one without the sandbox, one whose sandbox refuses, one at a
lower App Kit level) edits the table through the ``fake_host`` fixture.

These fakes stand in for the host's controls, they do not re-implement them: the
sandbox fake runs the argv unwrapped, the sensitive-path fake knows a handful of
names, the redaction and credential fakes know the credential shapes these tests
use. What the tests check is that PPTX Maker routes every spawn, download, path,
read and served text through the host and refuses when the host does. The
controls themselves are tested in Kiro Crew.

:class:`FakeCtx` is the ``AppContext`` the gateway hands the hooks at App Kit
level 20, with the fields PPTX Maker reads, ``verified_download`` among them.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator

from aiohttp import web

#: The package every lookup names.
BACKEND_SDK = "kiro_crew.apps.backend_sdk"

# ── sandbox ─────────────────────────────────────────────────────────────────


class SandboxUnavailableError(RuntimeError):
    """The host's own refusal type (the backend SDK's ``SandboxUnavailableError``)."""

    def __init__(self, message: str = "no sandbox backend", *, kind: str = "no_backend") -> None:
        super().__init__(message)
        self.kind = kind


@dataclass
class SpawnRecord:
    argv: list[str]
    mode: str
    kwargs: dict[str, Any]


#: The ``subprocess.run`` keywords the backend SDK's ``run_sandboxed`` forwards; any
#: other keyword is a ``TypeError`` there, so it is one here too.
RUN_KWARGS = frozenset(
    {
        "input",
        "capture_output",
        "timeout",
        "check",
        "cwd",
        "stdin",
        "stdout",
        "stderr",
        "text",
        "encoding",
        "errors",
        "universal_newlines",
        "bufsize",
    }
)


class FakeSandbox:
    """The host's sandboxed run: records each call, then runs the argv.

    ``wrap`` stands for the sandbox wrapper the host puts around the argv (none by
    default); ``refuse`` is raised before anything runs, as the host's preparation
    raises.
    """

    def __init__(self) -> None:
        self.calls: list[SpawnRecord] = []
        self.refuse: BaseException | None = None
        self.wrap: Callable[[list[str]], list[str]] = list

    def run_sandboxed(
        self,
        argv: list[str],
        *,
        mode: str = "standard",
        env: dict[str, str] | None = None,
        strip_python_env: bool = False,
        **run_kwargs: Any,
    ) -> subprocess.CompletedProcess:
        refused = sorted(set(run_kwargs) - RUN_KWARGS)
        if refused:
            raise TypeError(f"run_sandboxed does not accept {', '.join(refused)}")
        self.calls.append(
            SpawnRecord(
                list(argv), mode, dict(run_kwargs, env=env, strip_python_env=strip_python_env)
            )
        )
        if self.refuse is not None:
            raise self.refuse
        scrubbed = dict(env if env is not None else os.environ)
        return subprocess.run(  # noqa: S603 - test-only, fixed argv
            self.wrap(list(argv)), env=scrubbed, **run_kwargs
        )


# ── security ────────────────────────────────────────────────────────────────

#: Path components the fake's sensitive-path gate refuses wherever they appear.
SENSITIVE_COMPONENTS = frozenset({".ssh", ".aws", ".gnupg", ".local_secret", "sel_hmac.key"})


def is_sensitive_path(path: str, base_dir: str | None = None) -> bool:
    parts = {p.lower() for p in Path(path).parts}
    return bool(parts & SENSITIVE_COMPONENTS)


def contains_sensitive_path(dir_str: str) -> bool:
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


CREDENTIAL_PLACEHOLDER = "[REDACTED: credential]"

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
    out = _CREDENTIAL_RE.sub(CREDENTIAL_PLACEHOLDER, text)
    return _BARE_RUN_RE.sub(
        lambda m: CREDENTIAL_PLACEHOLDER if _looks_bare_secret(m.group(0)) else m.group(0), out
    )


def iter_credential_matches(text: str) -> Iterator[re.Match[str]]:
    """The host's credential scan: each credential-shaped run, with no bare-secret guess."""
    return _CREDENTIAL_RE.finditer(text)


class FileTooLargeError(Exception):
    """The host's ``FileTooLargeError`` (the backend SDK's)."""


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


def read_bytes_nofollow(
    raw: str,
    *,
    within_root: str | None = None,
    max_bytes: int | None = None,
    within_root_is_canonical: bool = False,
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


# ── content checks ──────────────────────────────────────────────────────────


#: The URLs :func:`contains_exfiltration_url` flags: a query long enough to carry data out.
_EXFIL_URL_RE = re.compile(r"https?://[^\s/?#]+[^\s?#]*\?[^\s#]{40,}")


def contains_exfiltration_url(text: str) -> bool:
    """The host's exfil scan, for the one shape these tests use: a long query."""
    return bool(_EXFIL_URL_RE.search(text))


def redact_url(url: str) -> str:
    """Scheme, host and port only (the host's shape)."""
    try:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        return "<unparseable url>"
    return f"{parsed.scheme}://{host}{port}"


# ── verified download ───────────────────────────────────────────────────────


class VerifiedDownloadError(OSError):
    """The host's ``VerifiedDownloadError``: nothing was installed."""


_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def verified_download(data_dir: Path, url: str, dest: str, *, sha256: str, size: int) -> Path:
    """``ctx.verified_download``: *dest* inside *data_dir*, installed only on an exact match.

    The same refusals the host makes before it opens anything (an https url, a
    64-hex digest, a positive size, a relative *dest* in a folder that exists), then
    a transfer into a staging file beside *dest* that is renamed onto it only when
    the length is *size* and the digest is *sha256*. Every failure raises
    :class:`VerifiedDownloadError` naming the url's host only, and leaves nothing.
    """
    if not isinstance(url, str) or not url.lower().startswith("https://"):
        raise ValueError("url must be an https:// URL")
    if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
        raise ValueError("sha256 must be the file's 64-character hex SHA-256 digest")
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise ValueError("size must be the file's exact length in bytes")
    relative = PurePosixPath(dest)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("dest must be a path relative to the app's data directory")
    folder = Path(data_dir).joinpath(*relative.parts[:-1])
    if not folder.is_dir() or folder.is_symlink():
        raise VerifiedDownloadError(
            "the destination folder must be a real folder inside the app's data directory"
        )
    target = folder / relative.name
    staging = folder / f".{secrets.token_hex(16)}.kc-dl"
    digest = hashlib.sha256()
    received = 0
    try:
        opener = urllib.request.build_opener()
        with opener.open(url, timeout=60) as response, open(staging, "xb") as out:
            while True:
                chunk = response.read(1 << 16)
                if not chunk:
                    break
                received += len(chunk)
                if received > size:
                    raise VerifiedDownloadError(f"the download is larger than {size} bytes")
                out.write(chunk)
                digest.update(chunk)
        if received < size:
            raise VerifiedDownloadError(f"downloaded file too small ({received} bytes)")
        got = digest.hexdigest()
        if got != sha256:
            raise VerifiedDownloadError(
                f"sha256 mismatch: got {got[:16]}…, expected {sha256[:16]}… (corrupt download)"
            )
        os.replace(staging, target)
        return target
    except VerifiedDownloadError:
        raise
    except Exception as exc:  # noqa: BLE001 - the host reports every transport failure
        raise VerifiedDownloadError(
            f"HTTPS download failed: {type(exc).__name__} from {redact_url(url)}"
        ) from None
    finally:
        staging.unlink(missing_ok=True)


# ── the host table ──────────────────────────────────────────────────────────


class FakeHost:
    """Answers ``_host._host_attr`` from :attr:`table`, a backend SDK at App Kit level 20.

    ``agent_template_host_values`` is left out on purpose: without it the app
    restates what an older gateway renders into ``{HOST_PATH}`` and ``{UV_BIN}``,
    which is what most tests pin. The tests of the gateway's own values add it.
    """

    def __init__(self) -> None:
        self.sandbox = FakeSandbox()
        self.table: dict[tuple[str, str], Any] = {
            (BACKEND_SDK, "SDK_LEVEL"): 20,
            (BACKEND_SDK, "run_sandboxed"): self.sandbox.run_sandboxed,
            (BACKEND_SDK, "SandboxUnavailableError"): SandboxUnavailableError,
            (BACKEND_SDK, "is_sensitive_path"): is_sensitive_path,
            (BACKEND_SDK, "contains_sensitive_path"): contains_sensitive_path,
            (BACKEND_SDK, "read_bytes_nofollow"): read_bytes_nofollow,
            (BACKEND_SDK, "FileTooLargeError"): FileTooLargeError,
            (BACKEND_SDK, "sniff_raster_mime"): sniff_raster_mime,
            (BACKEND_SDK, "CREDENTIAL_PLACEHOLDER"): CREDENTIAL_PLACEHOLDER,
            (BACKEND_SDK, "contains_exfiltration_url"): contains_exfiltration_url,
            (BACKEND_SDK, "iter_credential_matches"): iter_credential_matches,
            (BACKEND_SDK, "uv_executable"): lambda: "/opt/fake-host/bin/uv",
        }

    def attr(self, module: str, name: str) -> Any | None:
        return self.table.get((module, name))

    def remove(self, name: str) -> None:
        """Take the backend SDK name *name* away, as an older gateway lacks it."""
        self.table.pop((BACKEND_SDK, name), None)

    def set(self, name: str, value: Any) -> None:
        """Serve *value* as the backend SDK name *name*."""
        self.table[(BACKEND_SDK, name)] = value


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
    """The gateway's ``AppContext`` at App Kit level 20, as PPTX Maker reads it.

    ``require_owner`` is the host predicate: the configured owner, as a dashboard
    subject (an empty app claim). Everyone else gets the host's 403
    ``owner_only``, and the denial is audited as ``pptx-maker.<operation>``.
    ``verified_download`` is :func:`verified_download` into :attr:`data_dir`; each
    call is recorded in :attr:`downloads`, and :attr:`download_refusal` is raised
    instead when set.
    """

    data_dir: Path
    name: str = "pptx-maker"
    sdk_level: int = 20
    scrub: FakeScrub = field(default_factory=FakeScrub)
    audit: FakeAudit = field(default_factory=FakeAudit)
    health: FakeHealth = field(default_factory=FakeHealth)
    owner: str = OWNER
    denials: list[str] = field(default_factory=list)
    owner_calls: list[str] = field(default_factory=list)
    downloads: list[dict[str, Any]] = field(default_factory=list)
    download_refusal: BaseException | None = None

    def verified_download(self, url: str, dest: str, *, sha256: str, size: int) -> Path:
        self.downloads.append({"url": url, "dest": dest, "sha256": sha256, "size": size})
        if self.download_refusal is not None:
            raise self.download_refusal
        return verified_download(self.data_dir, url, dest, sha256=sha256, size=size)

    async def require_owner(
        self, request: web.Request, operation: str
    ) -> web.StreamResponse | None:
        self.owner_calls.append(operation)
        if request.get("user") == self.owner and "app" in request and request["app"] == "":
            return None
        self.denials.append(f"{self.name}.{operation}")
        return web.json_response({"error": "owner only", "code": "owner_only"}, status=403)
