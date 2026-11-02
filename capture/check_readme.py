#!/usr/bin/env python3
"""Check README.md against the app README standard (see CHECKLIST.md).

Mechanical half of the checklist only: required sections in order, a
"Get started" line under "What it is", no leftover
template placeholders, every image has alt text and exists, media budgets,
no SVG screenshots, and a GIF plus an MP4 walkthrough. Whether the words are
TRUE and the pictures CURRENT still needs a person (CHECKLIST.md says how).

Standard library only. Exit 0 when clean, 1 with one line per problem.

    python capture/check_readme.py            # checks ./README.md
    python capture/check_readme.py path/to/README.md
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

REQUIRED_SECTIONS = (
    "What it is",
    "The problem it solves",
    "Top 3 use cases",
    "Watch it",
    "Install",
    "First run",
    "How to use it",
    "Settings & data",
    "Troubleshooting",
    "Development",
    "License",
)
BUDGETS = {".png": 1_500_000, ".jpg": 1_500_000, ".jpeg": 1_500_000,
           ".gif": 8_000_000, ".mp4": 10_000_000}
MD_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HTML_IMG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
HTML_SOURCE = re.compile(r"<source\b[^>]*srcset=\"([^\"]+)\"", re.IGNORECASE)
ATTR = re.compile(r"(\w+)\s*=\s*\"([^\"]*)\"")
LINK = re.compile(r"\]\(([^)\s]+\.(?:mp4|gif))\)", re.IGNORECASE)


def check(readme: Path) -> list[str]:
    problems: list[str] = []
    text = readme.read_text(encoding="utf-8")
    root = readme.parent
    headings = [h.strip() for h in re.findall(r"^#{2,3}\s+(.+)$", text, re.MULTILINE)]
    positions = []
    for section in REQUIRED_SECTIONS:
        found = [i for i, h in enumerate(headings) if h.startswith(section)]
        if not found:
            problems.append(f"missing section: '## {section}'")
        else:
            positions.append((found[0], section))
    if [sec for _i, sec in sorted(positions)] != [sec for _i, sec in positions]:
        problems.append("required sections are out of order: " + ", ".join(REQUIRED_SECTIONS))
    first = text.find("## What it is")
    problem_at = text.find("## The problem it solves")
    lead = text[first:problem_at] if 0 <= first < problem_at else ""
    if "**Get started:**" not in lead:
        problems.append("'## What it is' must end with a one-line '**Get started:** ...' "
                        "telling a newcomer how to turn the app on")
    use_case_headings = [h for h in headings if re.match(r"^\d\.\s", h)]
    if len(use_case_headings) < 3:
        problems.append("'Top 3 use cases' needs three numbered sub-headings ('### 1. ...')")
    if re.search(r"\{\{[A-Z][A-Z0-9_]*[^}]*\}\}", text):
        problems.append("template placeholders ({{LIKE_THIS}}) are still in the README")
    if "<!-- TODO" in text:
        problems.append("a <!-- TODO --> marker is still in the README")

    images: list[tuple[str, str]] = [(alt, src) for alt, src in MD_IMAGE.findall(text)]
    for tag in HTML_IMG.findall(text):
        attrs = dict(ATTR.findall(tag))
        images.append((attrs.get("alt", ""), attrs.get("src", "")))
    referenced = [src for _alt, src in images] + HTML_SOURCE.findall(text) + LINK.findall(text)
    for alt, src in images:
        if not alt.strip():
            problems.append(f"image without alt text: {src}")
        elif len(alt.strip()) < 15:
            problems.append(f"alt text too short to describe the image: {src} ({alt!r})")
    seen: set[str] = set()
    for src in referenced:
        if re.match(r"^[a-z]+://", src) or src in seen:
            continue
        seen.add(src)
        path = (root / unquote(src.split("#")[0])).resolve()
        if not path.is_file():
            problems.append(f"referenced file does not exist: {src}")
            continue
        suffix = path.suffix.lower()
        if suffix == ".svg" and "media" in path.parts:
            problems.append(f"screenshots must be raster, not SVG: {src}")
        budget = BUDGETS.get(suffix)
        if budget and path.stat().st_size > budget:
            problems.append(f"{src} is {path.stat().st_size} bytes, over the {budget}-byte budget")
    if not any(s.lower().endswith(".gif") for s in referenced):
        problems.append("no walkthrough GIF is shown in the README")
    if not any(s.lower().endswith(".mp4") for s in referenced):
        problems.append("no link to the walkthrough MP4")
    return problems


def main(argv: list[str]) -> int:
    readme = Path(argv[0]) if argv else Path("README.md")
    if not readme.is_file():
        print(f"{readme}: not found")
        return 1
    problems = check(readme)
    for problem in problems:
        print(f"{readme}: {problem}")
    if not problems:
        print(f"{readme}: meets the mechanical README standard")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
