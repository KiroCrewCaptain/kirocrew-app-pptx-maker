"""Small test utilities the Kiro Crew test tree provided to these tests.

``wait_until`` / ``async_wait_until`` / ``await_until`` wait on a signal under a
lost-run bound, never as a sleep barrier. ``make_dir_link`` makes a directory
link that needs no privilege on Windows (a junction). ``requires_symlinks``
skips where a symlink cannot be created.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import pytest

#: The lost-run bound. Generous: a wait returns as soon as its signal arrives.
DEFAULT_TIMEOUT = 30.0
_INTERVAL = 0.01


def wait_until(
    predicate: Callable[[], Any],
    *,
    timeout: float | None = None,
    describe: Callable[[], object] = lambda: "",
) -> Any:
    deadline = time.monotonic() + (timeout or DEFAULT_TIMEOUT)
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"wait_until timed out: {getattr(predicate, '__name__', predicate)} {describe()}"
            )
        time.sleep(_INTERVAL)


async def async_wait_until(
    predicate: Callable[[], Any],
    *,
    timeout: float | None = None,
    describe: Callable[[], object] = lambda: "",
) -> Any:
    deadline = time.monotonic() + (timeout or DEFAULT_TIMEOUT)
    while True:
        value = predicate()
        if inspect.isawaitable(value):
            raise TypeError("async_wait_until takes a plain predicate")
        if value:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"async_wait_until timed out: {getattr(predicate, '__name__', predicate)} {describe()}"
            )
        await asyncio.sleep(_INTERVAL)


async def await_until(signal: Any, *, timeout: float | None = None, what: str = "") -> Any:
    budget = timeout or DEFAULT_TIMEOUT
    if isinstance(signal, asyncio.Event):
        if signal.is_set():
            return True
        waiter = asyncio.ensure_future(signal.wait())
        done, _ = await asyncio.wait({waiter}, timeout=budget)
        if waiter in done:
            return waiter.result()
        waiter.cancel()
        raise AssertionError(f"{what or 'the event'} was not set within {budget}s")
    done, _ = await asyncio.wait({signal}, timeout=budget)
    if signal in done:
        return signal.result()
    raise AssertionError(f"{what or 'the future'} did not finish within {budget}s")


def make_dir_link(link: Path, target: Path) -> None:
    """A reparse point at *link* resolving to the directory *target* (a junction on Windows)."""
    if sys.platform == "win32":
        import _winapi

        getattr(_winapi, "CreateJunction")(str(target), str(link))
        return
    link.symlink_to(target, target_is_directory=True)


def _can_create_symlink() -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "target")
        os.mkdir(target)
        try:
            os.symlink(target, os.path.join(tmp, "link"))
        except (OSError, NotImplementedError, AttributeError):
            return False
        return True


requires_symlinks = pytest.mark.skipif(
    not _can_create_symlink(),
    reason="creating a symlink needs SeCreateSymbolicLinkPrivilege on Windows",
)
