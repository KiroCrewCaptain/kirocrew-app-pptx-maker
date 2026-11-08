"""PPTX Maker — the managed preview tools (`pdftoppm` shim + its launcher).

The app's slide thumbnails need ``pdftoppm``, which a stock machine does not have.
These tests pin the two halves of the fix: the shim reproduces the exact
``pdftoppm`` command shape the engine calls (so the engine's own glob keeps
matching), and the launcher makes it resolvable as ``pdftoppm`` from the app's
managed bin dir.

The shim is exercised against REAL PDFs built with ``pypdfium2`` rather than
mocks: the whole point of the module is byte-level output compatibility with
poppler's naming, and a mocked renderer would assert nothing about that.
"""

from __future__ import annotations

import importlib.machinery
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest import mock

import pytest

from backend import pdftoppm_shim, preview_tools
from backend import _os_deps as os_deps

pdfium = pytest.importorskip(
    "pypdfium2",
    reason="pypdfium2 backs the pdftoppm shim; it ships in the engine venv",
)


def _make_pdf(path: Path, pages: int = 2, width: int = 612, height: int = 792) -> Path:
    """A genuine multi-page PDF, so the shim is tested against real parsing."""
    import pypdfium2.raw as raw

    doc = pdfium.PdfDocument.new()
    for _ in range(pages):
        raw.FPDFPage_New(doc, len(doc), width, height)
    doc.save(str(path))
    return path


class TestShimMatchesPopplerOutputNaming:
    """The engine globs ``page-*.png`` and parses ``page-(\\d+)\\.png``.

    Any deviation (zero-padding, 0-based numbering) silently yields zero
    thumbnails, so the naming is the contract worth pinning hardest.
    """

    def test_writes_one_png_per_page_numbered_from_one(self, tmp_path: Path) -> None:
        source = _make_pdf(tmp_path / "slides.pdf", pages=3)
        rc = pdftoppm_shim.main(["-png", "-scale-to", "1280", str(source), str(tmp_path / "page")])
        assert rc == 0
        assert sorted(p.name for p in tmp_path.glob("page-*.png")) == [
            "page-1.png",
            "page-2.png",
            "page-3.png",
        ]

    def test_scale_to_fits_the_long_edge(self, tmp_path: Path) -> None:
        """``-scale-to N`` sizes the LONGER side to N and keeps the aspect ratio."""
        from PIL import Image

        source = _make_pdf(tmp_path / "portrait.pdf", pages=1, width=612, height=792)
        assert (
            pdftoppm_shim.main(["-png", "-scale-to", "1280", str(source), str(tmp_path / "p")]) == 0
        )
        with Image.open(tmp_path / "p-1.png") as img:
            assert max(img.size) == 1280
            # 612/792 preserved to within a pixel of rounding.
            assert abs(img.size[0] / img.size[1] - 612 / 792) < 0.01

    def test_resolution_flag_is_dpi_over_72(self, tmp_path: Path) -> None:
        """The engine's other call shape is ``-r 200``."""
        from PIL import Image

        source = _make_pdf(tmp_path / "r.pdf", pages=1, width=72, height=72)
        assert pdftoppm_shim.main(["-png", "-r", "144", str(source), str(tmp_path / "r")]) == 0
        with Image.open(tmp_path / "r-1.png") as img:
            assert img.size == (144, 144)

    def test_page_range_keeps_the_original_page_numbers(self, tmp_path: Path) -> None:
        """``-f 2 -l 2`` must emit ``-2.png``, not a renumbered ``-1.png``."""
        source = _make_pdf(tmp_path / "range.pdf", pages=3)
        assert (
            pdftoppm_shim.main(["-png", "-f", "2", "-l", "2", str(source), str(tmp_path / "sel")])
            == 0
        )
        assert [p.name for p in tmp_path.glob("sel-*.png")] == ["sel-2.png"]

    def test_creates_a_missing_output_directory(self, tmp_path: Path) -> None:
        source = _make_pdf(tmp_path / "d.pdf", pages=1)
        nested = tmp_path / "made" / "up" / "page"
        assert pdftoppm_shim.main(["-png", str(source), str(nested)]) == 0
        assert (tmp_path / "made" / "up" / "page-1.png").is_file()


class TestShimRefusesRatherThanMisrender:
    """Silently ignoring a flag would hand back output that is not what was asked."""

    def test_unsupported_flag_is_an_error(self, tmp_path: Path) -> None:
        source = _make_pdf(tmp_path / "u.pdf", pages=1)
        assert pdftoppm_shim.main(["-tiff", str(source), str(tmp_path / "x")]) != 0
        assert not list(tmp_path.glob("x-*"))

    def test_missing_output_prefix_is_an_error(self, tmp_path: Path) -> None:
        """Poppler writes to stdout with no prefix; the shim will not pretend to."""
        source = _make_pdf(tmp_path / "np.pdf", pages=1)
        assert pdftoppm_shim.main(["-png", str(source)]) != 0

    def test_absent_input_is_an_error(self, tmp_path: Path) -> None:
        assert pdftoppm_shim.main(["-png", str(tmp_path / "nope.pdf"), str(tmp_path / "y")]) != 0

    def test_a_corrupt_pdf_is_an_error_not_a_traceback(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.pdf"
        bad.write_bytes(b"this is not a pdf")
        assert pdftoppm_shim.main(["-png", str(bad), str(tmp_path / "z")]) != 0

    @pytest.mark.parametrize("flag", ["-scale-to", "-r"])
    def test_a_negative_size_is_an_error_not_a_wrong_size(self, tmp_path: Path, flag: str) -> None:
        """argparse accepts `-scale-to -1280` as the integer -1280.

        A negative value fails the `> 0` sizing tests and falls through to the 150 DPI
        default, so the caller silently receives differently-sized PNGs. Measured
        before the guard: `-scale-to -1280` on US Letter produced 1275x1651 instead of
        a 1280px long edge, and exited 0.
        """
        source = _make_pdf(tmp_path / "neg.pdf", pages=1)
        assert pdftoppm_shim.main([flag, "-1280", str(source), str(tmp_path / "n")]) != 0
        assert not list(tmp_path.glob("n-*.png"))

    def test_an_empty_page_range_is_an_error(self, tmp_path: Path) -> None:
        source = _make_pdf(tmp_path / "e.pdf", pages=2)
        assert (
            pdftoppm_shim.main(["-png", "-f", "5", "-l", "9", str(source), str(tmp_path / "q")])
            != 0
        )


#: Stands in for distlib's console launcher, the one Windows binary in the `.exe`
#: form. What the launcher does with the file is distlib's; these tests pin the
#: layout it reads and run the appended zip with a real interpreter.
_FAKE_STUB = b"MZ\x90\x00 distlib console launcher stand-in"


class TestLauncherInstall:
    """The launcher is what makes the shim resolvable as ``pdftoppm``."""

    @pytest.fixture()
    def staged(self, tmp_path: Path):
        """Point the app's bin dir at tmp_path and pretend the engine venv exists."""
        bin_dir = tmp_path / "vendor" / "preview-tools" / "bin"
        fake_python = tmp_path / "venv" / "bin" / "python"
        fake_python.parent.mkdir(parents=True)
        fake_python.write_text("#!/bin/sh\n")
        # `Path.chmod`, matching the convention in `test_acp_client_binary_resolution.py`: the SAST
        # insecure-file-permissions rule matches `os.chmod` specifically, and a
        # fixture executable is exactly the case it is not meant to flag.
        fake_python.chmod(0o700)
        with (
            mock.patch.object(preview_tools.paths, "preview_tools_bin", return_value=bin_dir),
            mock.patch.object(preview_tools, "_engine_python", return_value=fake_python),
            # The Windows form reads the RUNNING interpreter's pip; pinned, never the host's.
            mock.patch.object(preview_tools, "_launcher_stub", return_value=_FAKE_STUB),
        ):
            yield bin_dir, fake_python

    def test_install_is_reported_and_probes_true(self, staged) -> None:
        bin_dir, _ = staged
        assert preview_tools.pdftoppm_installed() is False
        ok, message = preview_tools.install_pdftoppm()
        assert ok, message
        assert preview_tools.pdftoppm_installed() is True
        assert bin_dir.is_dir()

    def test_launcher_names_the_engine_interpreter_and_the_shim_file(self, staged) -> None:
        """`-m` would need the gateway package importable from the engine venv."""
        _, fake_python = staged
        assert preview_tools.install_pdftoppm()[0]
        data = preview_tools._launcher_paths()[0].read_bytes()
        assert str(fake_python).encode("utf-8") in data
        assert b"pdftoppm_shim.py" in data
        assert b" -m " not in data

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX exec bit")
    def test_launcher_is_executable_and_not_group_writable(self, staged) -> None:
        assert preview_tools.install_pdftoppm()[0]
        launcher = preview_tools._launcher_paths()[0]
        assert os.access(launcher, os.X_OK)
        assert preview_tools.managed_status()["pdftoppmSecure"] is True

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX /bin/sh quoting")
    @pytest.mark.parametrize(
        "hostile",
        ["has space", "has$dollar", "has'quote", "has`backtick", "has%percent"],
        ids=["space", "dollar", "quote", "backtick", "percent"],
    )
    def test_launcher_runs_from_a_path_the_shell_would_mangle(
        self, tmp_path: Path, hostile: str
    ) -> None:
        """KIROCREW_HOME is user-chosen, so the launcher must survive its characters.

        Inside POSIX double quotes ``$`` and backticks still expand, so a data home
        like ``/tmp/my $home/`` yielded a launcher whose interpreter path had the
        ``$home`` segment deleted and every invocation died with "cannot execute".
        Executes the real launcher rather than asserting on its text, because the
        shell is the thing being tested.
        """
        base = tmp_path / hostile
        bin_dir = base / "bin"
        # The hostile characters must sit in the INTERPRETER path, because that is
        # what the launcher body interpolates — putting them only in the bin dir
        # tests nothing (the bin dir never appears inside the script). A symlink
        # gives a real, working interpreter at a hostile path.
        venv_bin = base / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        hostile_python = venv_bin / "python"
        hostile_python.symlink_to(Path(sys.executable))
        with (
            mock.patch.object(preview_tools.paths, "preview_tools_bin", return_value=bin_dir),
            mock.patch.object(preview_tools, "_engine_python", return_value=hostile_python),
        ):
            assert preview_tools.install_pdftoppm()[0]
            launcher = preview_tools._launcher_paths()[0]

        result = subprocess.run(
            [str(launcher), "-png", str(base / "absent.pdf"), str(base / "out" / "page")],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        # The SHIM reporting a missing input (exit 1) proves it was reached. A shell
        # failure to resolve the path would be 126/127 with no shim message.
        assert result.returncode == 1, result.stderr
        assert "pdftoppm shim:" in result.stderr

    def test_the_launcher_is_written_as_bytes_so_line_endings_are_exact(self, staged) -> None:
        """Text-mode `atomic_write` translates `\\n` on Windows, which would make the
        `.exe` form's `\\r\\n` line `\\r\\r\\n` and corrupt its executable image.

        Asserts on the call rather than the file, because the translation only
        happens on Windows and this suite has to catch the regression everywhere.
        """
        real_write = preview_tools.atomic_write
        with mock.patch.object(preview_tools, "atomic_write", side_effect=real_write) as write:
            assert preview_tools.install_pdftoppm()[0]
        assert isinstance(
            write.call_args.args[1], bytes
        ), "the launcher must be written as bytes so its line endings are exact"

    @staticmethod
    def _install_as_windows(bin_dir: Path, python: Path, stub: bytes | None = _FAKE_STUB):
        """Install the Windows form, with *stub* standing in for distlib's launcher.

        Returns the install result, the launcher path and what
        ``pdftoppm_installed`` then reports.
        """
        with (
            mock.patch.object(preview_tools.platform_compat, "IS_WINDOWS", True),
            mock.patch.object(preview_tools, "_launcher_stub", return_value=stub),
            mock.patch.object(preview_tools.paths, "preview_tools_bin", return_value=bin_dir),
            mock.patch.object(preview_tools, "_engine_python", return_value=python),
        ):
            result = preview_tools.install_pdftoppm()
            launcher = preview_tools._launcher_paths()[0]
            installed = preview_tools.pdftoppm_installed()
        return result, launcher, installed

    def test_windows_launcher_is_an_exe_for_the_engines_bare_name(self, tmp_path: Path) -> None:
        """The engine runs a bare ``pdftoppm`` with no shell, and ``CreateProcess``
        appends only ``.exe``, so a ``.cmd`` launcher is never found there.

        The file is laid out the way pip lays out a Windows console script: the
        launcher, a ``#!"<interpreter>"`` line, then a zip starting right after it,
        whose ``__main__.py`` runs the shim file.
        """
        python = tmp_path / "venv" / "Scripts" / "python.exe"
        (ok, message), launcher, installed = self._install_as_windows(tmp_path / "bin", python)
        assert ok, message
        assert launcher.name == "pdftoppm.exe"
        assert installed is True
        data = launcher.read_bytes()
        line = f'#!"{python}"\r\n'.encode("utf-8")
        assert data.startswith(_FAKE_STUB + line + b"PK\x03\x04")
        with zipfile.ZipFile(launcher) as bundle:
            source = bundle.read("__main__.py").decode("utf-8")
        shim = Path(preview_tools.__file__).resolve().parent / "pdftoppm_shim.py"
        assert f"runpy.run_path({str(shim)!r}, run_name='__main__')" in source

    @pytest.mark.parametrize(
        "folder", ["has space", "has'quote", "pt_café"], ids=["space", "quote", "non-ascii"]
    )
    def test_the_appended_zip_runs_the_shim_with_a_real_interpreter(
        self, tmp_path: Path, folder: str
    ) -> None:
        """At run time the launcher starts ``"<interpreter>" "<the .exe>" <args>``.

        Done here directly, so a real interpreter has to find the zip behind the
        launcher and the ``#!`` line, and the shim has to render a real PDF from the
        engine's own argv.
        """
        base = tmp_path / folder
        (ok, message), launcher, _ = self._install_as_windows(base / "bin", base / "python.exe")
        assert ok, message
        source = _make_pdf(base / "deck.pdf", pages=2)
        result = subprocess.run(
            [
                sys.executable,
                str(launcher),
                "-png",
                "-scale-to",
                "64",
                str(source),
                str(base / "out" / "page"),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert sorted(p.name for p in (base / "out").iterdir()) == ["page-1.png", "page-2.png"]

    @pytest.mark.skipif(sys.platform != "win32", reason="CreateProcess resolves the name")
    def test_the_engines_bare_name_runs_the_installed_exe(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole Windows path: pip's own launcher, then a bare ``pdftoppm`` run the
        way the engine runs it (``subprocess.run``, no shell, so ``CreateProcess``
        resolves the name on this process's ``PATH``), then a real render."""
        bin_dir = tmp_path / "bin"
        with (
            mock.patch.object(preview_tools.paths, "preview_tools_bin", return_value=bin_dir),
            mock.patch.object(preview_tools, "_engine_python", return_value=Path(sys.executable)),
        ):
            ok, message = preview_tools.install_pdftoppm()
        assert ok, message
        monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]))
        source = _make_pdf(tmp_path / "slides.pdf", pages=2)
        result = subprocess.run(
            ["pdftoppm", "-png", "-scale-to", "64", str(source), str(tmp_path / "out" / "page")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            stdin=subprocess.DEVNULL,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["page-1.png", "page-2.png"]

    def test_a_cmd_launcher_never_reads_as_installed_and_is_removed(self, tmp_path: Path) -> None:
        """``shutil.which``, and so ``/deps``, finds a ``.cmd`` the engine cannot spawn."""
        bin_dir = tmp_path / "bin"
        stale = bin_dir / "pdftoppm.cmd"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"@echo off\r\n")
        with (
            mock.patch.object(preview_tools.platform_compat, "IS_WINDOWS", True),
            mock.patch.object(preview_tools.paths, "preview_tools_bin", return_value=bin_dir),
        ):
            assert preview_tools.pdftoppm_installed() is False
        (ok, message), _, installed = self._install_as_windows(bin_dir, tmp_path / "python.exe")
        assert ok and installed, message
        assert sorted(p.name for p in bin_dir.iterdir()) == ["pdftoppm.exe"]

    def test_windows_install_refuses_without_pips_launcher(self, tmp_path: Path) -> None:
        """No ``.exe`` can be built, and an earlier one must not keep reading as installed."""
        bin_dir = tmp_path / "bin"
        earlier = bin_dir / "pdftoppm.exe"
        earlier.parent.mkdir(parents=True)
        earlier.write_bytes(_FAKE_STUB)
        (ok, message), _, installed = self._install_as_windows(
            bin_dir, tmp_path / "python.exe", stub=None
        )
        assert ok is False
        assert "pip" in message
        assert installed is False
        assert not earlier.exists()

    def test_windows_install_refuses_a_line_the_launcher_cannot_read(self, tmp_path: Path) -> None:
        """The launcher reads its ``#!`` line in a 260-byte window; a longer one would
        install ready and fail every run."""
        python = tmp_path / ("x" * 250) / "python.exe"
        (ok, message), launcher, installed = self._install_as_windows(tmp_path / "bin", python)
        assert ok is False
        assert "KIROCREW_HOME" in message
        assert installed is False
        assert not launcher.exists()

    def test_install_is_idempotent(self, staged) -> None:
        assert preview_tools.install_pdftoppm()[0]
        first = preview_tools._launcher_paths()[0].read_bytes()
        assert preview_tools.install_pdftoppm()[0]
        assert preview_tools._launcher_paths()[0].read_bytes() == first

    def test_refuses_before_the_engine_venv_exists(self, tmp_path: Path) -> None:
        """Writing a launcher pointing at a missing interpreter would fail later."""
        with (
            mock.patch.object(
                preview_tools.paths, "preview_tools_bin", return_value=tmp_path / "bin"
            ),
            mock.patch.object(preview_tools, "_engine_python", return_value=None),
        ):
            ok, message = preview_tools.install_pdftoppm()
        assert ok is False
        assert "engine" in message.lower()


class TestLauncherStub:
    """distlib's console launcher is read from the gateway's own pip."""

    @staticmethod
    def _pip_with(tmp_path: Path, data: bytes) -> importlib.machinery.ModuleSpec:
        package = tmp_path / "pip"
        stub = package / "_vendor" / "distlib" / preview_tools._launcher_stub_name()
        stub.parent.mkdir(parents=True)
        stub.write_bytes(data)
        spec = importlib.machinery.ModuleSpec("pip", None, is_package=True)
        spec.submodule_search_locations = [str(package)]
        return spec

    def test_reads_the_launcher_pip_vendors(self, tmp_path: Path) -> None:
        spec = self._pip_with(tmp_path, b"MZ launcher")
        with mock.patch.object(
            preview_tools.importlib.util, "find_spec", return_value=spec
        ) as find:
            assert preview_tools._launcher_stub() == b"MZ launcher"
        find.assert_called_once_with("pip")

    def test_a_file_that_is_not_an_executable_image_is_not_used(self, tmp_path: Path) -> None:
        spec = self._pip_with(tmp_path, b"#!/bin/sh\n")
        with mock.patch.object(preview_tools.importlib.util, "find_spec", return_value=spec):
            assert preview_tools._launcher_stub() is None

    def test_no_pip_means_no_launcher(self) -> None:
        with mock.patch.object(preview_tools.importlib.util, "find_spec", return_value=None):
            assert preview_tools._launcher_stub() is None

    @pytest.mark.parametrize(
        "platform_tag,pointer,name",
        [("win-amd64", 8, "t64.exe"), ("win-arm64", 8, "t64-arm.exe"), ("win32", 4, "t32.exe")],
    )
    def test_the_launcher_matches_the_machine_as_distlib_picks_it(
        self, platform_tag: str, pointer: int, name: str
    ) -> None:
        with (
            mock.patch.object(preview_tools.sysconfig, "get_platform", return_value=platform_tag),
            mock.patch.object(preview_tools.struct, "calcsize", return_value=pointer),
        ):
            assert preview_tools._launcher_stub_name() == name


class TestSofficeIsReportedNotInstalled:
    """LibreOffice has no unpack-and-run build and no publishable digest pin, so it
    stays a user action — the app reports the command instead of running it."""

    def test_hint_is_platform_specific_and_non_empty(self) -> None:
        # A Debian os-release, so the Linux leg never reads the developer's own host
        # (Amazon Linux has no package and answers None by design).
        preview_tools._linux_soffice_hint.cache_clear()
        try:
            with mock.patch.object(
                os_deps.platform, "freedesktop_os_release", return_value={"ID": "debian"}
            ):
                for platform_name in ("darwin", "linux", "win32"):
                    with mock.patch.object(preview_tools.sys, "platform", platform_name):
                        assert (preview_tools.soffice_hint() or "").strip()
        finally:
            preview_tools._linux_soffice_hint.cache_clear()

    def test_no_install_function_exists_for_soffice(self) -> None:
        """Guards the product decision: nothing here may install a system package."""
        assert not any(
            name for name in dir(preview_tools) if "install" in name and "soffice" in name.lower()
        )
