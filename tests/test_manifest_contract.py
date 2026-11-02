"""What an existing user's copy of the built-in needs from this repository.

Kiro Crew adopts a user's built-in record onto this repository in place, at a
pinned commit, with no consent dialog, so the tree must be something it can take
over unchanged:

- the same name, version, permissions, dependencies, platform and
  ``defaultEnabled`` as the built-in (``tests/fixtures/builtin-app.json`` is its
  manifest at the commit this was ported from). A widened grant would be held
  back or force the app off, and adoption refuses that;
- the ``pptx-maker`` skill, now shipped and declared by the app instead of by
  Kiro Crew, and the four agent templates the built-in shipped, byte for byte
  (the gateway renders their placeholders, see ``backend/provision.py``);
- nothing that needs a build at install time, and no symlinks or file names a
  Windows checkout cannot hold;
- a committed UI bundle that imports only the seven modules the dashboard
  provides, and from the two SDK modules only names every supported host exports
  (a named import of a missing export fails the whole bundle at load);
- ``minKiroCrewVersion`` set, so a host without the App Kit seams refuses a
  fresh install instead of serving a half-working app.
"""

from __future__ import annotations

import json
import os
import re
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
BUNDLE = APP_ROOT / "ui" / "dist" / "index.mjs"

HOST_MODULES = {
    "react",
    "react-dom",
    "react/jsx-runtime",
    "lucide-react",
    "@tanstack/react-query",
    "@kirocrew/app-sdk",
    "@kirocrew/app-sdk/ui",
}

# What a host BEFORE App Kit level 1 exports from each SDK module (its
# public/vendor/*.mjs stubs). A named import from these modules must stay in this
# set; a level-1 name is read off a namespace import in ui/src/hostSdk.ts.
LEVEL0_EXPORTS = {
    "@kirocrew/app-sdk": {
        "useAppApi",
        "useAppEvents",
        "useTheme",
        "useAppInfo",
        "useNavigate",
        "useNotify",
        "useNavBadge",
        "useChatLauncher",
        "AppApiProvider",
        "useImeGuard",
        "useLanguageGeneration",
        "activeLocale",
        "fmtNumber",
        "fmtDate",
        "fmtTime",
        "fmtDateTime",
        "fmtRelative",
        "compareText",
    },
    "@kirocrew/app-sdk/ui": {
        "Card",
        "CardTitle",
        "Btn",
        "SendBtn",
        "Input",
        "SearchInput",
        "Badge",
        "SourceBadge",
        "StatCard",
        "Skeleton",
        "ContentSkeleton",
        "EmptyState",
        "PageHeader",
        "Toggle",
        "InfoTip",
        "SegmentedControl",
        "MarkdownRenderer",
        "Clickable",
        "Modal",
        "ErrorNotice",
        "SettingsSection",
        "SettingsCard",
        "SettingsInput",
        "SettingsToggle",
        "SettingsSelect",
    },
}

# Directories git ignores here (see .gitignore): never part of a clone.
_UNTRACKED_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".venv"}
_WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])$", re.IGNORECASE)
_STATIC_IMPORT = re.compile(
    r"^import\s+(?:(?P<clause>[^'\"]+?)\s+from\s+)?[\"'](?P<spec>[^\"']+)[\"'];?\s*$",
    re.MULTILINE,
)


def _manifest() -> dict:
    return json.loads((APP_ROOT / "app.json").read_text(encoding="utf-8"))


def _builtin() -> dict:
    return json.loads((APP_ROOT / "tests" / "fixtures" / "builtin-app.json").read_text("utf-8"))


def _bundle_imports() -> list[tuple[str, str]]:
    return [(m["spec"], (m["clause"] or "").strip()) for m in _STATIC_IMPORT.finditer(_text())]


def _text() -> str:
    return BUNDLE.read_text(encoding="utf-8")


def _named(clause: str) -> set[str]:
    """The imported names of ``{ a, b as c }``; empty for a namespace import."""
    match = re.search(r"\{([^}]*)\}", clause)
    if not match:
        return set()
    return {part.split(" as ")[0].strip() for part in match.group(1).split(",") if part.strip()}


def _tree() -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(APP_ROOT):
        dirnames[:] = [d for d in dirnames if d not in _UNTRACKED_DIRS]
        files += [Path(dirpath) / name for name in filenames]
        files += [Path(dirpath) / d for d in dirnames if (Path(dirpath) / d).is_symlink()]
    return files


class TestTheBundle(unittest.TestCase):
    def test_the_entry_the_manifest_names_is_committed(self) -> None:
        ui = _manifest()["ui"]
        self.assertEqual(ui["entry"], "dist/index.mjs")
        # ui.entry is relative to ui/.
        self.assertTrue((APP_ROOT / "ui" / ui["entry"]).is_file(), "run `npm run build` in ui/")

    def test_it_imports_only_what_the_host_provides(self) -> None:
        imports = _bundle_imports()
        self.assertTrue(imports, "no import statements found; the scan is broken")
        # Every top-level import statement is one the pattern read in full.
        starts = re.findall(r"^import\b", _text(), re.MULTILINE)
        self.assertEqual(len(starts), len(imports), "an import the scan could not parse")
        self.assertLessEqual({spec for spec, _ in imports}, HOST_MODULES)
        self.assertNotRegex(_text(), r"\bimport\s*\(", "a dynamic import would load a second file")

    def test_named_sdk_imports_exist_on_every_supported_host(self) -> None:
        for spec, clause in _bundle_imports():
            if spec in LEVEL0_EXPORTS:
                with self.subTest(module=spec):
                    self.assertLessEqual(_named(clause), LEVEL0_EXPORTS[spec])

    def test_level_1_names_are_feature_detected(self) -> None:
        text = _text()
        for name in ("guideCaution",):
            with self.subTest(name=name):
                self.assertIn(f'"{name}" in ', text)

    def test_no_build_machine_path_is_baked_in(self) -> None:
        self.assertNotRegex(_text(), r"/(?:home|Users|workplace)/|[A-Za-z]:\\\\")


class TestTheManifest(unittest.TestCase):
    def test_declares_the_first_kiro_crew_with_the_seams(self) -> None:
        self.assertRegex(_manifest().get("minKiroCrewVersion", ""), r"^\d+\.\d+\.\d+$")

    def test_what_adoption_compares_is_the_builtins(self) -> None:
        manifest, builtin = _manifest(), _builtin()
        for key in (
            "name",
            "version",
            "author",
            "license",
            "tags",
            "permissions",
            "dependencies",
            "defaultEnabled",
            "agents",
        ):
            with self.subTest(field=key):
                self.assertEqual(manifest[key], builtin[key])
        self.assertEqual(manifest["platform"], builtin["platform"])

    def test_declares_nothing_adoption_cannot_carry(self) -> None:
        manifest = _manifest()
        self.assertNotIn("mcpServers", manifest)
        self.assertNotIn("overlays", manifest["ui"])
        self.assertNotIn("sessionApproval", manifest)
        self.assertIs(manifest["defaultEnabled"], False)

    def test_the_page_carries_no_glyph_of_its_own(self) -> None:
        """The legacy route and sidebar glyph come from Kiro Crew's pin, not from here."""
        pages = _manifest()["ui"]["pages"]
        self.assertEqual(pages, [{"route": "/apps/pptx-maker", "label": "PPTX Maker"}])

    def test_listing_copy_matches_the_bundled_catalogs(self) -> None:
        """One source per string: the l10n block is the catalogs' ``manifest`` subtree,
        and the English fields are the built-in's own."""
        manifest, builtin = _manifest(), _builtin()
        catalogs = {
            p.stem: json.loads(p.read_text(encoding="utf-8"))["apps"]["pptxMaker"]["manifest"]
            for p in sorted((APP_ROOT / "ui" / "i18n").glob("*.json"))
        }
        english = catalogs.pop("en")
        for key in ("displayName", "description", "highlights", "useCases", "configuration"):
            with self.subTest(field=key):
                self.assertEqual(manifest[key], builtin[key])
        self.assertEqual(manifest["displayName"], english["display_name"])
        self.assertEqual(manifest["description"], english["description"])
        self.assertEqual(manifest["ui"]["pages"][0]["label"], english["page_label"])
        count = len(manifest["highlights"])
        self.assertEqual(
            manifest["highlights"], [english[f"highlight_{i}"] for i in range(1, count + 1)]
        )
        self.assertEqual(set(manifest["l10n"]), set(catalogs))
        for locale, strings in catalogs.items():
            with self.subTest(locale=locale):
                self.assertEqual(
                    manifest["l10n"][locale],
                    {
                        "displayName": strings["display_name"],
                        "description": strings["description"],
                        "pageLabel": strings["page_label"],
                        "highlights": [strings[f"highlight_{i}"] for i in range(1, count + 1)],
                    },
                )

    def test_art_is_repo_relative_png(self) -> None:
        manifest = _manifest()
        for key in (
            "iconPath",
            "heroImage",
            "heroImageDark",
            "heroImageDetail",
            "heroImageDetailDark",
        ):
            with self.subTest(field=key):
                self.assertRegex(manifest[key], r"^assets/[a-z0-9-]+\.png$")
                self.assertTrue((APP_ROOT / manifest[key]).is_file())
        for shot in manifest["screenshots"]:
            self.assertTrue((APP_ROOT / shot).is_file(), shot)

    def test_hooks_name_callables(self) -> None:
        import importlib

        for hook, target in _manifest()["backend"]["hooks"].items():
            with self.subTest(hook=hook):
                module, _, attr = target.partition(":")
                self.assertTrue(callable(getattr(importlib.import_module(module), attr)))

    def test_the_skill_is_declared_and_names_this_app(self) -> None:
        declared = _manifest()["skills"]
        self.assertEqual(declared, ["skills/pptx-maker"])
        text = (APP_ROOT / "skills" / "pptx-maker" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("\nname: pptx-maker\n", text)
        self.assertIn("\napp: pptx-maker\n", text)

    def test_the_skill_names_every_shipped_agent(self) -> None:
        text = (APP_ROOT / "skills" / "pptx-maker" / "SKILL.md").read_text(encoding="utf-8")
        for rel in _manifest()["agents"]:
            name = json.loads((APP_ROOT / rel).read_text(encoding="utf-8"))["name"]
            with self.subTest(agent=name):
                self.assertIn(f"`{name}`", text)

    def test_the_agent_templates_are_the_built_ins(self) -> None:
        """Byte for byte: the gateway renders and registers exactly these."""
        fixtures = APP_ROOT / "tests" / "fixtures" / "builtin-agents"
        for rel in _manifest()["agents"]:
            with self.subTest(agent=rel):
                self.assertEqual(
                    (APP_ROOT / rel).read_bytes(), (fixtures / Path(rel).name).read_bytes()
                )

    def test_l10n_is_within_the_host_parser_limits(self) -> None:
        """A locale over a limit is dropped by the host, silently for the reader."""
        l10n = _manifest()["l10n"]
        self.assertLessEqual(len(l10n), 32)
        for locale, entry in l10n.items():
            with self.subTest(locale=locale):
                self.assertRegex(locale, r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")
                self.assertLessEqual(
                    set(entry), {"displayName", "description", "pageLabel", "highlights"}
                )
                self.assertLessEqual(len(entry["displayName"]), 120)
                self.assertLessEqual(len(entry["description"]), 1000)
                self.assertLessEqual(len(entry["pageLabel"]), 60)
                self.assertLessEqual(len(entry["highlights"]), 12)
                self.assertTrue(all(len(h) <= 300 for h in entry["highlights"]))


class TestTheTreeIsAdoptable(unittest.TestCase):
    def test_no_root_build_file(self) -> None:
        for name in (
            "package.json",
            "pyproject.toml",
            "setup.py",
            "requirements.txt",
            ".gitattributes",
        ):
            with self.subTest(name=name):
                self.assertFalse((APP_ROOT / name).exists())

    def test_no_link_and_no_unportable_name(self) -> None:
        seen: dict[str, str] = {}
        for path in _tree():
            rel = path.relative_to(APP_ROOT).as_posix()
            with self.subTest(path=rel):
                self.assertFalse(path.is_symlink(), "a link is refused by adoption")
                self.assertTrue(path.is_file(), "only regular files")
                self.assertNotRegex(rel, r"[\\:]")
                for part in rel.split("/"):
                    self.assertFalse(part.endswith((".", " ")), part)
                    self.assertIsNone(_WINDOWS_RESERVED.match(part.split(".")[0]), part)
                self.assertNotIn(
                    rel.lower(), seen, f"differs only in case from {seen.get(rel.lower())}"
                )
                seen[rel.lower()] = rel

    def test_the_tree_is_within_the_adoption_caps(self) -> None:
        files = _tree()
        self.assertLessEqual(len(files), 5000)
        self.assertLessEqual(sum(p.stat().st_size for p in files), 64 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
