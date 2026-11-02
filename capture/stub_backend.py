#!/usr/bin/env python3
"""The offline model stand-in that ``capture.py`` points a stub gateway at.

It is Kiro Crew's own stub ACP backend (``kiro_crew.testing.fake_acp_backend``),
used for every scene that shows no chat reply (the app's own pages, the
Library). Chat scenes are marked ``"live"`` in ``scenes.json`` and run against
a real model instead; nothing here writes a reply for them.

One addition, for the README capture only: **an instant answer to ``chat``
subcommands** other than ``--list-models``. The gateway runs
``kiro-cli chat --no-interactive ... /usage`` to read the account's credit
balance. The plain stub treats that as an ACP session and waits on stdin, so
the top bar's credit reading spins for minutes. Answering with nothing lets the
gateway settle on "no reading", which the dashboard shows as a dash.

Run only through the launcher ``capture.py`` writes; it needs a Kiro Crew
checkout's ``src`` on ``PYTHONPATH``.
"""

from __future__ import annotations

import sys


def main() -> None:
    args = sys.argv[1:]
    if args[:1] == ["chat"] and args[1:2] != ["--list-models"]:
        return
    from kiro_crew.testing import fake_acp_backend as fake

    fake.main()


if __name__ == "__main__":
    main()
