#!/usr/bin/env python3
"""Capture README media for a Kiro Crew app from a real, isolated gateway.

What it does, in order:

1. Boots an ISOLATED Kiro Crew gateway from a Kiro Crew checkout you name
   (``--kirocrew``). The gateway is started by that checkout's own test harness
   (``kiro_crew.testing.harness.spawn_feature_gateway``): a throwaway
   ``KIROCREW_HOME`` under the system temp directory, a free loopback port and
   approval mode ``reads``. Scenes marked ``"live": true`` (the chats) run
   against a REAL model through your installed kiro-cli and its existing login
   (checked before anything is captured; there is no scripted fallback); every
   other scene runs against the packaged stub ACP backend
   (``capture/stub_backend.py``). Your own ``~/.kiro/crew`` is never read or
   written. The stub screenshots, the live screenshots and the walkthrough each
   get their OWN freshly seeded gateway, so none shows what another clicked.
2. Signs a fresh headless Chromium in with the gateway's one-time token and
   runs the ``setup`` HTTP calls from ``scenes.json`` -- from inside the page,
   with ``fetch``, exactly the way the dashboard calls the app's API -- to seed
   fictional demo data.
3. Takes every screenshot in ``scenes.json`` in each requested theme at
   1440x900 and device scale 2, then downscales it with ffmpeg so it stays
   inside the PNG budget.
4. Records the ``walkthrough`` with Playwright's video recorder and
   converts it with ffmpeg to an H.264 MP4 and a palette-optimized GIF.
5. Writes ``media.json`` (file list, sizes, alt text, provenance) beside the
   media, shuts the gateway down and deletes its temporary home.

Dependencies: the Python standard library, the ``playwright`` package with its
Chromium build, and an ``ffmpeg`` binary. See ``capture/README.md``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

IS_WINDOWS = os.name == "nt"

# ── Budgets and fixed capture geometry ──

VIEWPORT = {"width": 1440, "height": 900}
DEVICE_SCALE = 2
PNG_BUDGET = 1_500_000  # bytes, per PNG
GIF_BUDGET = 8_000_000
MP4_BUDGET = 10_000_000
# Downscale targets tried in order until a PNG fits its budget. 1600 px keeps
# text crisp on a README while staying under the 2000 px edge some image
# viewers (and AI reviewers) refuse.
PNG_WIDTHS = (1600, 1440, 1280)
# (width, fps) pairs tried in order until the GIF fits its budget.
GIF_LADDER = ((960, 10), (880, 8), (800, 8), (720, 6), (640, 5))
MP4_CRFS = (26, 30, 34)

READY_PREFIX = "CAPTURE_GATEWAY:"
# Printed by the bootstrap only after the harness's own teardown returned, i.e.
# the gateway is stopped and its temporary home deleted.
STOPPED_LINE = "CAPTURE_GATEWAY_STOPPED"
GATEWAY_BOOT_TIMEOUT = 180.0
TEARDOWN_GRACE = 30.0

# Text that must never be on screen when a frame is taken. A scene adds its own
# with ``forbid_text``; matching is a case-sensitive substring of the page's
# rendered text (``document.body.innerText``).
DEFAULT_FORBIDDEN_TEXT = (
    "Something went wrong",
    "Session expired",
    "Unauthorized",
    "Internal Server Error",
    "Traceback (most recent call last)",
    "Failed to fetch",
)
# A frame is taken only once none of these is visible: a spinner says "still
# loading", which a README picture must never say. ``.animate-spin`` is the
# class every Kiro Crew loading spinner carries.
DEFAULT_SETTLE_HIDDEN = (".animate-spin",)
SETTLE_HIDDEN_TIMEOUT_MS = 45_000

# The stand-in pointer drawn into recordings. Headless Chromium paints no mouse
# cursor, so without it a viewer sees things change with no visible cause.
CURSOR_SCRIPT = r"""
(() => {
  if (window.__captureCursor) return;
  window.__captureCursor = true;
  const install = () => {
    const dot = document.createElement('div');
    dot.id = '__capture_cursor';
    Object.assign(dot.style, {
      position: 'fixed', left: '-40px', top: '-40px', width: '22px', height: '22px',
      marginLeft: '-11px', marginTop: '-11px', borderRadius: '50%',
      background: 'rgba(124, 58, 237, 0.35)', border: '2px solid rgba(124, 58, 237, 0.95)',
      boxShadow: '0 0 0 2px rgba(255,255,255,0.85)', pointerEvents: 'none',
      zIndex: '2147483647', transition: 'transform 120ms ease-out',
    });
    document.documentElement.appendChild(dot);
    window.addEventListener('mousemove', (e) => {
      dot.style.left = e.clientX + 'px'; dot.style.top = e.clientY + 'px';
      dot.style.opacity = '1';
    }, true);
    // Like a real pointer, it hides while you type, so it never covers the text.
    window.addEventListener('keydown', () => { dot.style.opacity = '0'; }, true);
    window.addEventListener('mousedown', () => { dot.style.transform = 'scale(0.7)'; }, true);
    window.addEventListener('mouseup', () => { dot.style.transform = 'scale(1)'; }, true);
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', install);
  } else {
    install();
  }
})();
"""

# A caption pill drawn over the recording. ``position`` is "bottom" (default)
# or "top", ``offset`` its distance in px from that edge. ``over`` (a selector)
# centres the pill on that element and keeps it inside its width, so a caption
# in a chat stays over the conversation instead of the session list; ``above``
# (a selector) sits the pill ``offset`` px (default 14) above that element, e.g.
# the message box.
CAPTION_JS = r"""
([text, position, offset, over, above]) => {
  let el = document.getElementById('__capture_caption');
  if (!text) { if (el) el.remove(); return; }
  if (!el) {
    el = document.createElement('div');
    el.id = '__capture_caption';
    Object.assign(el.style, {
      position: 'fixed', transform: 'translateX(-50%)', width: 'max-content',
      padding: '12px 22px', borderRadius: '999px',
      background: 'rgba(17, 17, 27, 0.9)', color: '#fff',
      font: '600 22px/1.35 system-ui, -apple-system, Segoe UI, sans-serif',
      boxShadow: '0 6px 24px rgba(0,0,0,0.25)', pointerEvents: 'none',
      zIndex: '2147483646', textAlign: 'center',
    });
    document.documentElement.appendChild(el);
  }
  const box = (sel) => {
    if (!sel) return null;
    const node = document.querySelector(sel);
    return node ? node.getBoundingClientRect() : null;
  };
  const area = box(over);
  el.style.left = area ? (area.left + area.width / 2) + 'px' : '50%';
  el.style.maxWidth = area ? Math.max(area.width - 48, 240) + 'px' : '80%';
  const anchor = box(above);
  if (anchor) {
    el.style.top = '';
    el.style.bottom = (window.innerHeight - anchor.top + (offset ?? 14)) + 'px';
  } else if (position === 'top') {
    el.style.bottom = '';
    el.style.top = (offset ?? 104) + 'px';
  } else {
    el.style.top = '';
    el.style.bottom = (offset ?? 28) + 'px';
  }
  el.textContent = text;
}
"""

# Run by the Kiro Crew checkout's own interpreter. It owns the gateway through
# the checkout's harness context manager, so the harness's own teardown (process
# tree kill, then removal of the temporary home) runs when this script's stdin
# closes -- which is how the capture process asks it to stop.
BOOTSTRAP = r"""
import json, os, sys
STOPPED_LINE = "CAPTURE_GATEWAY_STOPPED"
fixture, backend = sys.argv[1], sys.argv[2]
# The program the gateway runs as its "kiro-cli": either the stub ACP backend
# (capture/stub_backend.py through the launcher capture.py wrote: no model, no
# credentials, no network) or, for a scene marked "live", the operator's own
# installed kiro-cli, which signs in with its own existing login.
os.environ["KIROCREW_KIRO_BIN"] = backend
from kiro_crew.testing.harness import spawn_feature_gateway
with spawn_feature_gateway(fixture=fixture, approval="reads") as gw:
    print("CAPTURE_GATEWAY:" + json.dumps({
        "port": gw.port, "token": gw.token, "home": str(gw.home), "pid": gw.proc.pid,
    }), flush=True)
    sys.stdin.read()
print(STOPPED_LINE, flush=True)
"""


class CaptureError(RuntimeError):
    """A capture that must not produce media: the run stops and says why."""


def log(message: str) -> None:
    print(f"[capture] {message}", flush=True)


# ── Configuration ──


def load_scenes(path: Path) -> dict[str, Any]:
    if not path.is_file() and (path.parent / "scenes.example.json").is_file():
        raise CaptureError(
            f"{path} does not exist: copy scenes.example.json to scenes.json and fill it in"
        )
    try:
        scenes = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaptureError(f"cannot read {path}: {exc}") from exc
    if not isinstance(scenes, dict):
        raise CaptureError(f"{path} must hold a JSON object")
    for key in ("app", "screenshots"):
        if key not in scenes:
            raise CaptureError(f"{path} is missing required key {key!r}")
    names = [s.get("name") for s in scenes["screenshots"]]
    if any(not isinstance(n, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", n) for n in names):
        raise CaptureError("every screenshot needs a kebab-case 'name'")
    if len(set(names)) != len(names):
        raise CaptureError("screenshot names must be unique")
    for shot in scenes["screenshots"]:
        if not str(shot.get("alt", "")).strip():
            raise CaptureError(f"screenshot {shot['name']!r} has no 'alt' text")
    if "stub_replies" in scenes:
        raise CaptureError(
            f"{path}: 'stub_replies' (scripted agent replies) is no longer supported. "
            'Mark a chat scene "live": true so it runs against a real model'
        )
    return scenes


def substitute(value: Any, variables: dict[str, Any]) -> Any:
    """Replace ``{{name}}`` in every string of ``value`` with ``variables[name]``."""
    if isinstance(value, str):
        whole = re.fullmatch(r"\{\{(\w+)\}\}", value)
        if whole and whole.group(1) in variables:
            return variables[whole.group(1)]

        def repl(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in variables:
                raise CaptureError(f"unknown variable {{{{{name}}}}}")
            return str(variables[name])

        return re.sub(r"\{\{(\w+)\}\}", repl, value)
    if isinstance(value, list):
        return [substitute(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, variables) for k, v in value.items()}
    return value


def dig(data: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        if isinstance(data, list) and part.isdigit():
            data = data[int(part)]
        elif isinstance(data, dict) and part in data:
            data = data[part]
        else:
            raise CaptureError(f"response has no {dotted!r}")
    return data


# ── Tools ──


def resolve_python(kirocrew: Path, explicit: str | None) -> str:
    candidates = [explicit, os.environ.get("KIROCREW_PYTHON")]
    venv = kirocrew / ".venv"
    candidates.append(str(venv / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise CaptureError(
        "no Python with Kiro Crew's dependencies found: pass --python, set "
        f"KIROCREW_PYTHON, or create {venv} (see the Kiro Crew CONTRIBUTING guide)"
    )


def resolve_ffmpeg(explicit: str | None) -> str:
    found = explicit or os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not found or not Path(found).is_file():
        raise CaptureError("ffmpeg not found: pass --ffmpeg, set FFMPEG, or put it on PATH")
    return found


def ffmpeg(binary: str, *args: str) -> None:
    proc = subprocess.run(
        [binary, "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise CaptureError(f"ffmpeg failed ({proc.returncode}): {proc.stderr.strip()[-800:]}")


def png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        header = handle.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        raise CaptureError(f"{path} is not a PNG")
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


def git_head(path: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def git_dirty(path: Path) -> bool | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(out.stdout.strip()) if out.returncode == 0 else None


# ── The isolated gateway ──


@dataclass
class Gateway:
    port: int
    token: str
    home: str
    pid: int

    @property
    def base_url(self) -> str:
        return f"http://localhost:{self.port}"


def _pid_alive(pid: int) -> bool:
    # POSIX only: on Windows os.kill(pid, 0) TERMINATES the process.
    if IS_WINDOWS:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


STUB_BACKEND = Path(__file__).resolve().parent / "stub_backend.py"


def write_backend_launcher(scratch: Path, python: str, src: Path) -> Path:
    """Write the program the gateway runs as its "kiro-cli": the stub backend.

    POSIX gets a ``sh`` script and Windows a ``.cmd`` (Windows cannot run a
    ``.py`` path, which is why Kiro Crew's own harness writes a ``.cmd`` too).
    Either one runs ``stub_backend.py`` with the checkout's interpreter and its
    ``src`` on ``PYTHONPATH``. Named ``kiro-backend``, not ``kiro-cli``, for the
    same reason the harness's shim is: nothing may be granted by its file name.
    """
    parts = [python, str(src), str(STUB_BACKEND)]
    if IS_WINDOWS:
        for part in parts:
            if '"' in part or "\r" in part or "\n" in part:
                raise CaptureError(f"cannot quote {part!r} in a Windows batch file")
        py, src_s, stub = (p.replace("%", "%%") for p in parts)
        launcher = scratch / "kiro-backend.cmd"
        launcher.write_text(
            "@echo off\r\n"
            f'set "PYTHONPATH={src_s};%PYTHONPATH%"\r\n'
            f'"{py}" "{stub}" %*\r\n',
            encoding="utf-8",
        )
        return launcher
    import shlex

    py, src_s, stub = (shlex.quote(p) for p in parts)
    launcher = scratch / "kiro-backend"
    launcher.write_text(
        "#!/bin/sh\n"
        f'PYTHONPATH={src_s}${{PYTHONPATH:+:$PYTHONPATH}} exec {py} {stub} "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    return launcher


# How long the sign-in probe of the real kiro-cli may take.
KIRO_LOGIN_PROBE_TIMEOUT = 60


def resolve_live_backend(explicit: str | None) -> Path:
    """The operator's installed kiro-cli, checked to be signed in.

    A scene marked ``"live": true`` runs against a real model through this
    binary and its EXISTING login: the harness copies no credential, prints
    nothing ``kiro-cli whoami`` says, and never falls back to the stub. If the
    binary is missing or not signed in, the run stops.
    """
    found = explicit or os.environ.get("KIROCREW_CAPTURE_KIRO_CLI") or shutil.which("kiro-cli")
    if not found or not Path(found).is_file():
        raise CaptureError(
            "a scene is marked \"live\" but no kiro-cli was found: install it, or pass "
            "--kiro-cli / set KIROCREW_CAPTURE_KIRO_CLI. The capture never falls back "
            "to scripted replies"
        )
    path = Path(found).absolute()
    try:
        probe = subprocess.run(
            [str(path), "whoami"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=KIRO_LOGIN_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CaptureError(
            f"{path} could not run ({type(exc).__name__}); live scenes need it"
        ) from exc
    if probe.returncode != 0:
        raise CaptureError(
            f"{path} is not signed in (`kiro-cli whoami` exited {probe.returncode}): "
            "run `kiro-cli login` yourself, then capture again. The capture never "
            "falls back to scripted replies"
        )
    return path


@contextlib.contextmanager
def isolated_gateway(
    kirocrew: Path, python: str, fixture: str, scratch: Path,
    live_backend: Path | None = None,
) -> Iterator[Gateway]:
    """Boot one isolated gateway; ``live_backend`` is a signed-in kiro-cli, else the stub."""
    src = kirocrew / "src"
    if not (src / "kiro_crew" / "testing" / "harness.py").is_file():
        raise CaptureError(f"{kirocrew} is not a Kiro Crew checkout with src/kiro_crew/testing")
    dist = src / "kiro_crew" / "static" / "dist" / "index.html"
    if not dist.is_file():
        raise CaptureError(
            f"{dist} is missing: build and stage the dashboard first "
            "(cd website && npm ci && npm run build; then python -m kiro_crew.frontend stage .)"
        )
    bootstrap = scratch / "gateway_bootstrap.py"
    bootstrap.write_text(BOOTSTRAP, encoding="utf-8")
    backend = live_backend or write_backend_launcher(scratch, python, src)
    stderr_log = scratch / "gateway.stderr.log"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("KIROCREW_HOME", None)  # the harness sets its own; never inherit a live one
    env["PYTHONUNBUFFERED"] = "1"
    kind = "live model through your kiro-cli" if live_backend else "stub ACP backend"
    log(f"booting an isolated gateway from {kirocrew} (fixture {fixture!r}, {kind})")
    with stderr_log.open("wb") as err:
        proc = subprocess.Popen(
            [python, str(bootstrap), fixture, str(backend)],
            cwd=str(scratch),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=err,
            start_new_session=not IS_WINDOWS,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    lines: queue.Queue[str | None] = queue.Queue()

    stopped = threading.Event()

    def pump() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace")
            if line.strip() == STOPPED_LINE:
                stopped.set()
            lines.put(line)
        lines.put(None)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    gateway: Gateway | None = None
    try:
        deadline = time.monotonic() + GATEWAY_BOOT_TIMEOUT
        while gateway is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CaptureError("gateway did not report ready in time")
            try:
                line = lines.get(timeout=remaining)
            except queue.Empty:
                continue
            if line is None:
                tail = stderr_log.read_text(encoding="utf-8", errors="replace")[-3000:]
                raise CaptureError(f"gateway bootstrap exited before ready:\n{tail}")
            if line.startswith(READY_PREFIX):
                info = json.loads(line[len(READY_PREFIX):])
                gateway = Gateway(int(info["port"]), str(info["token"]), info["home"], info["pid"])
        log(f"gateway up on {gateway.base_url} (pid {gateway.pid}, home {gateway.home})")
        yield gateway
    finally:
        _stop_gateway(proc, gateway, reader, stopped)


def _stop_gateway(
    proc: subprocess.Popen, gateway: Gateway | None,
    reader: threading.Thread, stopped: threading.Event,
) -> None:
    """Stop the bootstrap, which stops the gateway and deletes its home.

    Escalates only when the polite path fails: close stdin (the harness's own
    teardown), then terminate the bootstrap, then signal the gateway's process
    group directly, then remove a leftover temporary home.
    """
    with contextlib.suppress(OSError, ValueError):
        if proc.stdin:
            proc.stdin.close()
    try:
        proc.wait(timeout=TEARDOWN_GRACE)
    except subprocess.TimeoutExpired:
        log("bootstrap did not stop on its own; terminating it")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
    # The pipe closes when the bootstrap exits; let the reader see its last line.
    reader.join(timeout=5)
    if gateway is None:
        return
    if stopped.is_set():
        if Path(gateway.home).exists():
            log(f"WARNING: temporary home {gateway.home} was left behind")
        else:
            log("gateway stopped and its temporary home removed")
        return
    if IS_WINDOWS:
        # No safe liveness probe here (os.kill(pid, 0) TERMINATES a process on
        # Windows), so never delete a home a gateway might still be using.
        log(f"WARNING: the gateway's own teardown did not finish; check pid "
            f"{gateway.pid} and remove {gateway.home} by hand once it has exited")
        return
    if _pid_alive(gateway.pid):
        log(f"gateway pid {gateway.pid} still running; signalling its process group")
        for sig in (signal.SIGTERM, signal.SIGKILL):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(gateway.pid, sig)
            for _ in range(50):
                if not _pid_alive(gateway.pid):
                    break
                time.sleep(0.1)
    home = Path(gateway.home)
    tmp_root = Path(tempfile.gettempdir()).resolve()
    if home.exists() and home.name.startswith("kirocrew-harness-"):
        if tmp_root in home.resolve().parents and not _pid_alive(gateway.pid):
            shutil.rmtree(home, ignore_errors=True)
    if home.exists():
        log(f"WARNING: temporary home {home} was left behind")
    else:
        log("gateway stopped and its temporary home removed")


# ── Browser helpers ──


def init_storage_script(scenes: dict[str, Any], theme: str) -> str:
    storage = {"mc-onboarded": "1", "mc-theme": theme}
    storage.update(substitute(scenes.get("init_storage", {}), {"theme": theme}))
    return (
        "(() => { try { const s = "
        + json.dumps(storage)
        + "; for (const k in s) localStorage.setItem(k, s[k]); } catch (e) {} })();"
    )


# Kiro Crew keeps the light/dark choice on the SERVER (``/api/config/theme``)
# and treats localStorage as a render cache, so a theme pass sets it there.
DEFAULT_THEME_API = {"method": "PUT", "path": "/api/config/theme", "body": {"mode": "{{theme}}"}}


def apply_theme(page: Any, scenes: dict[str, Any], theme: str) -> None:
    spec = substitute(scenes.get("theme_api", DEFAULT_THEME_API), {"theme": theme})
    status, data = api_call(page, spec.get("method", "PUT"), spec["path"], spec.get("body"))
    if status >= 400:
        raise CaptureError(f"could not switch the dashboard to {theme}: {status} {str(data)[:200]}")


def sign_in(context: Any, gateway: Gateway) -> Any:
    page = context.new_page()
    page.goto(f"{gateway.base_url}/?token={gateway.token}", wait_until="load", timeout=60_000)
    # The token is exchanged for a session cookie; drop it from the address.
    page.goto(f"{gateway.base_url}/", wait_until="load", timeout=60_000)
    return page


def api_call(page: Any, method: str, path: str, body: Any) -> tuple[int, Any]:
    # The dashboard can reload itself right after sign-in (a fresh gateway
    # finishing its start-up), which destroys the page's script context under an
    # in-flight evaluate. The call is retried once the page has loaded again.
    for attempt in range(3):
        try:
            return _api_call_once(page, method, path, body)
        except Exception as exc:  # noqa: BLE001 - Playwright raises its own Error type
            if "Execution context was destroyed" not in str(exc) or attempt == 2:
                raise
            page.wait_for_load_state("load")
    raise AssertionError("unreachable")


def _api_call_once(page: Any, method: str, path: str, body: Any) -> tuple[int, Any]:
    result = page.evaluate(
        """async ([method, path, body]) => {
            const init = { method, credentials: 'same-origin', headers: {} };
            if (body !== null && body !== undefined) {
              init.headers['Content-Type'] = 'application/json';
              init.body = JSON.stringify(body);
            }
            const res = await fetch(path, init);
            const text = await res.text();
            let json = null;
            try { json = JSON.parse(text); } catch (e) { json = text; }
            return [res.status, json];
        }""",
        [method, path, body],
    )
    return int(result[0]), result[1]


def run_setup(page: Any, scenes: dict[str, Any], app_root: Path) -> dict[str, Any]:
    # ``{{app_root}}``: this repository's root, for a setup step that installs
    # the app from source (``POST /api/apps/install {"source": "{{app_root}}"}``).
    variables: dict[str, Any] = {"app_root": str(app_root)}
    for index, step in enumerate(scenes.get("setup", [])):
        if "wait_ms" in step:
            # Rows the app timestamps to the second need a gap to sort predictably.
            page.wait_for_timeout(int(step["wait_ms"]))
            continue
        label = step.get("name") or f"setup[{index}]"
        method = step.get("method", "GET").upper()
        path = substitute(step["path"], variables)
        body = substitute(step.get("body"), variables)
        expect = step.get("expect", [200, 201, 204])
        status, data = api_call(page, method, path, body)
        # ``until``: repeat the call until a field of the answer has a value, for
        # work the app does in the background (a download, a build). Bounded by
        # ``timeout_ms`` (default 300000); each repeat waits ``interval_ms``.
        until = step.get("until")
        if until is not None:
            deadline = time.monotonic() + int(step.get("timeout_ms", 300_000)) / 1000
            while True:
                try:
                    reached = status in expect and dig(data, until["path"]) == until["equals"]
                except CaptureError:
                    reached = False
                if reached:
                    break
                if time.monotonic() >= deadline:
                    raise CaptureError(f"{label}: {until['path']} never became {until['equals']!r}: {str(data)[:400]}")
                page.wait_for_timeout(int(step.get("interval_ms", 2000)))
                status, data = api_call(page, method, path, body)
        if status not in expect:
            if step.get("optional"):
                log(f"skipped optional step {label}: {method} {path} answered {status}")
                continue
            raise CaptureError(f"{label}: {method} {path} answered {status}: {str(data)[:400]}")
        for var, dotted in step.get("save", {}).items():
            variables[var] = dig(data, dotted)
        log(f"seeded: {label} ({status})")
    return variables


def locate(page: Any, selector: str) -> Any:
    return page.locator(selector).first


def move_to(page: Any, selector: str, animate: bool) -> Any:
    target = locate(page, selector)
    target.wait_for(state="visible", timeout=15_000)
    target.scroll_into_view_if_needed(timeout=5_000)
    if animate:
        box = target.bounding_box()
        if box:
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=18)
            page.wait_for_timeout(250)
    return target


# How much of a ``"cut": true`` wait the recording keeps, so the viewer still
# sees that something is happening (a "Thinking..." line) before the result.
CUT_KEEP_MS = 700
CUT_TAIL_MS = 150


# A live model's reply: how long one may take, and how often the slot is read.
REPLY_TIMEOUT_MS = 300_000
REPLY_POLL_MS = 1_000

ACTIVE_SLOT_JS = """() => {
  const row = document.querySelector('.session-row.session-active');
  const slot = row && row.closest('[data-slot-key]');
  return slot ? slot.getAttribute('data-slot-key') : null;
}"""

# The newest reply carries its turn stats ("0.15 credits . 11s") once it is done.
REPLY_RENDERED_JS = """() => {
  const all = document.querySelectorAll("[data-role='assistant']");
  const last = all[all.length - 1];
  return !!last && !!last.querySelector("[data-testid='turn-stats']");
}"""

# Scroll the chat so the START of the newest reply is on screen (below the
# pinned prompt), when a long reply pushed it above the top. Does nothing when
# the start is already visible.
SCROLL_REPLY_JS = r"""([margin, smooth]) => {
  const all = document.querySelectorAll("[data-role='assistant']");
  const last = all[all.length - 1];
  if (!last) return 'no reply';
  let sc = last.parentElement;
  while (sc && !(sc.scrollHeight > sc.clientHeight + 1
                 && /(auto|scroll)/.test(getComputedStyle(sc).overflowY))) sc = sc.parentElement;
  if (!sc) return 'fits';
  const pinned = document.querySelector("[data-testid='pinned-prompt']");
  const cover = pinned && pinned.getClientRects().length ? pinned.getBoundingClientRect().bottom
                                                          : sc.getBoundingClientRect().top;
  const top = last.getBoundingClientRect().top;
  if (top >= cover + margin) return 'visible';
  sc.scrollTo({ top: sc.scrollTop - (cover + margin - top), behavior: smooth ? 'smooth' : 'auto' });
  return 'scrolled';
}"""


def wait_reply(page: Any, turns: dict[str, int], timeout_ms: int) -> None:
    """Wait until the open chat's agent has finished its next turn.

    Reads the gateway's own record of the chat (``GET /api/chat/slots/<key>``)
    instead of guessing from the page: the turn counter must pass the last one
    seen here, nothing may still be running, stopping or queued, and the last
    message must be the agent's. Then waits for the page to render that reply.
    """
    from urllib.parse import quote

    slot = page.evaluate(ACTIVE_SLOT_JS)
    if not slot:
        raise CaptureError("wait_reply: no open chat on the page")
    want = turns.get(slot, 0) + 1
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        status, data = api_call(
            page, "GET", f"/api/chat/slots/{quote(slot, safe='')}?limit=1", None
        )
        if status == 200 and isinstance(data, dict):
            busy = (data.get("running") or data.get("stopping")
                    or data.get("admission_queued") or data.get("queue"))
            turn = int(data.get("turn") or 0)
            if turn >= want and not busy:
                messages = data.get("messages") or []
                role = messages[-1].get("role") if messages else None
                if role != "assistant":
                    raise CaptureError(f"wait_reply: the turn ended without a reply (last: {role})")
                turns[slot] = turn
                break
        if time.monotonic() > deadline:
            raise CaptureError(f"wait_reply: no finished reply within {timeout_ms // 1000}s")
        page.wait_for_timeout(REPLY_POLL_MS)
    page.wait_for_function(REPLY_RENDERED_JS, timeout=30_000)


def run_steps(
    page: Any, steps: list[dict[str, Any]], variables: dict[str, Any], animate: bool,
    cuts: list[tuple[float, float]] | None = None, t0: float = 0.0,
) -> None:
    """Run ``steps``. In a recording, a wait marked ``"cut": true`` is appended
    to ``cuts`` as (start, end) seconds since ``t0``, and is shortened later."""
    turns: dict[str, int] = {}
    for raw in steps:
        step = substitute(raw, variables)
        began = time.monotonic()
        if "goto" in step:
            page.goto(step["goto"], wait_until="load", timeout=60_000)
        elif "click" in step:
            move_to(page, step["click"], animate).click(timeout=10_000)
        elif "hover" in step:
            move_to(page, step["hover"], animate).hover(timeout=10_000)
        elif "fill" in step:
            move_to(page, step["fill"], animate).fill(str(step.get("value", "")))
        elif "type" in step:
            target = move_to(page, step["type"], animate)
            target.click(timeout=10_000)
            target.press_sequentially(str(step.get("value", "")), delay=step.get("delay_ms", 45))
        elif "press" in step:
            locate(page, step["press"]).press(step.get("key", "Enter"))
        elif "select" in step:
            move_to(page, step["select"], animate).select_option(step.get("value"))
        elif "wait_for" in step:
            locate(page, step["wait_for"]).wait_for(
                state="visible", timeout=int(step.get("timeout_ms", 15_000))
            )
        elif "wait_hidden" in step:
            locate(page, step["wait_hidden"]).wait_for(state="hidden", timeout=15_000)
        elif "wait_ms" in step:
            page.wait_for_timeout(int(step["wait_ms"]))
        elif "wait_reply" in step:
            wait_reply(page, turns, int(step.get("timeout_ms", REPLY_TIMEOUT_MS)))
        elif "scroll_reply" in step:
            page.evaluate(SCROLL_REPLY_JS, [int(step.get("margin", 12)), animate])
            page.wait_for_timeout(900 if animate else 200)
        elif "scroll" in step:
            locate(page, step["scroll"]).scroll_into_view_if_needed(timeout=5_000)
        elif "caption" in step:
            page.evaluate(CAPTION_JS, [
                step["caption"] or "", step.get("position", "bottom"), step.get("offset"),
                step.get("over"), step.get("above"),
            ])
        elif "mouse_park" in step:
            # Move the pointer somewhere neutral so it does not cover content.
            x, y = step["mouse_park"]
            page.mouse.move(x, y, steps=12 if animate else 1)
        else:
            raise CaptureError(f"unknown step {raw!r}")
        if cuts is not None and step.get("cut"):
            keep = int(step.get("cut_keep_ms", CUT_KEEP_MS)) / 1000
            start, end = began - t0 + keep, time.monotonic() - t0 - CUT_TAIL_MS / 1000
            if end - start > 0.3:
                cuts.append((start, end))


def settle(
    page: Any, ready: str | None, settle_ms: int, hidden: tuple[str, ...] = (), where: str = ""
) -> None:
    if ready:
        locate(page, ready).wait_for(state="visible", timeout=30_000)
    with contextlib.suppress(Exception):
        page.wait_for_load_state("networkidle", timeout=5_000)
    wait_until_hidden(page, hidden, where)
    page.wait_for_timeout(settle_ms)


def wait_until_hidden(page: Any, selectors: tuple[str, ...], where: str) -> None:
    """Wait until nothing matching ``selectors`` is on screen, or refuse the frame."""
    if not selectors:
        return
    try:
        page.wait_for_function(
            """(sels) => !sels.some((sel) => [...document.querySelectorAll(sel)]
                .some((el) => el.getClientRects().length > 0))""",
            arg=list(selectors), timeout=SETTLE_HIDDEN_TIMEOUT_MS,
        )
    except Exception as exc:  # Playwright's TimeoutError
        raise CaptureError(
            f"{where}: still showing {list(selectors)} after "
            f"{SETTLE_HIDDEN_TIMEOUT_MS // 1000}s; refusing to capture a loading frame"
        ) from exc


def settle_hidden(scenes: dict[str, Any], item: dict[str, Any]) -> tuple[str, ...]:
    return tuple(DEFAULT_SETTLE_HIDDEN) + tuple(scenes.get("settle_hidden", [])) + tuple(
        item.get("settle_hidden", [])
    )


def assert_clean(page: Any, forbidden: list[str], where: str) -> None:
    text = page.evaluate("() => document.body ? document.body.innerText : ''")
    hits = [f for f in forbidden if f and f in text]
    overlay = page.locator("vite-error-overlay").count()
    if hits or overlay:
        reason = f"forbidden text {hits}" if hits else "a dev error overlay"
        raise CaptureError(f"{where}: refusing to capture a frame showing {reason}")


# ── Media writers ──


def write_png(ffmpeg_bin: str, raw: Path, out: Path) -> tuple[int, int, int]:
    for width in PNG_WIDTHS:
        ffmpeg(
            ffmpeg_bin, "-i", str(raw), "-vf", f"scale={width}:-1:flags=lanczos",
            "-compression_level", "9", str(out),
        )
        if out.stat().st_size <= PNG_BUDGET:
            break
        # Same width, 256-colour palette: UI art quantizes with little visible loss.
        ffmpeg(
            ffmpeg_bin, "-i", str(raw), "-vf",
            f"scale={width}:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=256"
            "[p];[b][p]paletteuse=dither=none",
            "-pix_fmt", "pal8", str(out),
        )
        if out.stat().st_size <= PNG_BUDGET:
            break
    size = out.stat().st_size
    if size > PNG_BUDGET:
        raise CaptureError(f"{out.name} is {size} bytes, over the {PNG_BUDGET}-byte budget")
    w, h = png_size(out)
    return w, h, size


def _cut_filter(cuts: list[tuple[float, float]], trim: float, fps: int) -> str:
    """The filter that drops the ``cuts`` spans (seconds since recording start)."""
    spans = [(a - trim, b - trim) for a, b in cuts if b - trim > 0]
    if not spans:
        return ""
    expr = "+".join(f"between(t,{max(a, 0):.3f},{b:.3f})" for a, b in spans)
    return f"select='not({expr})',setpts=N/{fps}/TB,"


def write_video(
    ffmpeg_bin: str, webm: Path, out_dir: Path, name: str, trim: float,
    cuts: list[tuple[float, float]] | None = None,
) -> dict:
    mp4 = out_dir / f"{name}.mp4"
    gif = out_dir / f"{name}.gif"
    start = f"{max(trim, 0.0):.2f}"
    cuts = cuts or []
    for crf in MP4_CRFS:
        ffmpeg(
            ffmpeg_bin, "-ss", start, "-i", str(webm), "-vf",
            f"fps=30,{_cut_filter(cuts, trim, 30)}scale=1440:-2:flags=lanczos",
            "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-an", str(mp4),
        )
        if mp4.stat().st_size <= MP4_BUDGET:
            break
    if mp4.stat().st_size > MP4_BUDGET:
        raise CaptureError(
            f"{mp4.name} is over the {MP4_BUDGET}-byte budget; shorten the walkthrough"
        )
    for width, fps in GIF_LADDER:
        ffmpeg(
            ffmpeg_bin, "-ss", start, "-i", str(webm), "-vf",
            f"fps={fps},{_cut_filter(cuts, trim, fps)}scale={width}:-1:flags=lanczos,split[s0][s1];"
            "[s0]palettegen=max_colors=160:stats_mode=diff[p];"
            "[s1][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle",
            "-loop", "0", str(gif),
        )
        if gif.stat().st_size <= GIF_BUDGET:
            break
    if gif.stat().st_size > GIF_BUDGET:
        raise CaptureError(
            f"{gif.name} is over the {GIF_BUDGET}-byte budget; shorten the walkthrough"
        )
    return {
        "shortened_waits_s": [round(b - a, 1) for a, b in cuts],
        "mp4": {"file": mp4.name, "bytes": mp4.stat().st_size},
        "gif": {"file": gif.name, "bytes": gif.stat().st_size, "width": width, "fps": fps},
    }


# ── Capture passes ──


def new_context(browser: Any, scenes: dict[str, Any], theme: str, **extra: Any) -> Any:
    context = browser.new_context(
        viewport=VIEWPORT,
        device_scale_factor=extra.pop("device_scale_factor", DEVICE_SCALE),
        color_scheme="dark" if theme == "dark" else "light",
        locale=scenes.get("locale", "en-US"),
        timezone_id=scenes.get("timezone", "UTC"),
        reduced_motion="no-preference",
        **extra,
    )
    context.add_init_script(init_storage_script(scenes, theme))
    context.route(_is_usage_read, _no_usage_reading)
    return context


# The top bar's credit readout reads ``/api/sessions/usage``, which on a live
# gateway answers with the operator's REAL plan, balance and account email.
# Every capture page gets the gateway's own "no reading" shape instead, so no
# account data can reach a picture; the stub gateway has no reading anyway.
USAGE_PATHS = ("/api/sessions/usage", "/api/sessions/usage/refresh")


def _is_usage_read(url: str) -> bool:
    from urllib.parse import urlparse

    return urlparse(url).path in USAGE_PATHS


def _no_usage_reading(route: Any) -> None:
    route.fulfill(status=200, content_type="application/json",
                  body=json.dumps({"usage": {"available": False}}))


def capture_screenshots(
    browser: Any, gateway: Gateway, scenes: dict[str, Any], variables: dict[str, Any],
    out_dir: Path, scratch: Path, ffmpeg_bin: str, only: set[str] | None,
) -> list[dict[str, Any]]:
    written: list[dict[str, Any]] = []
    default_themes = scenes.get("themes", ["light", "dark"])
    forbidden = list(DEFAULT_FORBIDDEN_TEXT) + list(scenes.get("forbid_text", []))
    for theme in ("light", "dark"):
        shots = [
            s for s in scenes["screenshots"]
            if theme in s.get("themes", default_themes) and (only is None or s["name"] in only)
        ]
        if not shots:
            continue
        context = new_context(browser, scenes, theme)
        try:
            page = sign_in(context, gateway)
            apply_theme(page, scenes, theme)
            for shot in shots:
                where = f"{shot['name']} ({theme})"
                page.goto(gateway.base_url + substitute(shot["path"], variables), wait_until="load")
                settle(page, shot.get("ready") or scenes.get("ready"), 400)
                run_steps(page, shot.get("steps", []), variables, animate=False)
                page.mouse.move(VIEWPORT["width"] - 4, VIEWPORT["height"] - 4)
                settle(page, shot.get("ready_after"), int(shot.get("settle_ms", 700)),
                       settle_hidden(scenes, shot), where)
                assert_clean(page, forbidden + list(shot.get("forbid_text", [])), where)
                raw = scratch / f"{shot['name']}-{theme}.raw.png"
                if shot.get("clip"):
                    locate(page, shot["clip"]).screenshot(path=str(raw), animations="disabled")
                else:
                    page.screenshot(path=str(raw), animations="disabled")
                out = out_dir / f"{shot['name']}-{theme}.png"
                width, height, size = write_png(ffmpeg_bin, raw, out)
                written.append({
                    "file": out.name, "name": shot["name"], "theme": theme, "bytes": size,
                    "width": width, "height": height, "alt": shot["alt"],
                })
                log(f"screenshot {out.name}: {width}x{height}, {size} bytes")
        finally:
            context.close()
    return written


def capture_walkthrough(
    browser: Any, gateway: Gateway, scenes: dict[str, Any], variables: dict[str, Any],
    out_dir: Path, scratch: Path, ffmpeg_bin: str,
) -> dict[str, Any] | None:
    walk = scenes.get("walkthrough")
    if not walk:
        return None
    theme = walk.get("theme", "light")
    name = walk.get("name", "walkthrough")
    forbidden = list(DEFAULT_FORBIDDEN_TEXT) + list(scenes.get("forbid_text", []))
    # Sign in OUTSIDE the recorded context, so the token URL and the first-load
    # flash never reach the video.
    login = new_context(browser, scenes, theme, device_scale_factor=1)
    try:
        apply_theme(sign_in(login, gateway), scenes, theme)
        state = login.storage_state()
    finally:
        login.close()
    video_dir = scratch / "video"
    context = new_context(
        browser, scenes, theme, device_scale_factor=1, storage_state=state,
        record_video_dir=str(video_dir), record_video_size=VIEWPORT,
    )
    context.add_init_script(CURSOR_SCRIPT)
    started = time.monotonic()
    page = context.new_page()
    try:
        page.goto(gateway.base_url + substitute(walk["path"], variables), wait_until="load")
        hidden = settle_hidden(scenes, walk)
        settle(page, walk.get("ready") or scenes.get("ready"), 300, hidden, f"{name} (start)")
        assert_clean(page, forbidden, f"{name} (start)")
        page.mouse.move(VIEWPORT["width"] * 0.6, VIEWPORT["height"] * 0.6)
        trim = time.monotonic() - started - 0.1
        cuts: list[tuple[float, float]] = []
        run_steps(page, walk.get("steps", []), variables, animate=True, cuts=cuts, t0=started)
        wait_until_hidden(page, hidden, f"{name} (end)")
        assert_clean(page, forbidden, f"{name} (end)")
        page.wait_for_timeout(int(walk.get("tail_ms", 1200)))
    finally:
        video = page.video
        context.close()
    webm = Path(video.path()) if video else None
    if not webm or not webm.is_file():
        raise CaptureError("Playwright wrote no video")
    result = write_video(ffmpeg_bin, webm, out_dir, name, trim, cuts)
    result["theme"] = theme
    result["alt"] = walk.get("alt", "")
    log(f"walkthrough {result['mp4']['file']} {result['mp4']['bytes']} bytes, "
        f"{result['gif']['file']} {result['gif']['bytes']} bytes")
    return result


# ── Entry point ──


def parse_args(argv: list[str]) -> argparse.Namespace:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--kirocrew", required=True, type=Path,
                        help="Path to a Kiro Crew checkout with a built, staged dashboard")
    parser.add_argument("--python", help="Interpreter with Kiro Crew's dependencies "
                        "(default: $KIROCREW_PYTHON, then <kirocrew>/.venv)")
    parser.add_argument("--scenes", type=Path, default=here / "scenes.json")
    parser.add_argument("--out", type=Path, default=here.parent / "docs" / "media")
    parser.add_argument("--ffmpeg", help="ffmpeg binary (default: $FFMPEG, then PATH)")
    parser.add_argument("--only", choices=("all", "screenshots", "walkthrough"), default="all")
    parser.add_argument("--shot", action="append", help="Capture only this screenshot (repeatable)")
    parser.add_argument("--serve", action="store_true",
                        help="Boot and seed, print the sign-in URL, and wait for Enter "
                        "(for writing scenes.json); captures nothing")
    parser.add_argument("--headed", action="store_true", help="Show the browser window")
    parser.add_argument("--kiro-cli", help="The installed, signed-in kiro-cli that scenes "
                        'marked "live" run against (default: $KIROCREW_CAPTURE_KIRO_CLI, '
                        "then PATH)")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed: python -m pip install playwright && "
              "python -m playwright install chromium", file=sys.stderr)
        return 2
    kirocrew = args.kirocrew.expanduser().resolve()
    try:
        scenes = load_scenes(args.scenes)
        python = resolve_python(kirocrew, args.python)
        ffmpeg_bin = "" if args.serve else resolve_ffmpeg(args.ffmpeg)
        wanted = set(args.shot) if args.shot else {s["name"] for s in scenes["screenshots"]}
        live_shots = {s["name"] for s in scenes["screenshots"] if s.get("live")} & wanted
        stub_shots = wanted - live_shots
        if args.only == "walkthrough":
            live_shots = stub_shots = set()
        walk_live = bool((scenes.get("walkthrough") or {}).get("live"))
        needs_live = not args.serve and (
            bool(live_shots) or (args.only in ("all", "walkthrough") and walk_live)
        )
        # Checked BEFORE anything is captured: a live scene never falls back to the stub.
        live_backend = resolve_live_backend(args.kiro_cli) if needs_live else None
    except CaptureError as exc:
        print(f"[capture] FAILED: {exc}", file=sys.stderr)
        return 1
    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="kirocrew-capture-"))

    def _interrupted(signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, _interrupted)
    app_root = Path(__file__).resolve().parent.parent

    @contextlib.contextmanager
    def seeded(
        pw: Any, label: str, live: bool = False,
    ) -> Iterator[tuple[Gateway, Any, dict[str, Any]]]:
        """A freshly booted, freshly seeded gateway and a browser for one pass."""
        work = scratch / label
        work.mkdir()
        with isolated_gateway(
            kirocrew, python, scenes.get("fixture", "minimal"), work,
            live_backend if live else None,
        ) as gw:
            # The full Chromium build, not the headless shell: only it carries the
            # PDF viewer, so an app that shows a PDF in a frame is photographed with
            # the document rather than a blank pane.
            browser = pw.chromium.launch(headless=not args.headed, channel="chromium")
            try:
                seed_ctx = new_context(browser, scenes, "light", device_scale_factor=1)
                try:
                    variables = run_setup(sign_in(seed_ctx, gw), scenes, app_root)
                finally:
                    seed_ctx.close()
                yield gw, browser, variables
            finally:
                browser.close()

    try:
        shots: list[dict[str, Any]] = []
        walkthrough = None
        with sync_playwright() as pw:
            if args.serve:
                with seeded(pw, "serve") as (gw, _browser, _variables):
                    log(f"ready. Sign in at {gw.base_url}/?token={gw.token}")
                    log("(loopback only; the token dies with the gateway). Press Enter to stop.")
                    with contextlib.suppress(EOFError):
                        input()
                return 0
            # One gateway per pass: the walkthrough must not show the chats the
            # screenshots opened, and the screenshots must not show what the
            # walkthrough added or marked.
            # The chat scenes marked "live" get a gateway of their own on the
            # real model; every other screenshot stays on the stub.
            for label, names, live in (
                ("screenshots", stub_shots, False), ("screenshots-live", live_shots, True),
            ):
                if not names:
                    continue
                with seeded(pw, label, live) as (gw, browser, variables):
                    shots += capture_screenshots(
                        browser, gw, scenes, variables, out_dir, scratch, ffmpeg_bin, names
                    )
            if args.only in ("all", "walkthrough"):
                with seeded(pw, "walkthrough", walk_live) as (gw, browser, variables):
                    walkthrough = capture_walkthrough(
                        browser, gw, scenes, variables, out_dir, scratch, ffmpeg_bin
                    )
        walkthrough_name = (scenes.get("walkthrough") or {}).get("name", "walkthrough")
        live_names = sorted(
            {s["name"] for s in scenes["screenshots"] if s.get("live")}
            | ({walkthrough_name} if walk_live else set())
        )
        manifest_path = out_dir / "media.json"
        previous: dict[str, Any] = {}
        if manifest_path.is_file():
            with contextlib.suppress(json.JSONDecodeError):
                previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        # Keep an earlier run's entry only for a shot this scenes.json still
        # defines and whose file is still there (a --shot run updates the rest).
        current = {sh["name"] for sh in scenes["screenshots"]}
        merged_shots = {
            s["file"]: s for s in previous.get("screenshots", [])
            if s.get("name") in current and (out_dir / s["file"]).is_file()
        }
        merged_shots.update({s["file"]: s for s in shots})
        manifest = {
            "app": scenes["app"],
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "app_commit": git_head(Path(__file__).resolve().parent.parent),
            "kirocrew_commit": git_head(kirocrew),
            "kirocrew_tree_dirty": git_dirty(kirocrew),
            "gateway": "isolated temporary home, approval=reads; a freshly seeded gateway "
                       "for the stub screenshots, one for the live ones and one for the "
                       "walkthrough",
            "data": "fictional, seeded through the app's HTTP API (capture/scenes.json)",
            "agent_backend": {
                "live": live_names,
                "live_model": (
                    "the operator's own installed, signed-in kiro-cli over ACP with model "
                    "\"auto\"; the replies on screen are that model's and vary from run to run"
                    if live_names else None
                ),
                "stub": "Kiro Crew's offline stub ACP backend (capture/stub_backend.py) "
                        "for every other scene; no chat reply is shown from it",
                "credit_reading": "the browser answers the top bar's credit readout "
                                  "(/api/sessions/usage) with \"no reading\", so no "
                                  "account balance or email can be shown",
            },
            "note": scenes.get("provenance_note", ""),
            "data_notes": scenes.get("data_notes", []),
            "viewport": VIEWPORT,
            "device_scale": DEVICE_SCALE,
            "screenshots": sorted(merged_shots.values(), key=lambda s: s["file"]),
            "walkthrough": walkthrough or previous.get("walkthrough"),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        log(f"wrote {manifest_path}")
        return 0
    except CaptureError as exc:
        print(f"[capture] FAILED: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("[capture] interrupted; cleaned up", file=sys.stderr)
        return 130
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
