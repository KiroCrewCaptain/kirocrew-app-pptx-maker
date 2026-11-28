"""Every host control PPTX Maker reaches through ``backend/_host.py`` fails closed.

The gateway provides the App Kit context (owner gate, redaction, audit, data
directory, and from level 20 the verified download) and the public backend SDK
(the sandboxed run, the sensitive-path gate and the no-follow reader at level 7,
the raster sniffer at 14, the exfil and credential scans at 17, the ``uv`` and
agent-template values at 18). When one is missing, or the SDK reports a level
below the one a name arrived at, the operation that needs it is refused, never
run without it, and ``register_routes`` says why through ``ctx.health``. No
private ``kiro_crew`` name is ever asked.
"""

from __future__ import annotations

import base64
import logging
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from backend import _host, decks, engine, engine_source, library, paths, routes
from fakehost import BACKEND_SDK, CREDENTIAL_PLACEHOLDER, FakeCtx, VerifiedDownloadError

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
        "name,phrase",
        [
            ("run_sandboxed", "sandboxed process launch"),
            ("is_sensitive_path", "sensitive-path gate"),
            ("contains_sensitive_path", "sensitive-path gate"),
            ("read_bytes_nofollow", "pinned file reader"),
            ("sniff_raster_mime", "raster sniffer"),
            ("SDK_LEVEL", "raster sniffer"),
            ("SDK_LEVEL", "sandboxed process launch"),
            ("contains_exfiltration_url", "exfil scan"),
            ("iter_credential_matches", "credential scan"),
        ],
    )
    def test_each_missing_control_is_reported_on_registration(
        self, fake_host, app_ctx: FakeCtx, name: str, phrase: str
    ) -> None:
        fake_host.remove(name)
        routes.register_routes(app_ctx)
        assert app_ctx.health.status == "degraded"
        assert any(phrase in issue for issue in app_ctx.health.issues), app_ctx.health.issues

    def test_a_context_without_the_verified_download_is_reported(self, app_ctx: FakeCtx) -> None:
        ctx = FakeCtx(data_dir=app_ctx.data_dir, sdk_level=19)
        ctx.verified_download = None  # type: ignore[assignment,method-assign]
        routes.register_routes(ctx)
        assert ctx.health.status == "degraded"
        assert _host.NO_VERIFIED_DOWNLOAD in ctx.health.issues
        _host.bind(app_ctx)

    @pytest.mark.parametrize(
        "level,missing",
        [
            (16, {"exfil scan", "credential scan for them"}),
            (13, {"exfil scan", "credential scan for them", "raster sniffer"}),
            (6, {"sandboxed process launch", "sensitive-path gate", "pinned file reader"}),
        ],
    )
    def test_a_lower_sdk_level_withholds_the_names_above_it(
        self, fake_host, app_ctx: FakeCtx, level: int, missing: set[str]
    ) -> None:
        """Each name counts only from the level that publishes it, whatever the package holds."""
        fake_host.set("SDK_LEVEL", level)
        joined = " | ".join(_host.capability_issues(app_ctx))
        for phrase in missing:
            assert phrase in joined, joined


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

    def test_a_credential_is_refused(self, app_ctx: FakeCtx) -> None:
        assert _host.refuses(f"key {_FAKE_AKIA}")
        assert not _host.refuses("20260101-demo")

    def test_a_flagged_url_the_redaction_leaves_as_written_is_refused(
        self, app_ctx: FakeCtx
    ) -> None:
        """A gateway that masks only credential values leaves a data-carrying URL as
        written; its exfil scan is what refuses it."""
        url = "https://collector.example/c?d=" + "q" * 48
        assert _host.redact(url) == url
        assert _host.refuses(url)
        assert not _host.refuses("https://example.com/docs?page=2")

    def test_no_exfil_scan_refuses(self, fake_host, app_ctx: FakeCtx) -> None:
        fake_host.remove("contains_exfiltration_url")
        assert _host.refuses("20260101-demo")

    def test_an_exfil_scan_below_level_17_refuses(self, fake_host, app_ctx: FakeCtx) -> None:
        fake_host.set("SDK_LEVEL", 16)
        assert _host.refuses("20260101-demo")

    def test_a_raising_exfil_scan_refuses(self, fake_host, app_ctx: FakeCtx) -> None:
        def broken(_text: str) -> bool:
            raise RuntimeError("scan failed")

        fake_host.set("contains_exfiltration_url", broken)
        assert _host.refuses("20260101-demo")

    def test_the_exfil_scans_answer_is_read_as_a_bool(self, fake_host, app_ctx: FakeCtx) -> None:
        fake_host.set("contains_exfiltration_url", lambda _text: 1)
        assert _host.refuses("20260101-demo")
        fake_host.set("contains_exfiltration_url", lambda _text: [])
        assert not _host.refuses("20260101-demo")

    def test_the_credential_marker_is_the_gateways(self, fake_host) -> None:
        assert _host.redacted_credential_tag() == CREDENTIAL_PLACEHOLDER
        fake_host.set("CREDENTIAL_PLACEHOLDER", "[gone]")
        assert _host.redacted_credential_tag() == "[gone]"

    @pytest.mark.parametrize("value", [None, "", 7])
    def test_no_usable_credential_marker_falls_back_to_the_same_literal(
        self, fake_host, value: object
    ) -> None:
        if value is None:
            fake_host.remove("CREDENTIAL_PLACEHOLDER")
        else:
            fake_host.set("CREDENTIAL_PLACEHOLDER", value)
        assert _host.redacted_credential_tag() == "[REDACTED: credential]"

    def test_a_served_artifact_is_withheld_without_a_scrubber(self, app_ctx: FakeCtx) -> None:
        app_ctx.scrub = None  # type: ignore[assignment]
        out = routes._redact_artifact(b'{"text": "an ordinary slide"}').decode("utf-8")
        assert "ordinary slide" not in out
        assert _host.WITHHELD in out


class TestSandbox:
    def test_engine_spawns_go_through_the_sandboxed_run_in_strict_mode(
        self, fake_host, tmp_path
    ) -> None:
        result = engine._spawn([sys.executable, "-c", "print('hi')"], cwd=str(tmp_path), timeout=30)
        assert result.returncode == 0 and result.stdout.strip() == "hi"
        call = fake_host.sandbox.calls[-1]
        assert call.mode == "strict" and call.kwargs["strip_python_env"] is True
        assert call.argv == [sys.executable, "-c", "print('hi')"]

    def test_no_sandboxed_run_spawns_nothing(self, fake_host, tmp_path) -> None:
        fake_host.remove("run_sandboxed")
        with mock.patch.object(subprocess, "run") as run:
            with pytest.raises(_host.SandboxUnavailableError):
                engine._spawn([sys.executable, "-c", "print('hi')"], cwd=str(tmp_path), timeout=30)
        assert not run.called

    def test_a_package_below_level_7_spawns_nothing(self, fake_host, tmp_path) -> None:
        fake_host.set("SDK_LEVEL", 6)
        with mock.patch.object(subprocess, "run") as run:
            with pytest.raises(_host.SandboxUnavailableError):
                engine._spawn([sys.executable, "-c", "print('hi')"], cwd=str(tmp_path), timeout=30)
        assert not run.called
        assert fake_host.sandbox.calls == []

    def test_the_hosts_refusal_is_translated(self, fake_host, tmp_path) -> None:
        refusal = fake_host.attr(BACKEND_SDK, "SandboxUnavailableError")
        fake_host.sandbox.refuse = refusal("no backend here", kind="no_backend")
        with pytest.raises(_host.SandboxUnavailableError) as caught:
            engine._spawn([sys.executable, "-c", "print('hi')"], cwd=str(tmp_path), timeout=30)
        assert caught.value.kind == "no_backend"

    def test_only_the_forwarded_run_keywords_are_passed(self, fake_host, tmp_path) -> None:
        """The SDK refuses any other keyword, so a call site naming one would spawn nothing."""
        engine._spawn([sys.executable, "-c", "print('hi')"], cwd=str(tmp_path), timeout=30)
        call = fake_host.sandbox.calls[-1]
        passed = set(call.kwargs) - {"env", "strip_python_env"}
        assert passed <= {"cwd", "capture_output", "text", "encoding", "errors", "timeout", "check"}
        assert call.kwargs["env"] is None, "the host scrubs its own environment"

    def test_no_private_executor_is_asked(self) -> None:
        """Blocking work runs on the loop's default pool, which the gateway configures."""
        assert not hasattr(_host, "subprocess_executor")


class TestPathAndReadGates:
    def test_no_sensitive_path_gate_refuses_every_path(
        self, fake_host, tmp_path, monkeypatch
    ) -> None:
        root = tmp_path / "decks"
        (root / "20260101-demo").mkdir(parents=True)
        monkeypatch.setenv(paths.DECK_ROOT_ENV, str(root))
        fake_host.remove("is_sensitive_path")
        assert paths.resolve_deck_dir("20260101-demo") is None

    def test_no_containment_predicate_refuses_a_deck_root(self, fake_host, tmp_path) -> None:
        fake_host.remove("contains_sensitive_path")
        status, payload = routes._write_deck_root(str(tmp_path / "decks"))
        assert status == 400 and payload["code"] == "invalid_deck_root"

    def test_no_pinned_reader_reads_nothing(self, fake_host, tmp_path) -> None:
        deck = tmp_path / "deck"
        (deck / "specs").mkdir(parents=True)
        (deck / "specs" / "brief.md").write_text("the brief", encoding="utf-8")
        assert decks._read_deck_text(deck / "specs" / "brief.md", deck) == "the brief"
        fake_host.remove("read_bytes_nofollow")
        assert decks._read_deck_text(deck / "specs" / "brief.md", deck) is None
        assert library._read_style_text(deck / "specs" / "brief.md", deck) is None

    def test_the_read_is_handed_its_root_and_cap_by_keyword(self, fake_host, tmp_path) -> None:
        reader = mock.Mock(return_value=b"x")
        fake_host.set("read_bytes_nofollow", reader)
        assert _host.read_bytes_nofollow("/d/a.md", within_root="/d", max_bytes=9) == b"x"
        reader.assert_called_once_with("/d/a.md", within_root="/d", max_bytes=9)

    def test_the_hosts_too_large_error_is_translated(self, fake_host, tmp_path) -> None:
        host_error = fake_host.attr(BACKEND_SDK, "FileTooLargeError")
        fake_host.set("read_bytes_nofollow", mock.Mock(side_effect=host_error("too big")))
        with pytest.raises(_host.FileTooLargeError):
            _host.read_bytes_nofollow(str(tmp_path / "x"), within_root=str(tmp_path))


class TestInlineBitmaps:
    def _doc(self) -> str:
        body = base64.b64encode(_PNG).decode("ascii")
        return f'<img src="data:image/png;base64,{body}">'

    def test_a_real_raster_is_kept(self) -> None:
        doc = self._doc()
        assert routes._redact_artifact(doc.encode("utf-8")).decode("utf-8") == doc

    def test_no_raster_sniffer_means_no_exemption(self, fake_host) -> None:
        """The blob is then ordinary text: scanned, and here eaten as a bare secret."""
        fake_host.remove("sniff_raster_mime")
        out = routes._redact_artifact(self._doc().encode("utf-8")).decode("utf-8")
        assert "REDACTED" in out

    def test_the_encoded_body_is_scanned_by_the_gateways_credential_scan(self, fake_host) -> None:
        """An AWS key id glued into a real raster's body survives the re-encode, so the
        ENCODED form is screened, with the gateway's own scan rather than a copy."""
        seen: list[str] = []

        def scan(text: str):
            seen.append(text)
            return iter(())

        fake_host.set("iter_credential_matches", scan)
        doc = self._doc()
        assert routes._redact_artifact(doc.encode("utf-8")).decode("utf-8") == doc
        assert base64.b64encode(_PNG).decode("ascii") in seen

    def test_a_key_id_inside_the_body_blanks_the_picture(self) -> None:
        body = base64.b64encode(_PNG).decode("ascii") + _FAKE_AKIA
        out = routes._redact_artifact(
            f'<img src="data:image/png;base64,{body}">'.encode("utf-8")
        ).decode("utf-8")
        assert _FAKE_AKIA not in out
        assert f"base64,{CREDENTIAL_PLACEHOLDER}" in out

    @pytest.mark.parametrize("how", ["missing", "below_17", "raises"])
    def test_no_usable_credential_scan_blanks_the_picture(self, fake_host, how: str) -> None:
        """Without the gateway's scan the encoded body cannot be cleared, so the picture
        is dropped rather than served unscanned."""
        if how == "missing":
            fake_host.remove("iter_credential_matches")
        elif how == "below_17":
            fake_host.set("SDK_LEVEL", 16)
        else:
            fake_host.set("iter_credential_matches", mock.Mock(side_effect=RuntimeError("x")))
        out = routes._redact_artifact(self._doc().encode("utf-8")).decode("utf-8")
        assert "base64,[REDACTED: credential]" in out

    def test_the_holds_credential_answer(self, fake_host) -> None:
        assert _host.holds_credential(f"abc{_FAKE_AKIA}def")
        assert not _host.holds_credential("iVBORw0KGgoAAAANSUhEUgAA")
        fake_host.remove("iter_credential_matches")
        assert _host.holds_credential("iVBORw0KGgoAAAANSUhEUgAA")


class TestRasterSniffThroughTheBackendSdk:
    """The sniff is the public backend SDK's (App Kit level 14), never a private name."""

    _PRIVATE = ("kiro_crew.messaging.raster", "sniff_raster_mime")
    _PUBLIC = (BACKEND_SDK, "sniff_raster_mime")

    def test_the_sdk_sniffer_answers_from_the_whole_blob(self, fake_host) -> None:
        seen: list[bytes] = []

        def sniff(head: bytes) -> str | None:
            seen.append(head)
            return "image/png" if head.startswith(b"\x89PNG") else None

        fake_host.table[self._PUBLIC] = sniff
        assert _host.sniff_raster_mime(_PNG) == "image/png"
        assert seen == [_PNG]

    def test_a_private_sniffer_is_never_asked(self, fake_host) -> None:
        """A gateway that still ships the private module but no SDK name: refused."""
        asked = mock.Mock(return_value="image/png")
        fake_host.table[self._PRIVATE] = asked
        fake_host.remove("sniff_raster_mime")
        assert _host.sniff_raster_mime(_PNG) is None
        asked.assert_not_called()

    @pytest.mark.parametrize("level", [None, 0, 7, 13, True, "14"])
    def test_a_gateway_below_level_14_refuses(
        self, fake_host, app_ctx: FakeCtx, level: object
    ) -> None:
        """The level is checked as well as the name; an answer that is no integer is no level."""
        if level is None:
            fake_host.remove("SDK_LEVEL")
        else:
            fake_host.set("SDK_LEVEL", level)
        assert _host.sniff_raster_mime(_PNG) is None
        assert _host.NO_RASTER_SNIFFER in _host.capability_issues(app_ctx)

    def test_a_non_string_answer_is_not_a_raster(self, fake_host) -> None:
        fake_host.table[self._PUBLIC] = lambda _head: b"image/png"
        assert _host.sniff_raster_mime(_PNG) is None

    def test_the_shim_names_no_private_raster_module(self) -> None:
        source = (Path(_host.__file__)).read_text(encoding="utf-8")
        assert "kiro_crew.messaging" not in source


class TestDownloadAndAudit:
    def test_no_verified_download_fetches_nothing(self, app_ctx: FakeCtx) -> None:
        app_ctx.verified_download = None  # type: ignore[assignment,method-assign]
        ok, error = _host.verified_download(
            app_ctx.data_dir / "e.tar.gz", "https://example.invalid/e", sha256="0" * 64, size=1
        )
        assert ok is False and "verified download" in error and "level 20" in error

    def test_the_download_is_handed_a_path_relative_to_the_data_dir(self, app_ctx: FakeCtx) -> None:
        (app_ctx.data_dir / "vendor").mkdir()
        app_ctx.download_refusal = VerifiedDownloadError("refused for the test")
        ok, error = _host.verified_download(
            app_ctx.data_dir / "vendor" / "e.tar.gz",
            "https://example.invalid/e",
            sha256="a" * 64,
            size=12,
        )
        assert (ok, error) == (False, "refused for the test")
        assert app_ctx.downloads == [
            {
                "url": "https://example.invalid/e",
                "dest": "vendor/e.tar.gz",
                "sha256": "a" * 64,
                "size": 12,
            }
        ]

    def test_a_download_outside_the_data_dir_is_refused_before_the_call(
        self, app_ctx: FakeCtx, tmp_path
    ) -> None:
        ok, error = _host.verified_download(
            tmp_path / "e.tar.gz", "https://example.invalid/e", sha256="a" * 64, size=1
        )
        assert ok is False and "data directory" in error
        assert app_ctx.downloads == []

    @pytest.mark.parametrize("exc", [ValueError("url must be an https:// URL"), TypeError("x")])
    def test_a_refused_argument_is_reported_not_raised(self, app_ctx: FakeCtx, exc) -> None:
        app_ctx.download_refusal = exc
        ok, error = _host.verified_download(
            app_ctx.data_dir / "e.tar.gz", "https://example.invalid/e", sha256="a" * 64, size=1
        )
        assert ok is False and error == str(exc)

    def test_the_engine_fetch_names_the_codeload_tarball_and_its_size(
        self, app_ctx: FakeCtx
    ) -> None:
        """The gateway follows a redirect only on the same host, so the pinned url is
        the host that serves the file, and its exact length is part of the pin."""
        app_ctx.download_refusal = VerifiedDownloadError("refused for the test")
        engine_source.download_archive(app_ctx.data_dir / "engine.tar.gz")
        [call] = app_ctx.downloads
        assert call["url"] == (
            "https://codeload.github.com/aws-samples/sample-spec-driven-presentation-maker"
            f"/tar.gz/{engine_source.ENGINE_COMMIT}"
        )
        assert call["sha256"] == engine_source.ENGINE_TARBALL_SHA256
        assert call["size"] == engine_source.ENGINE_TARBALL_SIZE == 3_159_833

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


class TestTemplateValues:
    """What the gateway renders into the agents' ``{HOST_PATH}`` and ``{UV_BIN}``.

    The backend SDK's ``agent_template_host_values`` (App Kit level 18). Not a
    security control, so a gateway without it is not refused: it renders its own
    ``PATH`` and its resolver's ``uv``, and this app restates that.
    """

    def test_an_older_gateway_offers_no_host_path(self) -> None:
        assert _host.template_host_path() is None

    def test_the_gateways_host_path_is_returned(self, fake_host) -> None:
        fake_host.set("agent_template_host_values", lambda: {"{HOST_PATH}": "/fixed/a:/fixed/b"})
        assert _host.template_host_path() == "/fixed/a:/fixed/b"

    def test_a_host_path_the_gateway_cannot_compute_is_empty(self, fake_host) -> None:
        """The gateway leaves it out and then renders none, so nothing is restated."""
        fake_host.set("agent_template_host_values", lambda: {"{PATH_SEP}": ":"})
        assert _host.template_host_path() == ""

    def test_a_host_path_that_is_not_a_string_reads_as_absent(self, fake_host) -> None:
        fake_host.set("agent_template_host_values", lambda: {"{HOST_PATH}": ["/fixed/a"]})
        assert _host.template_host_path() is None

    def test_an_answer_that_is_not_a_mapping_reads_as_an_older_gateway(self, fake_host) -> None:
        fake_host.set("agent_template_host_values", lambda: ["{HOST_PATH}"])
        assert _host.template_host_path() is None
        assert _host.template_uv_bin() == "/opt/fake-host/bin/uv"

    def test_values_below_level_18_are_not_asked(self, fake_host) -> None:
        asked = mock.Mock(return_value={"{HOST_PATH}": "/fixed"})
        fake_host.set("agent_template_host_values", asked)
        fake_host.set("SDK_LEVEL", 17)
        assert _host.template_host_path() is None
        asked.assert_not_called()

    def test_an_older_gateways_uv_is_its_resolvers(self) -> None:
        assert _host.template_uv_bin() == "/opt/fake-host/bin/uv"

    def test_the_gateways_uv_value_is_returned(self, fake_host) -> None:
        fake_host.set(
            "agent_template_host_values", lambda: {"{UV_BIN}": "/opt/crew-venv-current/bin/uv"}
        )
        assert _host.template_uv_bin() == "/opt/crew-venv-current/bin/uv"

    def test_a_gateway_without_uv_offers_none(self, fake_host) -> None:
        fake_host.set("agent_template_host_values", lambda: {"{PATH_SEP}": ":"})
        assert _host.template_uv_bin() is None


class TestNoPrivateName:
    def test_the_shim_asks_only_the_backend_sdk(self, fake_host, app_ctx: FakeCtx) -> None:
        """Every lookup any capability check makes names the public package."""
        asked: list[str] = []

        def record(module: str, name: str):
            asked.append(module)
            return fake_host.attr(module, name)

        with mock.patch.object(_host, "_host_attr", record):
            _host.capability_issues(app_ctx)
            _host.refuses("20260101-demo")
            _host.holds_credential("abc")
            _host.redacted_credential_tag()
            _host.resolve_uv()
            _host.template_host_path()
        assert asked and set(asked) == {BACKEND_SDK}


def test_the_host_lookup_treats_any_import_failure_as_absent(real_host_attr) -> None:
    """The real lookup, not the fake: a module that does not import is "not provided"."""
    real = real_host_attr
    assert real("kiro_crew_missing_module", "anything") is None
    assert real("pathlib", "no_such_name") is None
    assert real("pathlib", "Path") is Path
