"""Tests for engine provisioning (`backend/provision.py`).

The engine is third-party code fetched at runtime and then BUILT and EXECUTED
(`uv sync` compiles wheels; the app's agents drive the engine), so what decides
which bytes run is the security story of this module. That pin now lives in
``engine_source`` (a sha256 over the received archive) and is tested in
``test_engine_source.py``; what is pinned HERE is the `uv` resolver,
the sandboxed spawn, and provisioning's failure ladder.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from backend import _host, provision


def _json_strings(node: object) -> list[str]:
    """Every string value anywhere in a parsed JSON document.

    Assertions about a rendered path go through this rather than searching the
    raw file text: the substituted value is JSON-escaped, so on Windows the file
    holds `C:\\\\Users\\\\…` while the parsed value holds `C:\\Users\\…`.
    """
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for value in node.values() for s in _json_strings(value)]
    if isinstance(node, list):
        return [s for item in node for s in _json_strings(item)]
    return []


def _same_path(a: str | None, b: Path) -> bool:
    """Whether a ``shutil.which`` result names the same file as *b*.

    Compares identity on the filesystem rather than the two strings, because on
    Windows ``which`` returns the extension as spelled in ``PATHEXT``
    (upper-case ``.CMD``) while the file was created as ``.cmd`` — the same file
    under a case-insensitive filesystem, so a string compare tests the wrong
    thing. ``os.path.samefile`` answers the actual question on every platform;
    ``normcase`` would not, since it is the identity function on POSIX.
    """
    if a is None:
        return False
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _fake_tool(directory: Path, name: str) -> Path:
    """Create an executable stub *name* that ``shutil.which`` can actually find.

    The suffix is not cosmetic. On Windows ``shutil.which`` only resolves a bare
    name against ``PATHEXT``, so an extensionless stub is invisible and the
    assertion fails for a reason that has nothing to do with the ``PATH`` under
    test. Production has the same constraint, which is why ``preview_tools``
    installs ``pdftoppm.cmd`` there.
    """
    suffix = ".cmd" if sys.platform == "win32" else ""
    tool = directory / f"{name}{suffix}"
    tool.write_text("@echo off\r\n" if suffix else "#!/bin/sh\n")
    tool.chmod(0o700)
    return tool


def _declared_placeholders() -> list[str]:
    """Every `PLACEHOLDER_*` the provision module declares.

    Derived rather than listed: the hand-maintained tuple this replaced fell out of
    sync the moment a fourth placeholder was added to the templates.
    """
    return [
        value
        for name, value in vars(provision).items()
        if name.startswith("PLACEHOLDER_") and isinstance(value, str)
    ]


class TestEnginePin:
    def test_the_pinned_commit_is_a_full_sha(self):
        """A short sha is ambiguous and an abbreviation can become non-unique."""
        assert len(provision.ENGINE_COMMIT) == 40
        assert all(c in "0123456789abcdef" for c in provision.ENGINE_COMMIT)

    def test_the_pin_is_re_exported_from_the_verifying_module(self):
        """One pin, one owner: `engine_source` verifies it, so `provision` must
        not carry a second copy that could drift out of step with the digest."""
        from backend import engine_source

        assert provision.ENGINE_COMMIT == engine_source.ENGINE_COMMIT
        assert provision.ENGINE_TAG == engine_source.ENGINE_TAG
        assert provision.ENGINE_REPO == engine_source.ENGINE_REPO

    def test_the_reported_tag_comes_from_the_verified_marker(self, tmp_path: Path):
        """An unverified tree must not be reported as the pinned version."""
        assert provision._current_tag(tmp_path / "absent") == "unknown"

    def test_the_engine_fetch_needs_no_external_binary(self, tmp_path: Path):
        """The whole point of dropping `git clone`: FETCHING the engine spawns no
        external tool, so a host with no `git` can still provision.

        The post-swap editable re-link is stubbed out here rather than allowed to run:
        this test is about the fetch, and `uv` is a declared Python dependency, so
        spawning it is not the "external binary" this guards against. The re-link's own
        behaviour is pinned by `TestEditableInstallSurvivesTheSwap` below.
        """
        log: list[str] = []
        with (
            mock.patch.object(
                provision.engine_source, "install_engine", return_value=True
            ) as install,
            mock.patch.object(provision, "_relink_editable_skill", return_value=True),
            mock.patch.object(provision, "_run") as run,
        ):
            assert provision._ensure_engine(tmp_path, log, "/opt/uv") is True
        assert install.called
        assert not run.called, "the engine fetch must not spawn a subprocess"


class TestEditableInstallSurvivesTheSwap:
    """An `--editable` install records an ABSOLUTE path.

    The venv is deliberately built in the STAGING tree so a broken build never replaces
    a working engine — but the `.pth` that validation writes then names the staging
    directory, which the swap renames and deletes. Every `sdpm` import therefore failed
    with ModuleNotFoundError while provisioning reported SUCCESS.

    Reproduced against real `uv`: after `mv staged engine`, the `.pth` still read
    `/private/tmp/.../staged/skill/src` and the import failed; re-running the editable
    step at the final path rewrote it to `.../engine/skill/src` and the import resolved.

    It is passed as `install_engine`'s `finalize` hook rather than called after
    `install_engine` returns, because the swap deletes the retired tree as soon as the
    new one is in place — relinking afterwards had no rollback state left, so a failure
    there deleted the working engine AND left the new one pointing at a removed path.
    `engine_source`'s own `TestSwapRollback` pins the rollback; these pin the wiring.
    """

    def test_the_relink_is_passed_as_the_finalize_hook(self, tmp_path: Path):
        """Both callbacks matter, and WHICH one they are matters: `validate` gets the
        staging tree, `finalize` gets the final path inside the rollback window."""
        captured: dict[str, object] = {}

        def _fake_install(root, log, validate=None, finalize=None):  # noqa: ANN001
            captured["root"] = root
            captured["validate"] = validate
            captured["finalize"] = finalize
            return True

        with (
            mock.patch.object(provision.engine_source, "install_engine", side_effect=_fake_install),
            mock.patch.object(provision, "_relink_editable_skill", return_value=True) as relink,
        ):
            assert provision._ensure_engine(tmp_path, [], "/opt/uv") is True

            assert captured["finalize"] is not None, "the relink is not wired as `finalize`"
            assert captured["validate"] is not None, "the staged venv build is not validated"

            # Invoked INSIDE the patch, or the real relink runs and shells out to `uv`.
            # The hook must relink at whatever path the swap hands it, not a captured one.
            final = tmp_path / "somewhere-else"
            assert captured["finalize"](final) is True  # type: ignore[operator]
            assert relink.call_args.args[0] == final

    def test_a_failed_relink_fails_the_provision(self, tmp_path: Path):
        """Reporting success with an unimportable engine is the bug this whole finding
        is about, so the re-link's result must not be discarded — `install_engine`
        returns False when its `finalize` fails."""

        def _fake_install(root, log, validate=None, finalize=None):  # noqa: ANN001
            # Mirrors the real contract: a failing `finalize` fails the install.
            return bool(finalize is None or finalize(root))

        with (
            mock.patch.object(provision.engine_source, "install_engine", side_effect=_fake_install),
            mock.patch.object(provision, "_relink_editable_skill", return_value=False),
        ):
            assert provision._ensure_engine(tmp_path, [], "/opt/uv") is False

    def test_the_relink_targets_the_given_root_not_a_staging_path(self, tmp_path: Path):
        """The argv must name `engine_root`, since being pointed at staging is exactly
        what produced the stale `.pth`."""
        calls: list[list[str]] = []

        def _capture(argv, **kwargs):  # noqa: ANN001, ANN202
            calls.append(list(argv))
            return 0, ""

        with mock.patch.object(provision, "_run", side_effect=_capture):
            assert provision._relink_editable_skill(tmp_path, [], "/opt/uv") is True

        argv = calls[0]
        assert "--editable" in argv
        assert argv[argv.index("--editable") + 1] == str(tmp_path / "skill")
        # And the interpreter it installs INTO is the tree's own venv.
        assert str(tmp_path / "mcp-local" / ".venv") in argv[argv.index("--python") + 1]


class TestResolveUv:
    """`uv` ships as a declared Python dependency of Kiro Crew, so it is always
    INSTALLED — but not necessarily on PATH (a wheel puts it in the venv's
    scripts dir; an installed launchd/systemd service runs with a minimal PATH;
    the frozen DMG bundle has no scripts dir at all). The gateway's resolver
    closes that gap, and it is the same one the gateway renders into the
    agents' `{UV_BIN}`, so the two cannot disagree. On a gateway without it the
    same ladder runs here."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        provision.reset_uv_cache()
        yield
        provision.reset_uv_cache()

    @pytest.fixture
    def no_host_resolver(self, fake_host):
        fake_host.remove("kiro_crew.env", "resolve_uv")

    def test_the_gateways_resolver_wins(self, fake_host):
        """The value the gateway renders into `{UV_BIN}` is the one provisioning uses."""
        fake_host.table[("kiro_crew.env", "resolve_uv")] = lambda: "/opt/gateway/bin/uv"
        with mock.patch.object(_host.shutil, "which") as which:
            assert provision.resolve_uv() == "/opt/gateway/bin/uv"
        assert not which.called

    def test_a_gateway_that_finds_no_uv_is_believed(self, fake_host):
        fake_host.table[("kiro_crew.env", "resolve_uv")] = lambda: None
        with mock.patch.object(_host.shutil, "which", return_value="/usr/bin/uv"):
            assert provision.resolve_uv() is None

    def test_prefers_the_installed_package_locator(
        self, tmp_path: Path, no_host_resolver, monkeypatch
    ):
        """The normal `pip install` case: ask the uv wheel where its binary is
        rather than hoping the venv's scripts dir is on PATH."""
        packaged = tmp_path / "site-packages-uv"
        packaged.write_text("#!/bin/sh", encoding="utf-8")
        monkeypatch.setitem(
            sys.modules, "uv", mock.Mock(find_uv_bin=mock.Mock(return_value=str(packaged)))
        )
        with mock.patch.object(_host.shutil, "which") as which:
            assert provision.resolve_uv() == str(packaged)
        assert not which.called, "PATH must not be consulted when the package resolves"

    def test_falls_back_to_path_when_the_locator_raises(self, no_host_resolver, monkeypatch):
        """`find_uv_bin` raises UvNotFound (a FileNotFoundError) on an odd
        repackaging — a user's own newer uv must still work."""
        monkeypatch.setitem(
            sys.modules,
            "uv",
            mock.Mock(find_uv_bin=mock.Mock(side_effect=FileNotFoundError("no uv"))),
        )
        with mock.patch.object(_host.shutil, "which", return_value="/usr/local/bin/uv"):
            assert provision.resolve_uv() == "/usr/local/bin/uv"

    def test_falls_back_to_path_when_the_package_is_absent(self, no_host_resolver, monkeypatch):
        """An install without the uv wheel at all must not raise ImportError."""
        monkeypatch.setitem(sys.modules, "uv", None)
        with mock.patch.object(_host.shutil, "which", return_value="/usr/bin/uv"):
            assert provision.resolve_uv() == "/usr/bin/uv"

    def test_a_locator_pointing_at_a_missing_file_is_not_trusted(
        self, tmp_path: Path, no_host_resolver, monkeypatch
    ):
        """A path that does not exist is not a usable uv — fall through rather
        than hand an absolute nonexistent path to `subprocess.run`."""
        monkeypatch.setitem(
            sys.modules,
            "uv",
            mock.Mock(find_uv_bin=mock.Mock(return_value=str(tmp_path / "gone"))),
        )
        with mock.patch.object(_host.shutil, "which", return_value="/usr/bin/uv"):
            assert provision.resolve_uv() == "/usr/bin/uv"

    def test_returns_none_when_uv_is_absent_everywhere_and_never_raises(
        self, no_host_resolver, monkeypatch
    ):
        """The contract `provision()` depends on: an absent uv is a reportable
        condition, not a traceback inside a detached background job."""
        monkeypatch.setitem(
            sys.modules, "uv", mock.Mock(find_uv_bin=mock.Mock(side_effect=FileNotFoundError()))
        )
        with mock.patch.object(_host.shutil, "which", return_value=None):
            assert provision.resolve_uv() is None

    def test_a_relative_which_result_is_skipped(
        self, tmp_path: Path, no_host_resolver, monkeypatch
    ):
        """A relative `PATH` entry makes `shutil.which` return a relative path,
        which names whatever sits in the working directory; it is not used."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setitem(
            sys.modules, "uv", mock.Mock(find_uv_bin=mock.Mock(side_effect=FileNotFoundError()))
        )
        with mock.patch.object(_host.shutil, "which", return_value="bin/uv"):
            assert provision.resolve_uv() is None

    def test_a_relative_locator_result_is_made_absolute(
        self, tmp_path: Path, no_host_resolver, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "uv").write_text("#!/bin/sh", encoding="utf-8")
        monkeypatch.setitem(sys.modules, "uv", mock.Mock(find_uv_bin=mock.Mock(return_value="uv")))
        assert provision.resolve_uv() == str(tmp_path / "uv")

    def test_the_resolved_path_is_cached(self, fake_host):
        """Called on every provision, and the answer cannot change in-process."""
        locator = mock.Mock(return_value="/opt/gateway/bin/uv")
        fake_host.table[("kiro_crew.env", "resolve_uv")] = locator
        assert provision.resolve_uv() == "/opt/gateway/bin/uv"
        assert provision.resolve_uv() == "/opt/gateway/bin/uv"
        assert locator.call_count == 1


class TestRunSandboxing:
    """`_run` spawns third-party code (`uv` against a freshly fetched tree), so
    the environment it hands the child is security-relevant."""

    def test_spawns_through_the_credential_scrubbing_chokepoint(self, tmp_path: Path):
        """Not a bare `subprocess.run`: the child must get the scrubbed env from
        `sandboxed_spawn_argv`, or it inherits cloud creds and SSH_AUTH_SOCK."""
        scrubbed = {"PATH": "/usr/bin"}
        with (
            mock.patch.object(
                provision, "sandboxed_spawn_argv", return_value=(["/opt/uv", "x"], scrubbed, None)
            ) as chokepoint,
            mock.patch.object(provision, "run_limited") as run,
        ):
            run.return_value = mock.Mock(returncode=0, stdout="out", stderr="")
            provision._run(["/opt/uv", "x"], cwd=str(tmp_path), timeout=5)
        assert chokepoint.call_args.kwargs["strip_python_env"] is True
        assert run.call_args.kwargs["env"] == scrubbed
        # STRICT, not the `standard` default. Scrubbing the env stops the child
        # INHERITING a credential; it does not stop it READING one, and standard
        # mode leaves ~/.aws and ~/.ssh visible so the AWS CLI and git-over-SSH
        # keep working. A build backend over a freshly-downloaded tree needs
        # neither. No sandbox mode restricts network, so uv still reaches the index.
        assert chokepoint.call_args.kwargs.get("mode") == "strict"

    def test_a_gateway_without_the_sandbox_runs_nothing(self, tmp_path: Path, fake_host):
        """Fail closed: no sandboxed launch, no `uv` over a third-party tree."""
        fake_host.remove("kiro_crew.sandbox", "sandboxed_spawn_argv")
        with mock.patch.object(subprocess, "run") as run:
            with pytest.raises(_host.SandboxUnavailableError):
                provision._run(["/opt/uv", "sync"], cwd=str(tmp_path), timeout=5)
        assert not run.called

    def test_a_gateway_without_resource_limits_runs_nothing(self, tmp_path: Path, fake_host):
        fake_host.remove("kiro_crew.sandbox", "run_limited")
        with mock.patch.object(subprocess, "run") as run:
            with pytest.raises(_host.SandboxUnavailableError):
                provision._run(["/opt/uv", "sync"], cwd=str(tmp_path), timeout=5)
        assert not run.called

    def test_a_timeout_is_reported_not_raised(self, tmp_path: Path):
        """Provisioning must report, not explode: a hung `uv` becomes a failed
        step with a message the UI can show."""
        with (
            mock.patch.object(provision, "sandboxed_spawn_argv", return_value=(["uv"], {}, None)),
            mock.patch.object(
                provision,
                "run_limited",
                side_effect=subprocess.TimeoutExpired(cmd="uv", timeout=7),
            ),
        ):
            code, out = provision._run(["uv", "sync"], cwd=str(tmp_path), timeout=7)
        assert code == 1
        assert "timed out" in out

    def test_a_missing_binary_is_reported_not_raised(self, tmp_path: Path):
        with (
            mock.patch.object(provision, "sandboxed_spawn_argv", return_value=(["uv"], {}, None)),
            mock.patch.object(provision, "run_limited", side_effect=OSError("No such file")),
        ):
            code, out = provision._run(["uv", "sync"], cwd=str(tmp_path), timeout=7)
        assert code == 1
        assert "could not be run" in out

    def test_the_sandbox_profile_is_always_cleaned_up(self, tmp_path: Path):
        """The chokepoint may hand back a temp profile path; leaking one per
        spawn would litter the temp dir on every provision."""
        profile = tmp_path / "sandbox.sb"
        profile.write_text("(version 1)", encoding="utf-8")
        with (
            mock.patch.object(
                provision, "sandboxed_spawn_argv", return_value=(["/opt/uv"], {}, str(profile))
            ),
            mock.patch.object(provision, "run_limited", side_effect=OSError("boom")),
        ):
            provision._run(["/opt/uv"], cwd=str(tmp_path), timeout=5)
        assert not profile.exists()

    def test_output_merges_both_streams(self, tmp_path: Path):
        """`uv` splits its diagnostics across stdout/stderr, so the log the user
        is shown has to carry both."""
        with (
            mock.patch.object(
                provision, "sandboxed_spawn_argv", return_value=(["/opt/uv"], {}, None)
            ),
            mock.patch.object(provision, "run_limited") as run,
        ):
            run.return_value = mock.Mock(returncode=2, stdout="on-out\n", stderr="on-err")
            code, out = provision._run(["/opt/uv"], cwd=str(tmp_path), timeout=5)
        assert code == 2
        assert "on-out" in out and "on-err" in out


class TestEnsureVenv:
    def test_a_checkout_without_mcp_local_is_refused(self, tmp_path: Path):
        """Guards against building against a tree that is not the engine."""
        log: list[str] = []
        with mock.patch.object(provision, "_run") as run:
            assert provision._ensure_venv(tmp_path, log, "/opt/uv") is False
        assert not run.called
        assert any("mcp-local" in line for line in log)

    def test_a_failed_dependency_resolve_stops_before_the_skill_install(self, tmp_path: Path):
        (tmp_path / "mcp-local").mkdir()
        log: list[str] = []
        with mock.patch.object(provision, "_run", return_value=(1, "resolution failed")) as run:
            assert provision._ensure_venv(tmp_path, log, "/opt/uv") is False
        assert run.call_count == 1
        assert any("dependency resolve failed" in line for line in log)

    def test_both_uv_calls_use_the_resolved_absolute_path(self, tmp_path: Path):
        """Never the bare name `uv`: the gateway's PATH may not carry the venv's
        scripts dir (installed service), and a frozen bundle has none at all."""
        (tmp_path / "mcp-local").mkdir()
        resolved = "/opt/kirocrew/uv"
        with mock.patch.object(provision, "_run", return_value=(0, "")) as run:
            assert provision._ensure_venv(tmp_path, [], resolved) is True
        assert run.call_count == 2
        for call in run.call_args_list:
            assert call.args[0][0] == resolved

    def test_the_skill_package_is_installed_editable(self, tmp_path: Path):
        """A non-editable install drops the engine's sibling data dirs (bundled
        templates and styles), so they would silently go missing."""
        (tmp_path / "mcp-local").mkdir()
        log: list[str] = []
        with mock.patch.object(provision, "_run", return_value=(0, "")) as run:
            assert provision._ensure_venv(tmp_path, log, "/opt/uv") is True
        install_argv = run.call_args_list[1].args[0]
        assert install_argv[:4] == ["/opt/uv", "pip", "install", "--python"]
        assert "--editable" in install_argv

    def test_a_failed_skill_install_fails_provisioning(self, tmp_path: Path):
        (tmp_path / "mcp-local").mkdir()
        log: list[str] = []
        with mock.patch.object(provision, "_run", side_effect=[(0, ""), (1, "wheel build failed")]):
            assert provision._ensure_venv(tmp_path, log, "/opt/uv") is False
        assert any("skill package install failed" in line for line in log)


def _rendered(values: dict[str, str] | None = None) -> list[tuple[str, dict]]:
    """Every shipped template rendered the way the gateway renders it."""
    values = provision.placeholder_values() if values is None else values
    return [
        (path.name, provision.render_agent_template(path.read_text(encoding="utf-8"), values))
        for path in provision.shipped_agent_templates()
    ]


def _sdpm_env(data: dict) -> dict:
    return data["mcpServers"]["sdpm"]["env"]


class TestRenderAgents:
    """The shipped agent configs are templates: the engine's absolute location is
    only known at run time, so the gateway substitutes every placeholder.
    `render_agent_template` / `placeholder_values` are this app's statement of
    that contract; these pin it, on every platform's path shapes."""

    def test_renders_every_shipped_agent_with_no_placeholder_left(self):
        rendered = _rendered()
        assert len(rendered) == 4, "the app ships four agent templates"
        for name, data in rendered:
            text = json.dumps(data)
            for placeholder in _declared_placeholders():
                assert placeholder not in text, f"{name} kept {placeholder}"
            # Nothing that merely LOOKS like a placeholder either: a placeholder
            # added to a template but not to the contract must fail here.
            leftover = re.findall(r"\{[A-Z][A-Z0-9_]*\}", text)
            assert leftover == [], f"{name} kept {leftover}"

    def test_every_template_placeholder_is_declared(self):
        """The reverse direction: a template may use only the declared placeholders."""
        for path in provision.shipped_agent_templates():
            used = set(re.findall(r"\{[A-Z][A-Z0-9_]*\}", path.read_text(encoding="utf-8")))
            assert used <= set(provision.PLACEHOLDERS), (path.name, used)

    def test_an_unresolved_placeholder_is_refused(self):
        """A config that names a literal `{ENGINE_ROOT}` must never be produced."""
        template = provision.shipped_agent_templates()[0].read_text(encoding="utf-8")
        values = provision.placeholder_values()
        values.pop(provision.PLACEHOLDER_ENGINE_ROOT)
        with pytest.raises(ValueError, match="unresolved placeholder"):
            provision.render_agent_template(template, values)

    def test_the_mcp_server_path_finds_the_managed_tool(self, tmp_path: Path):
        """The rendered MCP ``env.PATH`` is the ONLY place a managed tool is visible.

        The engine shells out to ``pdftoppm``/``soffice`` by name from inside the MCP
        server that kiro-cli spawns from this config — not from any gateway
        subprocess. So this asserts the engine's own resolution (``shutil.which``
        against the rendered value).
        """
        managed = tmp_path / "preview-tools" / "bin"
        managed.mkdir(parents=True)
        tool = _fake_tool(managed, "pdftoppm")
        with mock.patch.object(provision.paths, "preview_tools_bin", return_value=managed):
            rendered = _rendered()
        for name, data in rendered:
            assert _same_path(shutil.which("pdftoppm", path=_sdpm_env(data)["PATH"]), tool), name

    def test_a_system_tool_still_wins_the_engine_lookup(self, tmp_path: Path):
        """Appended, not prepended: the managed launcher is a fallback, not an override."""
        system_dir = tmp_path / "usr-bin"
        managed = tmp_path / "preview-tools" / "bin"
        system_dir.mkdir(parents=True)
        managed.mkdir(parents=True)
        system_tool = _fake_tool(system_dir, "pdftoppm")
        _fake_tool(managed, "pdftoppm")
        with (
            mock.patch.object(provision.paths, "preview_tools_bin", return_value=managed),
            mock.patch.dict(os.environ, {"PATH": str(system_dir)}, clear=False),
        ):
            _name, data = _rendered()[0]
        assert _same_path(shutil.which("pdftoppm", path=_sdpm_env(data)["PATH"]), system_tool)

    def test_a_windows_shaped_path_survives_json_rendering(self, tmp_path: Path):
        """A Windows ``PATH`` is full of backslashes, and ``\\U``/``\\A`` are invalid
        JSON escapes — an unescaped substitution makes every rendered config
        unparseable, i.e. no agent configs at all on Windows."""
        win_path = r"C:\Users\me\AppData\Local\Programs\Python;C:\Windows\system32"
        managed = tmp_path / "preview-tools" / "bin"
        managed.mkdir(parents=True)
        with (
            mock.patch.object(provision.paths, "preview_tools_bin", return_value=managed),
            mock.patch.dict(os.environ, {"PATH": win_path}, clear=False),
        ):
            rendered = _rendered()
        for _name, data in rendered:
            assert win_path in _sdpm_env(data)["PATH"]

    def test_the_rendered_path_never_contains_an_empty_element(self, tmp_path: Path):
        """An empty ``PATH`` element means the CWD on POSIX — tool resolution would
        then depend on wherever the MCP server happened to be started."""
        with mock.patch.object(
            provision.paths, "preview_tools_bin", return_value=tmp_path / "absent"
        ):
            rendered = _rendered()
        for _name, data in rendered:
            assert "" not in _sdpm_env(data)["PATH"].split(os.pathsep)

    def test_the_prompts_placeholder_points_into_the_app_checkout(self):
        """For an installed app the gateway's install dir IS this checkout, so the
        app-owned prompt resource is read from the repository's own `prompts/`."""
        wanted = str(provision.APP_ROOT / "prompts")
        strings = [s for _name, data in _rendered() for s in _json_strings(data)]
        assert any(wanted in s for s in strings)
        assert (provision.APP_ROOT / "prompts" / "spec-studio.md").is_file()

    def test_a_windows_style_path_still_renders_parseable_json(self):
        """A backslash path must be JSON-escaped into the template.

        Regression test for a real Windows bug: the raw substitution produced
        `"C:\\Users\\…"`, whose `\\U` is an invalid JSON escape, so EVERY template
        failed to parse. Simulated rather than skipped so the escaping is pinned
        on every platform.
        """
        win_root = r"C:\Users\runneradmin\.kiro\crew\apps\pptx-maker\data\vendor\sdpm"
        with (
            mock.patch.object(provision.paths, "engine_root", return_value=Path(win_root)),
            mock.patch.object(
                provision.paths, "engine_mcp_dir", return_value=Path(win_root + r"\mcp-local")
            ),
        ):
            rendered = _rendered()
        for name, data in rendered:
            assert any(win_root in s for s in _json_strings(data)), name

    def test_a_quote_in_a_path_cannot_break_out_of_the_json_string(self):
        """The escaping is a real escape, not a backslash special-case."""
        nasty = '/tmp/we"ird\\path'
        with mock.patch.object(provision.paths, "engine_root", return_value=Path(nasty)):
            rendered = _rendered()
        assert len(rendered) == 4

    def test_a_malformed_template_is_refused(self):
        """A malformed agent config is silently IGNORED by kiro-cli, surfacing
        only as "the mode is missing" — so it must fail loudly instead."""
        with pytest.raises(ValueError):
            provision.render_agent_template("{not json", provision.placeholder_values())

    def test_a_missing_agents_dir_has_no_templates(self, tmp_path: Path):
        with mock.patch.object(provision, "APP_ROOT", tmp_path / "absent"):
            assert provision.shipped_agent_templates() == []

    def test_provisioning_never_writes_into_the_app_checkout(self, tmp_path: Path):
        """The built-in rendered its agents into its install dir. For an installed
        app that dir IS this checkout: rendered files there would carry an
        executable `{UV_BIN}` the agent can rewrite, and dirty the tree an update
        verifies. Provisioning installs the preview tool and writes nothing here."""

        def _snapshot() -> dict[str, bytes]:
            return {
                str(p.relative_to(provision.APP_ROOT)): p.read_bytes()
                for sub in ("agents", "prompts")
                for p in sorted((provision.APP_ROOT / sub).rglob("*"))
                if p.is_file()
            }

        before = _snapshot()
        calls: list[str] = []
        with (
            mock.patch.object(provision, "resolve_uv", return_value="/opt/uv"),
            mock.patch.object(provision, "_ensure_engine", return_value=True),
            mock.patch.object(provision, "_venv_ready", return_value=True),
            mock.patch.object(provision, "_seed_deck_root", side_effect=lambda *a, **k: None),
            mock.patch.object(provision, "_current_tag", return_value="v0"),
            mock.patch(
                "backend.preview_tools.install_pdftoppm",
                side_effect=lambda: (calls.append("install_tool"), (True, "ready"))[1],
            ),
            mock.patch("backend.engine.scan_new_templates", return_value=[]),
        ):
            outcome = provision.provision()
        assert outcome.ok, outcome.log
        assert calls == ["install_tool"]
        assert _snapshot() == before


class TestSeedDeckRoot:
    """A first install seeds a deck dir that needs no macOS file-access grant;
    an existing config or existing decks must never be touched."""

    def test_seeds_a_config_dir_location_on_a_first_install(self, tmp_path: Path):
        config_path = tmp_path / "sdpm" / "config.json"
        log: list[str] = []
        with (
            mock.patch.object(provision.paths, "engine_config_path", return_value=config_path),
            mock.patch.object(provision.Path, "home", return_value=tmp_path / "home"),
        ):
            provision._seed_deck_root(log)
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["output_dir"] == provision.paths.SEEDED_DECK_ROOT

    def test_an_existing_engine_config_is_never_overwritten(self, tmp_path: Path):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"output_dir": "/the/users/choice"}), encoding="utf-8")
        with mock.patch.object(provision.paths, "engine_config_path", return_value=config_path):
            provision._seed_deck_root(log=[])
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["output_dir"] == "/the/users/choice"

    def test_existing_decks_in_the_engine_default_are_left_alone(self, tmp_path: Path):
        """Someone already using the engine has decks in ~/Documents; silently
        re-pointing the output dir would strand them."""
        home = tmp_path / "home"
        existing = home / provision.paths.ENGINE_DEFAULT_DECK_ROOT
        existing.mkdir(parents=True)
        (existing / "20260101-deck").mkdir()
        config_path = tmp_path / "sdpm" / "config.json"
        with (
            mock.patch.object(provision.paths, "engine_config_path", return_value=config_path),
            mock.patch.object(provision.Path, "home", return_value=home),
        ):
            provision._seed_deck_root(log=[])
        assert not config_path.exists()

    def test_an_empty_engine_default_still_gets_seeded(self, tmp_path: Path):
        home = tmp_path / "home"
        (home / provision.paths.ENGINE_DEFAULT_DECK_ROOT).mkdir(parents=True)
        config_path = tmp_path / "sdpm" / "config.json"
        with (
            mock.patch.object(provision.paths, "engine_config_path", return_value=config_path),
            mock.patch.object(provision.Path, "home", return_value=home),
        ):
            provision._seed_deck_root(log=[])
        assert config_path.exists()


class TestProvision:
    """The top-level orchestration: what fails the run, and what only warns."""

    @pytest.fixture(autouse=True)
    def _no_real_work(self, tmp_path: Path):
        """Neutralize every side effect; each test re-patches what it cares about."""
        with (
            mock.patch.object(provision.paths, "engine_root", return_value=tmp_path / "engine"),
            mock.patch.object(provision, "_seed_deck_root"),
            mock.patch.object(provision, "_current_tag", return_value="v0.3.8"),
        ):
            yield

    def test_an_unresolvable_uv_fails_before_any_network_call(self):
        """`uv` ships as a Python dependency, so this only happens on a broken
        install — and it must say THAT rather than failing deep inside a build."""
        with (
            mock.patch.object(provision, "resolve_uv", return_value=None),
            mock.patch.object(provision, "_ensure_engine") as fetch,
        ):
            outcome = provision.provision()
        assert outcome.ok is False
        assert "`uv` could not be found" in outcome.log
        assert not fetch.called, "a missing uv must stop before fetching the engine"

    def test_the_failure_message_no_longer_blames_git(self):
        """`git` is not used any more; a message naming it would send the user
        off to install something this app does not need."""
        with mock.patch.object(provision, "resolve_uv", return_value=None):
            outcome = provision.provision()
        assert "git" not in outcome.log

    def test_the_resolved_uv_is_threaded_into_the_build(self):
        """Resolved ONCE and passed down, so the fetch and the two uv calls can
        never disagree about which binary they are using."""
        with (
            mock.patch.object(provision, "resolve_uv", return_value="/opt/kirocrew/uv"),
            mock.patch.object(provision, "_ensure_engine", return_value=True) as fetch,
            mock.patch.object(provision, "_ensure_venv", return_value=False) as venv,
        ):
            provision.provision()
        assert fetch.call_args.args[2] == "/opt/kirocrew/uv"
        assert venv.call_args.args[2] == "/opt/kirocrew/uv"

    def test_a_refused_engine_fetch_never_builds_a_venv(self):
        """The digest-pin refusal is worthless if the build runs anyway."""
        with (
            mock.patch.object(provision, "resolve_uv", return_value="/x/uv"),
            mock.patch.object(provision, "_ensure_engine", return_value=False),
            mock.patch.object(provision, "_ensure_venv") as venv,
        ):
            outcome = provision.provision()
        assert outcome.ok is False
        assert not venv.called

    def test_a_failed_venv_build_installs_no_preview_tool(self):
        with (
            mock.patch.object(provision, "resolve_uv", return_value="/x/uv"),
            mock.patch.object(provision, "_ensure_engine", return_value=True),
            mock.patch.object(provision, "_ensure_venv", return_value=False),
            mock.patch("backend.preview_tools.install_pdftoppm") as install,
        ):
            outcome = provision.provision()
        assert outcome.ok is False
        assert not install.called

    def test_a_successful_run_reports_the_resolved_tag(self):
        with (
            mock.patch.object(provision, "resolve_uv", return_value="/x/uv"),
            mock.patch.object(provision, "_ensure_engine", return_value=True),
            mock.patch.object(provision, "_ensure_venv", return_value=True),
            mock.patch(
                "backend.engine.scan_new_templates",
                return_value=["corp"],
            ),
        ):
            outcome = provision.provision()
        assert outcome.ok is True
        assert outcome.engine_tag == "v0.3.8"
        assert "analyzed 1 template(s)" in outcome.log

    def test_provisioning_does_not_register_resources_itself(self):
        """Registration belongs to the gateway's enable path and its start-up
        reconcile, not here: the gateway renders the agent templates itself. A
        detached provisioning job registering mid-run could also race a disable
        the operator made meanwhile, recreating resources the disable removed.
        The provisioner has no way to register at all: it names no registrar."""
        with (
            mock.patch.object(provision, "resolve_uv", return_value="/x/uv"),
            mock.patch.object(provision, "_ensure_engine", return_value=True),
            mock.patch.object(provision, "_ensure_venv", return_value=True),
            mock.patch("backend.engine.scan_new_templates", return_value=[]),
        ):
            outcome = provision.provision()
        assert outcome.ok is True
        source = Path(provision.__file__).read_text(encoding="utf-8")
        assert "register_app" not in source and "bridges" not in source

    def test_a_template_analysis_failure_does_not_fail_provisioning(self):
        """An un-analyzed template is still usable, so this is best effort."""
        with (
            mock.patch.object(provision, "resolve_uv", return_value="/x/uv"),
            mock.patch.object(provision, "_ensure_engine", return_value=True),
            mock.patch.object(provision, "_ensure_venv", return_value=True),
            mock.patch(
                "backend.engine.scan_new_templates",
                side_effect=RuntimeError("engine exploded"),
            ),
        ):
            outcome = provision.provision()
        assert outcome.ok is True
        assert "template analysis skipped" in outcome.log


class TestProvisionOutcome:
    def test_the_log_handed_to_the_ui_is_tail_bounded(self):
        """A cold `uv sync` emits megabytes; the response must stay bounded."""
        outcome = provision.ProvisionOutcome(ok=False, log="x" * 99999)
        assert len(outcome.to_dict()["log"]) == provision.LOG_TAIL_CHARS

    def test_to_dict_keeps_the_tail_not_the_head(self):
        """The FAILURE is at the end of a build log — truncating the head would
        throw away the only useful part."""
        outcome = provision.ProvisionOutcome(ok=False, log="early\n" + "x" * 8000 + "\nTHE ERROR")
        assert outcome.to_dict()["log"].endswith("THE ERROR")


class TestShippedAgentsDoNotPreAuthorizeTools:
    """No shipped agent config may carry ``allowedTools``.

    An auto-approved tool never reaches ``hooks.on_tool_call``: that hook is only
    invoked from the permission-request branch, and the tool-call branch is explicitly
    informational and cannot block. So an ``allowedTools`` entry skips the whole
    PreToolUse plane — deny rules, the governance ceiling, an enterprise
    ``enabled: false``. ``AGENTS.md`` states the same rule for the managed
    ``kirocrew-computer`` server, which is deliberately in ``tools`` but NOT
    ``allowedTools`` and ships no ``autoApprove`` key.

    These four configs pre-authorized ``read`` alongside ``web_fetch`` — the
    exfiltration pair — so URL content that talked the agent into reading a file could
    send it back out with neither call surfacing for approval.

    Removing the key costs nothing functional: the tools stay in ``tools``, so the
    agent can still call them; each call now prompts (or is auto-approved by the
    read-only policy in ``hooks.py``, AFTER the deny and governance checks).

    Meetings' three agent configs already ship no ``allowedTools`` — this brings PPTX
    in line rather than inventing a new rule.
    """

    def test_no_agent_template_carries_allowed_tools(self) -> None:
        # Anchored on the app root, not the CWD: `APP_ROOT` is derived from
        # `__file__`, so it is correct from any working directory.
        templates = provision.shipped_agent_templates()
        assert templates, "no shipped agent templates found — did the path change?"
        offenders: list[str] = []
        for path in templates:
            raw = path.read_text(encoding="utf-8")
            # Render placeholders so a template still parses as JSON.
            for placeholder in _declared_placeholders():
                raw = raw.replace(placeholder, "/rendered")
            data = json.loads(raw)
            if data.get("allowedTools"):
                offenders.append(path)
        assert offenders == [], (
            "these shipped agent configs pre-authorize tools, which skips the entire "
            "PreToolUse gate (deny rules, governance ceiling):\n  " + "\n  ".join(offenders)
        )

    def test_the_tools_themselves_are_still_offered(self) -> None:
        """The fix must not disable the agents: only the auto-approval is removed."""
        for path in provision.shipped_agent_templates():
            raw = path.read_text(encoding="utf-8")
            for placeholder in _declared_placeholders():
                raw = raw.replace(placeholder, "/rendered")
            data = json.loads(raw)
            assert data.get("tools"), f"{path} lost its tools entirely"

    def test_every_at_server_grant_resolves_to_a_declared_server(self) -> None:
        """Every ``@server``/``@server/tool`` grant names a server something declares.

        kiro-cli drops an unresolvable ``@`` reference SILENTLY at mount time:
        the agent registers, mounts without the tool, and no exception or
        warning appears anywhere — so a typo in a spec edit degrades an agent
        with zero signal. This gate makes that failure loud in CI.

        A shipped spec's ``@`` grant is resolvable when its server part names
        one of the three sources registration actually merges (see
        ``bridges._register_agents``):

        - the spec's OWN ``mcpServers`` block (e.g. pptx-maker's ``sdpm``);
        - a HOST-MANAGED server (``@kirocrew-cron``, ``@kirocrew-core``). These
          specs reference none, and the host's list of them is not public, so
          any such reference fails here and must be added deliberately;
        - the owning app's NAMESPACED servers, ``<app>:<server>`` for every
          key in the manifest's ``mcpServers`` (``bridges._own_mcp_servers``
          injects these by prefix after ``_register_mcp_servers`` writes them).

        Deliberately NOT validated: bare builtin names (kiro-cli checks those
        at registration — re-listing its vocabulary here would rot) and the
        ``/tool`` half of a reference (only the live server can enumerate its
        tools; the server lookup is the part that fails silently).
        """
        templates = provision.shipped_agent_templates()
        assert templates, "no shipped agent templates found — did the path change?"
        offenders: list[str] = []
        grants_seen = 0
        for path in templates:
            raw = path.read_text(encoding="utf-8")
            for placeholder in _declared_placeholders():
                raw = raw.replace(placeholder, "/rendered")
            data = json.loads(raw)
            resolvable = set(data.get("mcpServers") or {})
            manifest = json.loads((path.parent.parent / "app.json").read_text(encoding="utf-8"))
            app_name = manifest.get("name")
            if isinstance(app_name, str) and app_name:
                resolvable.update(
                    f"{app_name}:{server}" for server in (manifest.get("mcpServers") or {})
                )
            for entry in data.get("tools") or []:
                if not isinstance(entry, str) or not entry.startswith("@"):
                    continue
                grants_seen += 1
                remainder = entry[1:]
                server = remainder.split("/", 1)[0]
                if server not in resolvable:
                    offenders.append(f"{path}: {entry!r} (server {server!r})")
        # The gate must not pass vacuously: shipped specs DO carry @ grants
        # today, so finding none means the traversal or the spec format moved.
        assert grants_seen, "no @server grants found in any shipped spec — did the format change?"
        assert offenders == [], (
            "these shipped agent specs grant tools on a server that nothing "
            "declares — kiro-cli will silently drop them at mount time:\n  "
            + "\n  ".join(offenders)
        )


class TestTheProvisionLogIsDecodedAsUtf8:
    """`_run`'s captured output is the provisioning log the operator reads.

    ``ProvisionState`` serves it straight to the dashboard
    (``{"ok": ..., "log": self.log[-LOG_TAIL_CHARS:], ...}``), so whatever this
    decode gets wrong is what a human sees while trying to work out why an
    install failed — the one moment the log has to be right.

    The child is ``uv``, which this module's own docstring calls "a static Rust
    binary": it writes UTF-8, not the console code page. But
    ``run_limited(..., text=True)`` with no ``encoding=`` decodes with
    ``locale.getpreferredencoding()``, which on Windows is the legacy ANSI code
    page. uv's output carries package names and absolute paths under the user's
    data home, so a non-ASCII account name is enough to reach this.

    Measured, not argued: the child exits **0** and the whole log arrives as
    ``""``. On Windows ``capture_output`` decodes on a helper thread, so the
    error kills that thread rather than the call and ``proc.stdout`` is ``None``;
    the operator gets a blank log for a run that printed plenty. On POSIX the
    same decode raises `UnicodeDecodeError` — a `ValueError`, so neither
    ``except subprocess.TimeoutExpired`` nor
    ``except (OSError, subprocess.SubprocessError)`` catches it and it escapes
    ``_run`` entirely.
    """

    #: Not valid UTF-8 (``0xff`` never begins a sequence), so this is red on
    #: every host — it turns on the decode being strict, not on the host codec.
    PAYLOAD = b"Resolved 41 packages\nerror: failed at " + bytes([0xFF, 0xFE]) + b"/pkg\n"

    def _argv(self) -> list[str]:
        return [
            sys.executable,
            "-c",
            f"import sys;sys.stdout.buffer.write({self.PAYLOAD!r});sys.stdout.buffer.flush()",
        ]

    def test_undecodable_uv_output_still_reaches_the_log(self, tmp_path):
        """A malformed byte must cost one character, not the entire log."""
        with pytest.raises(UnicodeDecodeError):
            self.PAYLOAD.decode("utf-8")  # guard the guard

        with mock.patch.object(
            provision, "sandboxed_spawn_argv", return_value=(self._argv(), None, None)
        ):
            code, out = provision._run(self._argv(), cwd=str(tmp_path), timeout=30)

        assert code == 0
        assert "Resolved 41 packages" in out, "the log must survive one bad byte"
        assert "error: failed at" in out
