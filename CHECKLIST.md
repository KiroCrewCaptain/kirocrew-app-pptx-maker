# README acceptance checklist

Every Kiro Crew app repository's README must pass this before it is published,
and again whenever the app's screens change. Items marked **(CI)** are also
checked by `python capture/check_readme.py`; the rest need a person.

## 1. The four guidance items, in plain language

- [ ] **What it is**: says what appears in Kiro Crew once the app is on (a page,
      an agent, a job) and what it does, in two to four sentences, and ends with
      a one-line **Get started:** that turns the app on. **(CI: the line is there)**
- [ ] **The problem it solves**: starts from the reader's situation, not from the
      app's features.
- [ ] **Top 3 use cases**: three real situations, each a short scenario plus a
      screenshot of that moment, and each one something a reader can do TODAY
      through the app's own screens (not data only the API can create).
      **(CI: three numbered sub-headings)**
- [ ] **How to use it**: install from the Kiro Crew App Store with the exact UI
      path, what the first run looks like, and the everyday steps as a numbered
      list using the exact button and tab names on screen.
- [ ] A non-developer can follow everything above **Development** without a
      terminal. Jargon, internal names, error codes and commands are confined to
      Development; troubleshooting says what the reader sees and what to do.
- [ ] Every numbered step works as written; a step that depends on a limit says
      so in the step itself, not only in a later FAQ.
- [ ] All required sections are present, in order. **(CI)**
- [ ] No template placeholder or TODO is left. **(CI)**

## 2. Accuracy

- [ ] Every claim was checked against the app's code (manifest, agent spec,
      routes, UI source), not against the old README or the catalog blurb.
- [ ] No feature is claimed that the app does not have; every limit the user
      will hit is stated (what the agent cannot see, what is not automatic, what
      it will never do). Where a test pins a limit, the README may cite it.
- [ ] Install and uninstall steps match the current Kiro Crew UI and CLI
      (`kirocrew app install|enable|disable|uninstall`).
- [ ] Data location, what leaves the machine, and what disable/uninstall keep
      are stated.

## 3. Media present and current

- [ ] Hero image in light and dark (`<picture>` with a dark `<source>`).
- [ ] One screenshot per top use case, plus light and dark of the main page.
- [ ] A walkthrough GIF shown inline and a link to the MP4. **(CI)**
- [ ] Every referenced file exists. **(CI)**
- [ ] All media were produced by `capture/capture.py` from the current app and
      a current Kiro Crew; `docs/media/media.json` names both commits.
- [ ] A person looked at every PNG and at least three frames of the MP4, and
      none is blank, cropped wrong, or shows an error, a loading spinner, a login
      wall, a dev overlay, a debug toolbar, or text too small to read.
- [ ] A provenance line under the recording says how it was made and what it
      does not show, including that agent replies came from a live model and
      will vary (chat scenes are `"live"`; scripted replies are not allowed) and
      that waits were shortened (when a step has `"cut": true`).
- [ ] The README, the alt text and the captions claim only what the live
      agent actually said in the captured files.
- [ ] A recording that claims an agent does something shows it doing it (a
      caption claims only what is on screen), and holds on the result for a few
      seconds at the end.
- [ ] Captions never cover what the viewer needs to read, and sit in one place
      per kind of screen.
- [ ] Stills and the recording agree (the same marks, the same lists), and the
      recording shows no leftovers from earlier runs.

## 4. Budgets and formats

- [ ] Each PNG under 1.5 MB. **(CI)**
- [ ] The GIF under 8 MB. **(CI)**
- [ ] The MP4 under 10 MB, H.264. **(CI: size)**
- [ ] Screenshots are raster (PNG), never SVG. **(CI)**
- [ ] Images are at most 2000 px on their longest edge (capture writes 1600 px).

## 5. Accessibility

- [ ] Every image has alt text that describes what is in the picture, not just
      its file name. **(CI: present and not trivially short)**
- [ ] Link text says where it goes ("Watch the walkthrough as a video"), never
      "click here".
- [ ] Captions in the recording are short enough to read at GIF size.

## 6. Fictional data only

- [ ] No real person, account, email address, phone number, street address,
      order number or store appears in any picture, frame or example.
- [ ] Web addresses in demo data use the reserved `.example` domain.
- [ ] Demo data was seeded through the app's own API (`scenes.json` `setup`),
      never by writing the app's files by hand.
- [ ] Frames were checked for tokens, local paths with a real user name, and
      anything else the flow was not meant to show.
