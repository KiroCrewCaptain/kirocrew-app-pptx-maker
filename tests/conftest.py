"""Test session setup: the app root on ``sys.path``, and a fake gateway.

The gateway loads hook files into a synthetic package named after the app, so
the backend never needs to be on ``sys.path`` there. A plain pytest run has no
such loader, so the app root is put on the path for the test session only.

Every test runs against :class:`fakehost.FakeHost` (the host controls
``backend/_host.py`` resolves) and a bound :class:`fakehost.FakeCtx` whose
``data_dir`` is a fresh temporary directory, so no test reads or writes a real
data home and none needs Kiro Crew installed. ``XDG_CONFIG_HOME`` (where the
engine keeps its config) and the deck-root override are pointed at temporary
directories too, so no test sees the machine's own engine config.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

import pytest

TESTS = Path(__file__).resolve().parent
APP_ROOT = TESTS.parent
for entry in (str(APP_ROOT), str(TESTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from backend import _host, provision, routes  # noqa: E402
from fakehost import FakeCtx, FakeHost  # noqa: E402

#: The real lookup, captured before any test replaces it with the fake host's.
_REAL_HOST_ATTR = _host._host_attr


@pytest.fixture
def real_host_attr():
    """``_host._host_attr`` as shipped, for the tests of the lookup itself."""
    return _REAL_HOST_ATTR


@pytest.fixture(autouse=True)
def fake_host(monkeypatch: pytest.MonkeyPatch) -> FakeHost:
    """The fake gateway every ``_host`` lookup answers from."""
    host = FakeHost()
    monkeypatch.setattr(_host, "_host_attr", host.attr)
    return host


@pytest.fixture(autouse=True)
def app_ctx(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[FakeCtx]:
    """A bound app context whose data dir is a fresh temporary directory."""
    ctx = FakeCtx(data_dir=tmp_path_factory.mktemp("pptx-maker-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg")))
    monkeypatch.delenv("KIROCREW_PPTX_DECK_ROOT", raising=False)
    monkeypatch.delenv("KIROCREW_PPTX_ENGINE_URL", raising=False)
    previous = _host.bound()
    _host.bind(ctx)
    provision.reset_uv_cache()
    routes._encoded_credential_cache.clear()
    yield ctx
    _host.bind(previous)
    provision.reset_uv_cache()
