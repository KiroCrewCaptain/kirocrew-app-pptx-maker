"""Every host control PPTX Maker reaches through ``backend/_host.py`` fails closed.

The gateway provides the App Kit context (owner gate, redaction, audit, data
directory) and, for now, the security controls no seam exposes yet (the sandbox
chokepoint, the verified downloader, the sensitive-path gate, the pinned reader,
the raster sniffer). When one is missing, the operation that needs it is refused,
never run without it, and ``register_routes`` says why through ``ctx.health``.
"""

from __future__ import annotations

import base64
import logging
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from backend import _host, decks, engine, library, paths, routes
from fakehost import FakeCtx

_FAKE_AKIA = "AKIAIOSFODNN7EXAMPLE"
_PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4


class TestTheContext:
    def test_the_data_dir_is_the_contexts(self, app_ctx: FakeCtx) -> None:
        assert paths.app_root() == app_ctx.data_dir
        assert paths.engine_root() == app_ctx.data_dir / "vendor" / "sdpm"
        assert paths.preview_tools_root() == app_ctx.data_dir / "vendor" / "preview-tools"

    def test_no_bound_context_means_no_data_dir(self) -> None:
        """A guess would put the engine somewhere the gateway does not manage."""
        _host.bind(None)
        with pytest.raises(RuntimeError, match="no app context"):
            paths.engine_root()

    def test_the_sdk_level_is_read_defensively(self) -> None:
        assert _host.sdk_level(object()) == 0
        assert _host.sdk_level(mock.Mock(sdk_level=True)) == 0
        assert _host.sdk_level(mock.Mock(sdk_level="1")) == 0
        assert _host.sdk_level(mock.Mock(sdk_level=1)) == 1

    def test_a_complete_gateway_reports_no_issue(self, app_ctx: FakeCtx) -> None:
        assert _host.capability_issues(app_ctx) == []

    @pytest.mark.parametrize(
        "module,name,phrase",
        [
            ("kiro_crew.sandbox", "sandboxed_spawn_argv", "sandboxed process launch"),
            ("kiro_crew.sandbox", "run_limited", "sandboxed process launch"),
            ("kiro_crew.asset_downloader", "download_to", "verified downloader"),
            ("kiro_crew.security", "is_sensitive_path", "sensitive-path gate"),
            ("kiro_crew.security", "path_contains_sensitive", "sensitive-path gate"),
            ("kiro_crew.hooks", "safe_read_file_bytes_nolink", "pinned file reader"),
            ("kiro_crew.messaging.raster", "sniff_raster_mime", "raster sniffer"),
        ],
    )
    def test_each_missing_control_is_reported_on_registration(
        self, fake_host, app_ctx: FakeCtx, module: str, name: str, phrase: str
    ) -> None:
        fake_host.remove(module, name)
        routes.register_routes(app_ctx)
        assert app_ctx.health.status == "degraded"
        assert any(phrase in issue for issue in app_ctx.health.issues), app_ctx.health.issues


class TestRedaction:
    def test_text_goes_through_the_contexts_scrubber(self, app_ctx: FakeCtx) -> None:
        assert _host.redact(f"key {_FAKE_AKIA}") == "key [REDACTED: credential]"
        assert app_ctx.scrub.calls == 1

    def test_no_scrubber_withholds_the_text(self, app_ctx: FakeCtx) -> None:
        app_ctx.scrub = None  # type: ignore[assignment]
        assert _host.redact("an ordinary brief") == _host.WITHHELD

    def test_a_raising_scrubber_withholds_the_text(self, app_ctx: FakeCtx) -> None:
        """A host whose companion policy could not compose raises on purpose."""
        app_ctx.scrub.raises = RuntimeError("companion failed to compose")
        assert _host.redact("an ordinary brief") == _host.WITHHELD

    def test_a_withheld_deck_id_is_refused_not_served(
        self, app_ctx: FakeCtx, tmp_path, monkeypatch
    ) -> None:
        root = tmp_path / "decks"
        (root / "20260101-demo").mkdir(parents=True)
        monkeypatch.setenv(paths.DECK_ROOT_ENV, str(root))
        assert paths.resolve_deck_dir("20260101-demo") is not None
        app_ctx.scrub = None  # type: ignore[assignment]
        assert paths.resolve_deck_dir("20260101-demo") is None

    def test_a_served_artifact_is_withheld_without_a_scrubber(self, app_ctx: FakeCtx) -> None:
        app_ctx.scrub = None  # type: ignore[assignment]
        out = routes._redact_artifact(b'{"text": "an ordinary slide"}').decode("utf-8")
        assert "ordinary slide" not in out
        assert _host.WITHHELD in out


class TestSandbox:
    def test_engine_spawns_go_through_the_chokepoint_in_strict_mode(
        self, fake_host, tmp_path
    ) -> None:
        with mock.patch.object(_host, "_host_attr", fake_host.attr):
            result = engine._spawn(
                [sys.executable, "-c", "print('hi')"], cwd=str(tmp_path), timeout=30
            )
        assert result.returncode == 0 and result.stdout.strip() == "hi"
        call = fake_host.sandbox.calls[-1]
        assert call.mode == "strict" and call.kwargs["strip_python_env"] is True

    def test_no_chokepoint_spawns_nothing(self, fake_host, tmp_path) -> None:
        fake_host.remove("kiro_crew.sandbox", "sandboxed_spawn_argv")
        with mock.patch.object(subprocess, "run") as run:
            with pytest.raises(_host.SandboxUnavailableError):
                engine._spawn([sys.executable, "-c", "print('hi')"], cwd=str(tmp_path), timeout=30)
        assert not run.called

    def test_the_hosts_refusal_is_translated(self, fake_host, tmp_path) -> None:
        refusal = fake_host.table[("kiro_crew.sandbox", "SandboxUnavailableError")]
        fake_host.sandbox.refuse = refusal("no backend here", kind="no_backend")
        with pytest.raises(_host.SandboxUnavailableError) as caught:
            engine._spawn([sys.executable, "-c", "print('hi')"], cwd=str(tmp_path), timeout=30)
        assert caught.value.kind == "no_backend"

    def test_no_resource_limited_runner_spawns_nothing(self, fake_host, tmp_path) -> None:
        fake_host.remove("kiro_crew.sandbox", "run_limited")
        with mock.patch.object(subprocess, "run") as run:
            with pytest.raises(_host.SandboxUnavailableError):
                engine._spawn([sys.executable, "-c", "print('hi')"], cwd=str(tmp_path), timeout=30)
        assert not run.called

    def test_the_subprocess_pool_is_the_gateways(self, fake_host) -> None:
        pool = object()
        fake_host.table[("kiro_crew.executors", "subprocess_executor")] = lambda: pool
        assert _host.subprocess_executor() is pool
        fake_host.remove("kiro_crew.executors", "subprocess_executor")
        assert _host.subprocess_executor() is None


class TestPathAndReadGates:
    def test_no_sensitive_path_gate_refuses_every_path(
        self, fake_host, tmp_path, monkeypatch
    ) -> None:
        root = tmp_path / "decks"
        (root / "20260101-demo").mkdir(parents=True)
        monkeypatch.setenv(paths.DECK_ROOT_ENV, str(root))
        fake_host.remove("kiro_crew.security", "is_sensitive_path")
        assert paths.resolve_deck_dir("20260101-demo") is None

    def test_no_containment_predicate_refuses_a_deck_root(self, fake_host, tmp_path) -> None:
        fake_host.remove("kiro_crew.security", "path_contains_sensitive")
        status, payload = routes._write_deck_root(str(tmp_path / "decks"))
        assert status == 400 and payload["code"] == "invalid_deck_root"

    def test_no_pinned_reader_reads_nothing(self, fake_host, tmp_path) -> None:
        deck = tmp_path / "deck"
        (deck / "specs").mkdir(parents=True)
        (deck / "specs" / "brief.md").write_text("the brief", encoding="utf-8")
        assert decks._read_deck_text(deck / "specs" / "brief.md", deck) == "the brief"
        fake_host.remove("kiro_crew.hooks", "safe_read_file_bytes_nolink")
        assert decks._read_deck_text(deck / "specs" / "brief.md", deck) is None
        assert library._read_style_text(deck / "specs" / "brief.md", deck) is None

    def test_the_hosts_too_large_error_is_translated(self, fake_host, tmp_path) -> None:
        host_error = fake_host.table[("kiro_crew.hooks", "FileTooLargeError")]
        fake_host.table[("kiro_crew.hooks", "safe_read_file_bytes_nolink")] = mock.Mock(
            side_effect=host_error("too big")
        )
        with pytest.raises(_host.FileTooLargeError):
            _host.safe_read_file_bytes_nolink(str(tmp_path / "x"), within_root=str(tmp_path))


class TestInlineBitmaps:
    def _doc(self) -> str:
        body = base64.b64encode(_PNG).decode("ascii")
        return f'<img src="data:image/png;base64,{body}">'

    def test_a_real_raster_is_kept(self) -> None:
        doc = self._doc()
        assert routes._redact_artifact(doc.encode("utf-8")).decode("utf-8") == doc

    def test_no_raster_sniffer_means_no_exemption(self, fake_host) -> None:
        """The blob is then ordinary text: scanned, and here eaten as a bare secret."""
        fake_host.remove("kiro_crew.messaging.raster", "sniff_raster_mime")
        out = routes._redact_artifact(self._doc().encode("utf-8")).decode("utf-8")
        assert "REDACTED" in out

    def test_no_key_pattern_blanks_the_picture(self, fake_host) -> None:
        """Without the gateway's AWS key pattern the encoded body cannot be cleared,
        so the picture is dropped rather than served unscanned."""
        fake_host.remove("kiro_crew.credential_patterns", "AWS_KEY_ID")
        out = routes._redact_artifact(self._doc().encode("utf-8")).decode("utf-8")
        assert "base64,[REDACTED: credential]" in out


class TestDownloadAndAudit:
    def test_no_downloader_fetches_nothing(self, fake_host, tmp_path) -> None:
        fake_host.remove("kiro_crew.asset_downloader", "download_to")
        ok, error = _host.download_to(tmp_path / "e.tar.gz", "https://example.invalid/e")
        assert ok is False and "verified downloader" in error

    def test_audit_rows_go_through_the_context(self, app_ctx: FakeCtx) -> None:
        _host.audit("style_import", "ok", resources=f"brand {_FAKE_AKIA}")
        row = app_ctx.audit.rows[-1]
        assert row["operation"] == "pptx-maker.style_import"
        assert _FAKE_AKIA not in row["resources"]

    def test_no_audit_sink_logs_locally(self, app_ctx: FakeCtx, caplog) -> None:
        app_ctx.audit = None  # type: ignore[assignment]
        with caplog.at_level(logging.INFO, logger="kirocrew.app.pptx-maker"):
            _host.audit("style_import", "ok", resources="brand")
        assert "no audit sink" in caplog.text

    def test_a_failing_audit_sink_never_breaks_the_operation(self, app_ctx: FakeCtx) -> None:
        app_ctx.audit = mock.Mock(record=mock.Mock(side_effect=RuntimeError("log full")))
        _host.audit("style_import", "ok", resources="brand")


def test_the_host_lookup_treats_any_import_failure_as_absent(real_host_attr) -> None:
    """The real lookup, not the fake: a module that does not import is "not provided"."""
    real = real_host_attr
    assert real("kiro_crew_missing_module", "anything") is None
    assert real("pathlib", "no_such_name") is None
    assert real("pathlib", "Path") is Path
