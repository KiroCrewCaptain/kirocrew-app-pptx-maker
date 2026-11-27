"""A failed engine install names its cause in the outcome, the audit row and ``GET /engine``.

Each failing step appends its cause to the provisioning log, and ``install_engine``
then appends a consequence line ("keeping the existing one"). The cause is the last
entry that is not one of those consequence lines.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import tarfile
from pathlib import Path
from unittest import mock

import pytest
from aiohttp.test_utils import make_mocked_request

from backend import engine, engine_source, provision, routes

_UV_ERROR = (
    "error: Distribution `pywin32==311 @ registry+https://pypi.org/simple` can't be "
    "installed because it doesn't have a source distribution or wheel for the current "
    "platform"
)
# Shaped like the uv output in the report, with its `error:` line last.
_UV_RESOLVE_OUTPUT = (
    "Using CPython 3.15.2\nCreating virtual environment at: mcp-local/.venv\n" + _UV_ERROR
)
# What uv prints first when it is handed an interpreter path.
_UV_PATH_PREAMBLE = (
    "Using CPython 3.12.16 interpreter at: "
    "C:\\Users\\someone\\AppData\\Local\\Programs\\Kiro Crew\\resources\\python\\python.exe\n"
    "Creating virtual environment at: mcp-local\\.venv\n"
)


def _engine_tar(*, with_mcp_local: bool = True) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        names = ["repo-abc123"] + (["repo-abc123/mcp-local"] if with_mcp_local else [])
        for name in names:
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            tar.addfile(info)
    return buf.getvalue()


def _fetch(data: bytes | None, error: str = ""):
    def fake(archive: Path) -> tuple[bool, str]:
        if data is None:
            return False, error
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(data)
        return True, ""

    return mock.patch.object(engine_source, "download_archive", side_effect=fake)


@pytest.fixture
def ladder(tmp_path: Path):
    """The real provisioning ladder with the download, uv and the deck seed faked."""
    with (
        mock.patch.object(provision.paths, "engine_root", return_value=tmp_path / "engine"),
        mock.patch.object(provision, "resolve_uv", return_value="/opt/uv"),
        mock.patch.object(provision, "_seed_deck_root"),
    ):
        yield tmp_path


_CASES = {
    "uv missing": (None, None, [], "`uv` could not be found"),
    "download refused": (None, "REFUSING the engine archive: timeout", [], "REFUSING"),
    "archive without mcp-local": (
        _engine_tar(with_mcp_local=False),
        None,
        [],
        "the engine archive has no mcp-local directory",
    ),
    "dependency resolve in staging": (
        _engine_tar(),
        None,
        [(2, _UV_RESOLVE_OUTPUT)],
        "dependency resolve failed: error: Distribution `pywin32==311",
    ),
    "skill install in staging": (
        _engine_tar(),
        None,
        [(0, ""), (1, "skill boom")],
        "skill package install failed: skill boom",
    ),
    "relink at the final path": (
        _engine_tar(),
        None,
        [(0, ""), (0, ""), (1, "relink boom")],
        "skill package install failed: relink boom",
    ),
}


@pytest.mark.parametrize("case", list(_CASES))
def test_a_failed_provision_names_the_failing_step(ladder: Path, case: str) -> None:
    tar, refusal, uv_results, cause = _CASES[case]
    with contextlib.ExitStack() as stack:
        stack.enter_context(_fetch(tar, refusal or ""))
        stack.enter_context(mock.patch.object(provision, "_run", side_effect=uv_results))
        if case == "uv missing":
            stack.enter_context(mock.patch.object(provision, "resolve_uv", return_value=None))
        outcome = provision.provision()
    assert outcome.ok is False
    assert outcome.error.startswith(cause)
    assert engine_source.BUILD_FAILED not in outcome.error
    assert engine_source.FINALIZE_FAILED not in outcome.error


def test_a_missing_venv_after_a_pinned_tree_names_its_cause(ladder: Path) -> None:
    with mock.patch.object(provision, "_ensure_engine", return_value=True):
        outcome = provision.provision()
    assert outcome.ok is False
    assert outcome.error == "the engine checkout has no mcp-local directory"


def test_an_os_error_during_install_names_its_cause(ladder: Path) -> None:
    with _fetch(_engine_tar()), mock.patch.object(
        engine_source, "write_source_marker", side_effect=PermissionError("read-only volume")
    ):
        outcome = provision.provision()
    assert outcome.ok is False
    assert outcome.error == "engine install failed: read-only volume"


def test_a_failed_rollback_reports_where_the_previous_engine_is(ladder: Path) -> None:
    """The relink cause is followed by the rollback failure, the message to act on."""
    with (
        _fetch(_engine_tar()),
        mock.patch.object(provision, "_run", side_effect=[(0, ""), (0, ""), (1, "relink boom")]),
        mock.patch.object(engine_source, "_restore_previous", return_value=False),
    ):
        outcome = provision.provision()
    assert outcome.ok is False
    assert outcome.error.startswith(
        "engine install failed: the previous engine could not be restored"
    )


def test_a_successful_provision_has_no_error(ladder: Path) -> None:
    with (
        mock.patch.object(provision, "_ensure_engine", return_value=True),
        mock.patch.object(provision, "_venv_ready", return_value=True),
        mock.patch.object(provision, "_current_tag", return_value="v0.3.8"),
        mock.patch("backend.preview_tools.install_pdftoppm", return_value=(True, "ready")),
        mock.patch("backend.engine.scan_new_templates", return_value=[]),
    ):
        outcome = provision.provision()
    assert outcome.ok is True
    assert outcome.error == ""


@pytest.fixture
def engine_state(monkeypatch: pytest.MonkeyPatch) -> engine.ProvisionState:
    state = engine.ProvisionState()
    monkeypatch.setattr(routes, "_engine_state", state)
    return state


def _get_engine() -> dict:
    with mock.patch.object(engine, "engine_status", return_value={"ready": False}):
        resp = asyncio.run(routes._handle_engine(make_mocked_request("GET", "/engine")))
    return json.loads(resp.body)["provision"]


def test_the_failed_audit_row_and_the_engine_status_carry_the_cause(
    ladder: Path, engine_state, app_ctx, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="kirocrew.app.pptx-maker")
    with (
        _fetch(_engine_tar()),
        mock.patch.object(provision, "_run", side_effect=[(2, _UV_RESOLVE_OUTPUT)]),
    ):
        routes._run_provision()

    failed = [row for row in app_ctx.audit.rows if row["outcome"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["operation"] == "pptx-maker.engine_provision"
    assert "pywin32==311" in failed[0]["error"]
    assert any("pywin32==311" in r.getMessage() for r in caplog.records)

    served = _get_engine()
    assert served["state"] == "error"
    assert "can't be installed" in served["error"]


def test_a_successful_run_serves_no_error(engine_state, app_ctx) -> None:
    outcome = provision.ProvisionOutcome(ok=True, log="engine ready", engine_tag="v0.3.8")
    with mock.patch.object(provision, "provision", return_value=outcome):
        routes._run_provision()
    assert app_ctx.audit.rows[-1]["outcome"] == "ok"
    assert app_ctx.audit.rows[-1]["error"] == ""
    assert _get_engine()["error"] == ""


def test_a_raised_provision_serves_its_exception(engine_state, app_ctx) -> None:
    with mock.patch.object(provision, "provision", side_effect=RuntimeError("no sandbox")):
        routes._run_provision()
    assert app_ctx.audit.rows[-1]["error"] == "no sandbox"
    assert _get_engine()["error"] == "no sandbox"


def test_a_long_error_is_served_as_its_tail(engine_state) -> None:
    engine_state.state = "error"
    engine_state.error = "dependency resolve failed: " + "x" * engine.LOG_TAIL_CHARS + "hint: end"
    served = _get_engine()["error"]
    assert len(served) == engine.LOG_TAIL_CHARS
    assert served.endswith("hint: end")


def test_a_new_run_clears_the_previous_error(engine_state) -> None:
    engine_state.state = "error"
    engine_state.error = "dependency resolve failed: boom"
    request = make_mocked_request("POST", "/engine/provision")
    request["app"] = "pptx-maker"
    with mock.patch.object(routes, "_run_in_background", side_effect=lambda coro: coro.close()):
        resp = asyncio.run(routes._handle_engine_provision(request))
    assert resp.status == 202
    assert engine_state.state == "running"
    assert engine_state.error == ""


def test_the_warning_for_a_long_failure_is_its_tail(
    engine_state, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="kirocrew.app.pptx-maker")
    cause = "dependency resolve failed: " + "x" * (3 * engine.LOG_TAIL_CHARS) + "hint: end"
    outcome = provision.ProvisionOutcome(ok=False, log=cause, error=cause)
    with mock.patch.object(provision, "provision", return_value=outcome):
        routes._run_provision()
    (record,) = [r for r in caplog.records if "provisioning failed" in r.getMessage()]
    assert record.getMessage().endswith("hint: end")
    assert len(record.getMessage()) < 2 * engine.LOG_TAIL_CHARS


def test_a_uv_preamble_does_not_push_the_cause_past_the_audit_cut(
    ladder: Path, engine_state, app_ctx
) -> None:
    with (
        _fetch(_engine_tar()),
        mock.patch.object(provision, "_run", side_effect=[(2, _UV_PATH_PREAMBLE + _UV_ERROR)]),
    ):
        routes._run_provision()
    (failed,) = [row for row in app_ctx.audit.rows if row["outcome"] == "failed"]
    assert failed["error"].startswith("dependency resolve failed: error: Distribution")
    assert "pywin32==311" in failed["error"]
    assert "Using CPython 3.12.16 interpreter at" in engine_state.log


@pytest.mark.parametrize(
    ("entry", "cause"),
    [
        ("skill package install failed: boom", "skill package install failed: boom"),
        ("engine install failed: no error: here", "engine install failed: no error: here"),
        (
            "skill package install failed: Resolved 3 packages\nerror: boom\nhint: retry",
            "skill package install failed: error: boom\nhint: retry",
        ),
        (
            "skill package install failed: \x1b[2mResolved 1 package\x1b[0m\n"
            "\x1b[31m\x1b[1merror\x1b[0m: Failed to build `p`",
            "skill package install failed: error: Failed to build `p`",
        ),
    ],
)
def test_the_cause_starts_at_the_first_error_line(entry: str, cause: str) -> None:
    assert provision._cause(entry) == cause
