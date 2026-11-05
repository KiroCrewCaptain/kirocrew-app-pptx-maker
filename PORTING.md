# Porting notes: built-in → external app

This repository was ported from Kiro Crew's built-in PPTX Maker at KiroCrew `main`
`4bb268b4e9` (the last commit touching the app is `63c13386ca`):

- the backend, `src/kiro_crew/apps/builtins/pptx_maker/` (`app.json`,
  `ATTRIBUTION.md`, `agents/*.json`, `prompts/spec-studio.md`, `backend/*.py`,
  and its two in-package tests);
- the skill, which shipped in Kiro Crew core:
  `src/kiro_crew/builtin_skills/pptx-maker/SKILL.md`;
- the dashboard page, `website/src/apps/pptx-maker/`, and its store art,
  `website/public/app-assets/pptx-maker/`;
- the `apps.pptxMaker.*` strings of `website/src/i18n/locales/*.json` (plus
  `en.manual.json`);
- the tests: `test/test_pptx_maker_{agents,background_tasks,decks,engine,engine_source,library,owner_gate,paths,preview_tools,provision,routes,soffice_hint}.py`,
  `src/kiro_crew/apps/builtins/pptx_maker/tests/test_{venv_paths,preview_tool_discovery}.py`,
  and the frontend tests `website/src/test/PptxMakerPage.test.tsx`,
  `website/src/test/PptxDeckViewerCov80.test.tsx`,
  `website/src/test/SlidePreviewSanitize.test.tsx`,
  `website/src/test/SlidePreviewCoverage.test.tsx`,
  `website/src/apps/pptx-maker/LibraryPanel.coverage.test.tsx` and
  `website/src/apps/pptx-maker/api.coverage.test.ts`.

Paths with a line number below refer to that commit. A **gap** is something the
built-in gets from Kiro Crew that an external app has no public way to get yet;
closing one is a Kiro Crew change (a generic, app-agnostic App Kit seam), not a
change here.

## What carried over unchanged

- `app.json` `name` (`pptx-maker`), `version` (`0.3.0`), `author` (`sktok`),
  `license` (`MIT-0`), `tags`, `permissions` (including the twenty
  `@sdpm/*` `mcpTools`), `dependencies`, `platform`, `agents`,
  `defaultEnabled: false` and every display field (`displayName`,
  `description`, `highlights`, `useCases`, `configuration`), except the fifth
  highlight: the built-in's "reveal its folder" became "copy its folder path"
  (gap 4), in `app.json`, its `l10n` block and all 12 catalogs.
  `tests/test_manifest_contract.py` compares them with the built-in's manifest
  (`tests/fixtures/builtin-app.json`), because adoption refuses a manifest that
  widens a grant or forces the app off.
- The four agents, as the configs they render to. The built-in's templates
  (`tests/fixtures/builtin-agents/`) named `{ENGINE_ROOT}` / `{ENGINE_MCP_DIR}` /
  `{APP_PROMPTS}` / `{TOOLS_PATH}`, which only the built-in's own module could
  compute; these name the host placeholders the gateway computes for every app
  (`{APP_ROOT}`, `{APP_DATA}`, `{UV_BIN}`, `{HOST_PATH}`, `{PATH_SEP}`) and spell
  the engine and preview-tool subpaths out from `{APP_DATA}`. Rendered, each is
  the built-in's config (`tests/test_provision.py`
  `test_each_template_renders_to_the_built_ins_config`). The app-owned prompt
  resource `prompts/spec-studio.md` carried over (one section added, see below).
- Every route's method, validation, status codes, error `code`s and response
  shape. The handler bodies are the built-in's.
- The security behaviour: the engine is fetched by commit and accepted only when
  its sha256 matches `ENGINE_TARBALL_SHA256`, extracted with every member
  validated, swapped in with rollback; every engine and `uv` child runs in the
  sandbox's `strict` mode with a scrubbed environment and resource limits; every
  browser-derived path goes through `backend/paths.py`'s segment allow-list,
  resolve-and-contain check and the sensitive-path gate; every agent-written file
  is read through the descriptor-pinned, link-refusing reader; every textual
  artifact, style, template field, worker error and audit field is redacted, with
  the raster carve-out verified by signature and by the encoded-key scan; HTML and
  SVG artifacts are served with the sandboxing CSP; the board previews keep their
  egress-denying policy and `<link>` strip. These live in `backend/*.py`, which
  are the built-in's files with their imports changed.
- The data location: the gateway hands an app's hooks
  `ctx.data_dir = <data home>/apps/pptx-maker/data`, the directory the built-in
  resolved through `app_data_dir("pptx-maker")`
  (`src/kiro_crew/apps/builtins/pptx_maker/backend/paths.py:33`, `:67`). The
  engine (`vendor/sdpm/`, with its venv) and the managed preview tool
  (`vendor/preview-tools/`) keep their paths, so an existing user's engine is
  found where it is and nothing is downloaded again. Decks stay in the engine's
  `output_dir` (`~/.config/sdpm/decks` for a new user, `~/Documents/SDPM-Presentations`
  for one who used the engine before) and the styles, templates and engine config
  in `$XDG_CONFIG_HOME/sdpm/`, both outside the data home and untouched.
- The skill's directory name and frontmatter (`name: pptx-maker`,
  `app: pptx-maker`).

## What changed, and why

| Built-in | Here | Why |
|---|---|---|
| `"backend": {"routes": "backend.routes:register_routes"}` (`app.json:66-68`) | `"backend": {"hooks": {"routes": "backend.routes:register_routes"}}` | Top-level `backend.routes` is read only by the built-in boot loop (`src/kiro_crew/dashboard/routes/system.py:235-241`). An external app's routes go through the gateway's `RouteRegistry`. |
| `register_routes(app: web.Application)` adding 20 absolute routes (`backend/routes.py:1443-1479`) | `register_routes(ctx) -> list[Route]`, relative paths, handlers `(request, ctx)` | The `hooks.routes` contract. `Route` is the app's own frozen dataclass (`backend/_host.py`), accepted structurally from App Kit level 1. The table is `routes.ROUTES`. |
| `GET`/`PUT /api/apps/pptx-maker/config` (`backend/routes.py:1463-1464`) | `GET`/`PUT /api/apps/pptx-maker/settings`, same bodies | The gateway serves `/api/apps/{name}/config` itself (`src/kiro_crew/apps/routes.py:247-248`, an app's manifest configuration) and matches it before the registry's catch-all, so an app route there is never reached. The page calls `/settings`. |
| `GET /preview/{deck_id}/{subpath:.*}` (`backend/routes.py:1467-1469`) | Six routes, `/preview/{deck_id}/{p1}` … `/{p1}/…/{p6}`, joined back into the same `subpath` | The registry's `{param}` matches one segment (`src/kiro_crew/apps/route_registry.py:92`) and has no tail form. The engine writes artifacts at most two levels deep; containment is still `paths.resolve_deck_file`'s. |
| `_require_enabled`: an `is_app_enabled` read on every request (`backend/routes.py:429-446`) | Removed | The registry deregisters an app's routes on disable, so a disabled app answers 404. The `app_disabled` 403 is gone; the page never branched on it. |
| Owner gate through `kiro_crew.dashboard.handlers._shared.require_owner_dashboard_request` (`backend/routes.py:449-461`) | `ctx.require_owner(request, operation)` in `routes._refuse_non_owner` | The level-1 owner gate, the same host predicate. As in the built-in, a request carrying an app token passes (the token middleware already confines it to `permissions.api`), and every mutating dashboard request must be the owner's. Denials are audited as `pptx-maker.<verb>` (the built-in wrote `pptx_maker.<verb>`). A gateway without the seam refuses every dashboard caller. |
| `kiro_crew.security.redact` (`backend/paths.py:34`, `decks.py:40`, `routes.py:89-94`) | `ctx.scrub.outbound(text).text` (`_host.redact`) | The level-0 scrub seam, which applies the gateway's active policy on top of the same baseline passes. With no scrubber, or one that raises, the text is replaced by a fixed "withheld" marker, which every comparison (`redact(x) != x`) also reads as a refusal: a deck id that cannot be scanned is not served. |
| `sel().log_api_access(caller="core:pptx-maker", source="builtin-app", …)` (`backend/routes.py:464-492`) | `ctx.audit.record(operation, outcome, …)` (`_host.audit`), redacted then truncated | The level-0 audit seam. Operation names are unchanged; the row's caller is now `app:pptx-maker` and its source `app`. |
| `app_data_dir("pptx-maker")` (`backend/paths.py:33`, `:67`) | `ctx.data_dir`, bound by `register_routes` (`_host.bind`) | An external app has no ambient data home. The provisioning threads reach the bound context, so it is held in `_host`. With no context bound, `paths.app_root()` raises rather than guessing a location. |
| Absolute self-imports `from kiro_crew.apps.builtins.pptx_maker.backend import …` (`backend/routes.py:75`, `library.py:35`, `provision.py:42`, `:591`, `:633`, `preview_tools.py:40`, `decks.py:38`, `engine.py:41`) | Relative imports | Hook files load into a synthetic package (App Kit `manifest-reference.md`, "Importing your own modules"). |
| Package `__init__.py` re-exporting `register_routes` (`__init__.py:13-18`) | Removed | Only the built-in boot loop reads it. |
| `provision._stage_static` / `_copy_tree` copied `prompts/` into the install dir, and `_render_agents` wrote rendered agent configs there (`backend/provision.py:380-460`) | Removed; `render_agent_template` / `placeholder_values` state the template contract and are pinned by tests | For an installed app the install dir IS this checkout. Re-staging `prompts/` would delete and re-copy the directory onto itself, and rendered configs there would put an executable `{UV_BIN}` in a file the agent can rewrite and dirty the tree an update verifies. The gateway renders the templates itself into `<data home>/rendered-agents/` (gap 1). |
| `provision._register_resources` calling the private `kiro_crew.apps.bridges.register_app` (`backend/provision.py:491-528`) | Removed | Provisioning already did not call it (the gateway registers on enable and at start); it was kept only as a test seam. |
| `kiro_crew.atomic_write.atomic_write`, `kiro_crew.platform_compat.{IS_WINDOWS, strip_extended_length_prefix, chmod_safe}`, `kiro_crew.subprocess_utf8.UTF8_TEXT`, `kiro_crew.browser_cli.os_deps.{os_release_ids, rpm_package_manager}` | `backend/_fs.py`, `backend/_compat.py`, `engine.UTF8_TEXT`, `backend/_os_deps.py` | Small and carrying no security decision, so restated (see gap 6). |
| `kiro_crew.env.resolve_uv` (`backend/provision.py:45`) | The gateway's resolver through `_host.resolve_uv`, with the same ladder restated for a gateway without it | The gateway renders the same value into the agents' `{UV_BIN}`, so the two must agree. |
| `iconUrl` / `hero*` as absolute `/app-assets/pptx-maker/*.svg`, `screenshots` `/app-assets/…` (`app.json:85-99`) | `iconPath` / `hero*` / `screenshots` as repo-relative PNGs under `assets/` | A registry app's art is repo-relative, and the catalog's publish step refuses SVG for a non-builtin source. The PNGs were rendered from the built-in SVGs (kept beside them): the icon at 512×512 on an opaque tile with `--ico-a` / `--ico-b` resolved to fixed colours, the heroes at 1200×675 and the detail heroes at 1200×288. |
| `ui.pages` `[{"route": "/pptx-maker", "icon": "Presentation"}]`, page compiled into the dashboard (`app.json:69-77`, `website/src/apps/builtinRegistry.ts:70`) | `ui.entry: "dist/index.mjs"` (a committed ESM bundle built from `ui/src/`), `ui.pages` `[{"route": "/apps/pptx-maker"}]`, no `icon` | An installed app ships its page as a bundle AppHost loads. The legacy `/pptx-maker` route and `Presentation` glyph of an adopted record come from Kiro Crew's adoption pin, never from this manifest. |
| App Store copy keys in the dashboard catalogs (`website/src/components/appstore/appManifest.ts:349-363`) | `l10n` block in `app.json` for the 11 non-English locales | The listing copy travels with the app. It is the `apps.pptxMaker.manifest` subtree of `ui/i18n/<locale>.json`, kept equal by a test. |
| `prompts/spec-studio.md` said nothing about the composer's name; the engine's prompts tell the Spec and Vibe agents to start `sdpm-composer` (`mcp-local/acp-agent-prompts/vibe-agent.md:88` in the pinned engine), while the app's composer agent is `pptx-maker-composer` and the only one their `toolsSettings.subagent.availableAgents` allows | A "The slide composer" section mapping the engine's name to `pptx-maker-composer` | Without it, a live Vibe run on Kiro Crew `99161724c0` wrote the brief, outline and style and then reported that it could not start the slide builder, leaving a deck with no slides. The built-in ships the same prompt, so it has the same defect; the fix is in the app-owned prompt resource, which takes precedence over the engine's, so the engine checkout stays unmodified. |
| No page offered the icon packs: `api.ts` had `assets()` / `provisionAssets()` (`website/src/apps/pptx-maker/api.ts:186-190`) and the backend served `GET /assets` / `POST /assets/provision` (`backend/routes.py:884-910`), but no component called them | An **Install icon packs** note on the page (`ui/src/PptxMakerPage.tsx` `IconPacksNote`), shown once the engine is ready and the packs are not, over the same two routes; five new strings in every locale | The engine's asset search exits its MCP server when no pack is installed ("Assets not installed. Cannot continue."), which the agent reports as its slide tool going offline, so a live Vibe run stopped at the outline. No backend change. |
| Skill under Kiro Crew's `builtin_skills/`, synced into every install | `skills/pptx-maker`, declared in `app.json` `skills` | An app's skill ships with the app. Unchanged. |
| No `minKiroCrewVersion` | `"minKiroCrewVersion": "0.12.0"` | The first Kiro Crew with App Kit level 1. |

## Host controls without a seam: resolved from the gateway, fail closed

Nothing in `backend/` but `_host.py` names `kiro_crew`, and `_host.py` imports it
only lazily, by name, through one function (`_host._host_attr`;
`tests/test_no_host_imports.py` pins both). These are security controls: copying
them into this repository would leave a control that looks present and drifts
from the one the gateway enforces. Each is looked up at the moment it is needed;
when the gateway lacks it, the operation that needs it is **refused**, never run
without it, and `register_routes` reports the reason through `ctx.health`
(`tests/test_host_seams.py`).

| Built-in import | Where | Without it |
|---|---|---|
| `kiro_crew.sandbox.{sandboxed_spawn_argv, run_limited}` | `backend/engine.py:42`, `backend/provision.py:47` | `SandboxUnavailableError`: no engine snippet, icon script or `uv` call is spawned; provisioning reports the failure |
| `kiro_crew.asset_downloader.download_to` | `backend/engine_source.py:48` | The engine install is refused with its reason; nothing is fetched another way |
| `kiro_crew.security.{is_sensitive_path, path_contains_sensitive}` | `backend/paths.py:34`, `backend/routes.py:89-94` | Every path is treated as sensitive: no deck or library file resolves, no deck root is accepted |
| `kiro_crew.hooks.{safe_read_file_bytes_nolink, FileTooLargeError}` | `backend/decks.py:37-39`, `backend/library.py:34-37`, `backend/routes.py:74`, `:87` | Every deck file and style reads as unreadable (`None`) |
| `kiro_crew.messaging.raster.{sniff_raster_mime, SNIFF_BYTES}` | `backend/routes.py:88` | No inline bitmap is exempted from redaction (it is scanned as text) |
| `kiro_crew.credential_patterns.AWS_KEY_ID` | `backend/routes.py:85`, `:213` | Every inline bitmap is treated as credential-bearing and blanked |
| `kiro_crew.security.REDACTED_CREDENTIAL_TAG` | `backend/routes.py:90` | The same literal marker |
| `kiro_crew.executors.subprocess_executor` | `backend/routes.py:86` | The loop's default pool |

## Remaining gaps

1. **Agent template rendering is the built-in's, by name.** Closed. The gateway
   renders an app's agent template from a fixed host placeholder set it computes
   for every app (Kiro Crew `app-kit-platform.md` §34, "Agent-template
   placeholders"): `{APP_ROOT}`, `{APP_DATA}`, `{UV_BIN}`, `{HOST_PATH}`,
   `{PATH_SEP}`, never read from a file the app or its agent can write. The
   built-in instead computed `{ENGINE_ROOT}` and friends in its own
   `agent_placeholders.py`, which a released record no longer consults, so these
   templates use only the host set. `backend/provision.py` `PLACEHOLDERS` /
   `render_agent_template` / `placeholder_values` state this app's side of that
   contract, and `tests/test_provision.py` pins it on every platform's path shapes.
2. **The built-in's startup route loop.** While the built-in package is in
   `BUILTIN_NAMES`, the gateway registers its routes straight on the aiohttp
   router at start (`src/kiro_crew/dashboard/routes/system.py:235-241`), ahead of
   the registry's catch-all, so they would answer `/api/apps/pptx-maker/*` for an
   adopted record instead of this app's. Adoption has to take the name out of that
   loop for a record it moves (Kiro Crew's adoption change, not a change here).
3. **Sandboxed child processes, verified download, path and read gates.** The
   controls in the table above have no App Kit seam. A generic `ctx.subprocess`
   (spawn an argv through the gateway's chokepoint at a named sandbox mode, with
   the scrubbed environment and resource limits), a verified-download seam, and
   `ctx.paths.is_sensitive` / `contains_sensitive` / a pinned reader would remove
   every `kiro_crew` name from `_host.py`.
4. **Reveal folder.** The built-in's deck viewer revealed the deck folder in the
   host's file manager through the dashboard's private `revealOrOpen` and
   `useCanReveal` (`website/src/apps/pptx-maker/DeckViewer.tsx:16`, `:125`,
   `:195`), which call `/api/reveal`, a path outside this app's `permissions.api`;
   adoption refuses a widened grant. Here the button is **Copy folder path**
   (`ui/src/DeckViewer.tsx`). Closing this needs an SDK reveal seam scoped to the
   app's own files.
5. **Starting a deck chat.** The built-in created the session itself through the
   dashboard's private `api.createChatSlot(…, 'persistent', …, true)` and navigated
   to it (`website/src/apps/pptx-maker/PptxMakerPage.tsx:303-317`). Here the mode
   buttons call the SDK's `useChatLauncher().openChat({ agent, memoryMode:
   'persistent', sessionFolder: false })`, which opens the same kind of chat as a
   new draft: the agent's welcome message is shown and the session is created when
   the user sends the first message.
6. **File helper drift.** `backend/_fs.py`'s `atomic_write` keeps the temp-file
   + rename, the `0600` default, the mode-before-rename and a short Windows
   sharing-violation retry; it drops the host's data-home owner-only directory
   creation and its descriptor-pinned variants. A public file helper in the SDK
   would remove the copy.
7. **Execution trust.** A built-in's in-gateway Python is exempt from the
   third-party execution gate (`src/kiro_crew/apps/execution.py:393-415`, `:694`). This
   copy's hooks load only when the operator trusts it. For an existing user Kiro
   Crew's adoption writes that grant for this repository; a fresh install goes
   through the normal trust dialog.
8. **Styles.** The page keeps the built-in's Tailwind utility class strings. They
   render because the dashboard's stylesheet still carries them while the compiled
   built-in page ships with Kiro Crew.
9. **Store listing localization.** The catalog's publish step does not carry
   `l10n` yet, so a store row that is not installed shows the English fields.
10. **Sidebar icon on a fresh install.** The `Presentation` glyph and the
    `/pptx-maker` route are shown only for an adopted record (from Kiro Crew's
    pin). A fresh install lives at `/apps/pptx-maker` with the `iconPath` image.
11. **Skill duplicate on adoption.** Kiro Crew's builtin-skill sync has already
    copied `pptx-maker` into users' `<data home>/skills/` as a real directory. The
    app's own skill then gets only its namespaced link (`skills/pptx-maker/pptx-maker`;
    the gateway skips a flat link over a real directory), so until core stops
    shipping and prunes its copy both are present.
12. **Core references that move with the built-in.** Kiro Crew still names this
    app in `src/kiro_crew/metrics/feature_usage.py:203` (an official app counted
    by name), `src/kiro_crew/security_posture.py:1071`, `:1216-1217` (posture rows
    for the built-in's files), and imports the built-in's `engine` and
    `preview_tools` for the file panel's slide preview
    (`src/kiro_crew/dashboard/handlers/office_slides.py:204`, `:224`; KiroCrew
    PR #27435 moves that lookup into core). Each goes when the built-in goes.
13. **Background work on disable.** As in the built-in, an engine or icon-pack
    provisioning job that is running when the app is disabled finishes in the
    background; it writes only under the app's data dir and the engine's config
    dir. An `on_shutdown` hook could cancel it once the registry offers a
    cancellation point for executor work.

## UI

The page is `ui/src/`, ported from `website/src/apps/pptx-maker/` with the same
markup, class strings and behaviour apart from gaps 4 and 5. `npm run build` in
`ui/` writes the committed `ui/dist/index.mjs`; CI rebuilds it and fails on a
difference. Every request goes through the host's scoped client
(`useAppApi().raw`, handed to `api.setTransport` by the root), so a path outside
`permissions.api` is refused in the browser; the backend's `error` text is read
back out of the client's refusal. The `.pptx` download stays a plain link under
the app's own prefix. DOMPurify (the SVG slide sanitizer) is bundled; everything
else the page imports is a module the dashboard provides. Strings are `t()` over
`ui/i18n/<locale>.json` for all 12 dashboard locales, with the dashboard's own
key paths (`apps.pptxMaker.*`), plural forms by `Intl.PluralRules`; the two new
strings (`deckViewer.copy_folder_path`, `deckViewer.folder_path_copied`, and the
five `pptxMakerPage.icon_packs_*` / `install_icon_packs` strings) are in every
locale and `deckViewer.reveal_folder` is gone.

## Tests

| Built-in test | Here | Change |
|---|---|---|
| `test/test_pptx_maker_routes.py` | `tests/test_routes.py` | Driven through `tests/harness.py`, which serves the records `register_routes` returns the way the gateway's registry does (exact path, then one-segment `{param}` patterns; a raising handler is a 502). `TestDenyByDefault` became `TestRouteRegistration` (the route table, no route on a gateway-owned suffix, structural records, the context bound, no file touched, degraded health on an older host, a fresh context adopted) and `TestDisabledAppServesNothing`. `/config` cases call `/settings`. The audit cases read `ctx.audit` rows. |
| `test/test_pptx_maker_owner_gate.py` | `tests/test_owner_gate.py` | Through the harness; denials are the context's (`pptx-maker.<verb>`). Plus a gateway without the gate refusing every mutating route. Each async case runs on its own `asyncio.run` loop. |
| `test/test_pptx_maker_provision.py` | `tests/test_provision.py` | `TestResolveUv` runs against the gateway's resolver and, removed, the restated ladder. `TestRenderAgents` pins `render_agent_template` / `placeholder_values` (no placeholder left or undeclared, Windows paths and quotes, the managed tool on the MCP `PATH`, a system tool winning, the prompt resource in this checkout, the engine paths `paths` derives, the built-in's rendered configs) and that provisioning writes nothing into the checkout. Dropped: `TestStageStatic` and the `_register_resources` cases (the code is gone). The shipped-agent `@server` check no longer consults Kiro Crew's private managed-server list (these specs name none). |
| `test/test_pptx_maker_engine_source.py` | `tests/test_engine_source.py` | Downloads go through the fake host's verified downloader over the same mocked opener; plus a gateway without the downloader. Dropped: the three `redact_url` cases, the SSL-context and the https-only-redirect cases, which test the host's `asset_downloader` (Kiro Crew keeps them). |
| `test/test_pptx_maker_{agents,decks,engine,library,paths,preview_tools,soffice_hint,background_tasks}.py`, the two in-package tests | `tests/test_{agents,decks,engine,library,paths,preview_tools,soffice_hint,background_tasks,venv_paths,preview_tool_discovery}.py` | Imports only. `test_agents.py` reads `ui/src/PptxMakerPage.tsx` and no longer skips without it. |
| (none) | `tests/test_host_seams.py`, `tests/test_no_host_imports.py`, `tests/test_manifest_contract.py` | Every fail-closed path above; only `_host.py` reaches `kiro_crew`, lazily; manifest parity with the built-in, the `l10n` block, repo-relative PNG art, hooks resolve, the skill and agents, the committed bundle importing only host modules (and only level-0 names by name), an adoptable tree. |
| `website/src/test/PptxMakerPage.test.tsx`, `PptxDeckViewerCov80.test.tsx`, `SlidePreviewSanitize.test.tsx`, `SlidePreviewCoverage.test.tsx`, `apps/pptx-maker/LibraryPanel.coverage.test.tsx`, `apps/pptx-maker/api.coverage.test.ts` | `ui/tests/` | Run by vitest with stand-ins for the host SDK (`ui/tests/stubs/`). The two SlidePreview files (the SVG sanitizer, the only place agent-written SVG becomes DOM, and the component's render paths) port with import paths only. The deck-start cases assert the chat launcher call (gap 5); the reveal cases became copy-path cases (gap 4); the settings cases expect `/settings`. Plus `ui/tests/externalSeams.test.tsx`: the catalogs, the transport, no private dashboard import, the older-host fallback. |

`tests/fakehost.py` stands in for the gateway's controls so the suite needs no
Kiro Crew install; its redaction fake recognises the credential shapes these
tests use at their real lengths. The route, deck, path and library suites were
also run against the real controls of the Kiro Crew commit named in
`docs/media/media.json` (the real `ScrubSDK`, sensitive-path gate, pinned reader,
raster sniffer and key pattern) and pass unchanged.

## What Kiro Crew keeps doing

The release that adds adoption still ships the built-in. A user's built-in
record moves onto this repository at the commit Kiro Crew pins, after the tree is
fetched and verified; a record that cannot move keeps running the built-in copy,
unchanged. Because of gaps 1 and 2, adopting PPTX Maker needs the built-in
package to stay importable for its agent values and to leave the startup route
loop. Removing the built-in, its skill, its catalog keys and the core references
(`src/kiro_crew/apps/builtins/__init__.py:19`,
`src/kiro_crew/apps/bridges_agents.py:734-771`,
`src/kiro_crew/dashboard/handlers/office_slides.py:204`, `:224`,
`src/kiro_crew/security_posture.py:1071`, `:1216-1217`,
`src/kiro_crew/metrics/feature_usage.py:203`,
`website/src/apps/builtinRegistry.ts:70`,
`website/src/components/appstore/appManifest.ts:349-363`) is a later release.

## Release handoff: one commit for both pins

1. Call the full 40-hex SHA of the commit to pin `C` (`git rev-parse HEAD`).
2. In the Kiro Crew release PR that adds adoption, run
   `python3 scripts/adoption_pin.py compute <this URL> C` and add the row it
   prints to `src/kiro_crew/apps/adoption-pins.json`; then
   `python3 scripts/adoption_pin.py verify`.
3. The KiroCrewApps row for `pptx-maker` switches from `"type": "builtin"` to
   `"type": "git"`, this URL, `"ref": "C"`, never a branch name.
4. Check that both files name the same `C`, and that GitHub serves it
   (`git fetch origin C` in a fresh clone, then `git cat-file -t C` prints
   `commit`).
