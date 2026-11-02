# capture/: README screenshots and screen recordings

`capture.py` produces every picture and the walkthrough recording in
`docs/media/` from a **real Kiro Crew dashboard**, so the README shows the app
exactly as a user sees it. It is driven by one file per app, `scenes.json`.

| File | What it is |
|---|---|
| `capture.py` | The harness: boots the gateway, seeds data, takes the pictures, records the video |
| `stub_backend.py` | The offline model stand-in a gateway runs for scenes that show no chat reply: Kiro Crew's stub ACP backend |
| `scenes.json` | This app's demo data, screenshots and walkthrough script (start from `scenes.example.json`) |
| `check_readme.py` | Checks README.md against the standard in `CHECKLIST.md` (CI runs it) |

## What one run does

1. **Boots an isolated gateway** from the Kiro Crew checkout you pass with
   `--kirocrew`, through that checkout's own test harness
   (`kiro_crew.testing.harness.spawn_feature_gateway`):
   - a throwaway `KIROCREW_HOME` under the system temp directory, seeded from a
     packaged fixture (`"fixture"`, default `minimal`);
   - a free port on loopback only (`127.0.0.1` / `::1`);
   - for a scene marked `"live": true` (the chats), a **real model**: the
     gateway runs your own installed `kiro-cli` (from `PATH`, or `--kiro-cli` /
     `KIROCREW_CAPTURE_KIRO_CLI`) with its existing sign-in, and the agent's
     model stays `"auto"`. The harness checks `kiro-cli whoami` succeeds before
     it captures anything, prints none of its output, and copies no credential.
     If kiro-cli is missing or not signed in, the run stops: there is no
     scripted fallback. The replies are that model's own, so they differ from
     run to run, and each live chat turn uses some of your Kiro credits;
   - for every other scene, Kiro Crew's **stub ACP backend** instead of a real
     model, so no model is called and nothing costs money. `stub_backend.py`
     wraps it to answer the credit-balance probe at once so the top bar does not
     show a spinner. The gateway runs it through a launcher written into the
     scratch directory (a `sh` script, or a `.cmd` on Windows);
   - approval mode `reads` (never `yolo`). It approves read-style tool calls
     such as a web page fetch and declines the rest, a web search included.

   The browser answers the top bar's credit readout (`/api/sessions/usage`)
   with "no reading" on every gateway, so a live run never shows your real plan,
   balance or account email.

   Your own `~/.kiro/crew` is never read or written, and an inherited
   `KIROCREW_HOME` is dropped before the gateway starts. The stub screenshots,
   the live screenshots and the walkthrough each get **their own freshly seeded
   gateway**, so the recording never shows a chat a screenshot opened, and no
   still shows what the recording clicked.
2. **Signs a headless Chromium in** with the gateway's one-time token, then runs
   the `setup` calls from `scenes.json` with `fetch()` from inside the page, the
   same way the dashboard calls the app's API. Demo data therefore goes through
   the app's own validation and storage; never hand-write the app's data files.
3. **Takes the screenshots** at 1440x900, device scale 2, in each theme listed
   (the light/dark choice is switched through the dashboard's own
   `PUT /api/config/theme`), and downscales them with ffmpeg to 1600 px wide
   (smaller, or a 256-colour palette, if a PNG would exceed 1.5 MB).
4. **Records the walkthrough** with Playwright's video recorder (signed in
   outside the recording, so no token or login flash is captured), draws a
   visible pointer and optional caption pills, then encodes
   `walkthrough.mp4` (H.264, under 10 MB) and `walkthrough.gif` (palette
   optimized, under 8 MB).
5. **Refuses a bad frame**: it waits until no loading spinner (`.animate-spin`,
   plus any `settle_hidden` selector) is on screen, and if the page shows any
   `forbid_text` (plus built-in ones such as "Something went wrong", "Session
   expired", "Failed to fetch") or a dev error overlay, the run stops instead of
   writing that picture.
6. **Writes `docs/media/media.json`**: every file, its size, dimensions and alt
   text, the app and Kiro Crew commits, how the data was produced, whether the
   scenes ran on a live model, any `data_notes`, and how many seconds of
   waiting the video cut.
7. **Cleans up**: stops the gateway (and, if it does not stop, its process group),
   deletes the temporary home and its own scratch directory, also on Ctrl-C.

## Prerequisites

- A Kiro Crew checkout with its dashboard **built and staged**:

  ```bash
  cd /path/to/KiroCrew/website && npm ci && npm run build && cd ..
  python -m kiro_crew.frontend stage .   # links src/kiro_crew/static/dist
  ```

- A Python that has Kiro Crew's dependencies (the checkout's `.venv`, or pass
  `--python` / set `KIROCREW_PYTHON`).
- For the harness itself (any Python 3.10+):

  ```bash
  python -m pip install playwright
  python -m playwright install chromium
  ```

- `ffmpeg` with `libx264` on `PATH` (or `--ffmpeg` / `FFMPEG`).
- For the live chat scenes: `kiro-cli` installed and already signed in
  (`kiro-cli login`, run by you; the harness never signs in for you).

## Run it

```bash
python capture/capture.py --kirocrew /path/to/KiroCrew
python capture/capture.py --kirocrew /path/to/KiroCrew --only screenshots --shot hero
python capture/capture.py --kirocrew /path/to/KiroCrew --only walkthrough
python capture/capture.py --kirocrew /path/to/KiroCrew --serve    # explore, captures nothing
```

`--serve` boots and seeds, prints a sign-in URL and waits for Enter: use it to
find selectors while writing `scenes.json`. The URL is loopback only and its
token dies with the gateway.

## Which copy of the app is photographed

The gateway serves whatever the checkout's throwaway home has installed. While
an app still ships **built into** Kiro Crew, that is the built-in copy; once it
does not, add `setup` steps that install this repository into the throwaway
home the way the dashboard's **Install from Path** does, grant it trust if it
runs its own code, and enable it. `{{app_root}}` is this repository's root, so
no machine-specific path goes into `scenes.json`:

```json
{"name": "install this repository", "method": "POST", "path": "/api/apps/install",
 "body": {"source": "{{app_root}}"}},
{"name": "trust it (it runs its own code)", "method": "POST",
 "path": "/api/security/trusted-apps/my-app"},
{"name": "turn it on", "method": "POST", "path": "/api/apps/my-app/enable"}
```

`scenes.example.json` carries the same steps under
`setup_when_not_built_in`. Say which copy was photographed in
`"provenance_note"`, which is copied into `media.json`.

## `scenes.json` reference

| Key | Meaning |
|---|---|
| `app` | The app's manifest `name` |
| `fixture` | Kiro Crew seed fixture for the throwaway home (`minimal`, `empty`, `rich`, ...) |
| `locale`, `timezone` | Browser locale and time zone (default `en-US`, `UTC`) |
| `init_storage` | localStorage entries set before any page script runs. `mc-onboarded=1` and `mc-theme` are always set; `"mc-nav": "0"` shows the labelled sidebar |
| `ready` | Default selector that must be visible before a screenshot |
| `forbid_text` | Extra on-screen text that must never be captured (spinners, error notices) |
| `provenance_note` | Free text copied into `media.json` |
| `data_notes` | List of facts about the demo data a reader of `media.json` should know (for example, dates the app stamps itself) |
| `settle_hidden` | Extra selectors that must be gone before a frame is taken (`.animate-spin` always is) |
| `theme_api` | Override for the theme switch call (default `PUT /api/config/theme {"mode": "{{theme}}"}`) |
| `setup` | Ordered HTTP calls: `method`, `path`, `body`, `expect` (status list), `save` (`{"var": "dotted.path.in.response"}`), `optional`, `until` (`{"path": "dotted.path", "equals": value}`: repeat the call every `interval_ms` until that field has that value, within `timeout_ms`); or `{"wait_ms": n}` |
| `screenshots` | List of shots (below) |
| `walkthrough` | One recorded flow (below) |

A **screenshot** has `name` (kebab-case; the file is `<name>-<theme>.png`),
`path`, `alt` (required), `themes` (default both), `ready`, `steps`,
`ready_after` (selector to wait for after the steps), `settle_ms`, `clip`
(selector: capture only that element), `forbid_text`, `settle_hidden` and
`live` (`true`: run against the real model, see above).

The **walkthrough** has `name`, `path`, `theme`, `alt`, `steps`, `tail_ms` and
`live`.

Scripted agent replies are not supported (`stub_replies` is refused): a chat
scene is `"live"`, and its `alt` text, captions and the README describe what
the model actually replied in the run that produced the files. Re-read them
after every live capture.

**Steps** (one verb per object; `{{var}}` is replaced with values saved in
`setup`, and `{{app_root}}` with this repository's root):

| Step | Does |
|---|---|
| `{"goto": "/path"}` | Navigate |
| `{"click": SEL}` / `{"hover": SEL}` | Click / hover (the recording moves the pointer there first) |
| `{"fill": SEL, "value": "..."}` | Set a field at once |
| `{"type": SEL, "value": "...", "delay_ms": 45}` | Type visibly, key by key |
| `{"press": SEL, "key": "Enter"}` | Press a key in an element |
| `{"select": SEL, "value": "..."}` | Choose in a native `<select>` |
| `{"wait_for": SEL}` / `{"wait_hidden": SEL}` | Wait for an element to appear / go (`timeout_ms`, default 15000). Add `"cut": true` to shorten that wait in the recording to `cut_keep_ms` (default 700), e.g. while an agent "thinks" |
| `{"wait_reply": true}` | Wait until the open chat's agent has finished its next turn, read from the gateway's own record of the chat (`timeout_ms`, default 300000). Add `"cut": true` to shorten the wait in the recording |
| `{"scroll_reply": "start"}` | If the newest reply is taller than the screen, scroll back so its start is visible below the pinned prompt (`margin` px, default 12) |
| `{"wait_ms": 800}` | Pause (only to let a viewer read, never as a barrier) |
| `{"scroll": SEL}` | Scroll an element into view |
| `{"caption": "...", "position": "bottom"}` | Show a caption pill (`"top"` or `"bottom"`, `offset` px from that edge); `"over": SEL` centres it on that element, `"above": SEL` sits it `offset` px above that element; `""` hides it |
| `{"mouse_park": [x, y]}` | Move the pointer out of the way |

Selectors are Playwright selectors: `text=...`, `role=radio[name='History']`,
CSS such as `textarea[aria-label='Message input']`.

## Rules for demo data

- **Fictional only**: no real person, account, email, phone number, address or
  store. Use `.example` domains (`https://trailhead-outfitters.example`).
- **Through the app's API only**, so the data is something the app could hold.
- **Realistic**: the data should read like a real user's, so the pictures teach
  what the app is for.

## After a run

Look at every PNG and at three or more frames of the MP4 before committing:

```bash
for t in 2 10 20; do ffmpeg -loglevel error -y -ss $t -i docs/media/walkthrough.mp4 -frames:v 1 /tmp/frame-$t.png; done
```

Redo a capture that is blank, shows an error, a spinner, a login wall, a dev
overlay, a caption covering something the viewer needs, text too small to read,
or real personal data. A live reply is different every run: read what the
agent said in the new files and make the `alt` text, the captions and the
README say only that. Keep captions in one place per kind of screen (bottom of the page; above
the message box in a chat) and hide them while the result the viewer should read
is on screen. Then run `python capture/check_readme.py`.
