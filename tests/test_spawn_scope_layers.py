"""PPTX Maker's two spawn sites hand the spawn exactly ONE ``systemd-run --scope`` layer.

The gateway's ``sandboxed_spawn_argv`` applies the cgroup scope as its outermost
layer, so a call site that wraps its result again nests a second
``systemd-run --user --scope``, which newer systemd refuses, and the command never
runs. The other spawn tests mock the subprocess, so only an argv-level check sees
the layer count. The gateway pins its own chokepoint; this pins the app's side:
each site runs exactly the argv the chokepoint returned, adding no layer of its own.

The fake chokepoint wraps with one scope layer, so this runs identically on Linux,
macOS and Windows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from backend import engine, provision

_SYSTEMD_RUN = "/usr/bin/systemd-run"
_SCOPE = [_SYSTEMD_RUN, "--user", "--scope", "--quiet", "--"]


@pytest.fixture
def scoping_chokepoint(fake_host) -> None:
    """A gateway chokepoint that wraps its argv in one cgroup scope, as the real one does."""

    def wrap(argv: list[str], mode: str = "standard", **_kw: Any):
        return [*_SCOPE, *argv], {}, None

    fake_host.table[("kiro_crew.sandbox", "sandboxed_spawn_argv")] = wrap


def _scope_layers(argv: list[str]) -> int:
    return sum(1 for a in argv if a == _SYSTEMD_RUN)


def test_provision_run_hands_one_scope_layer_to_the_spawn(
    scoping_chokepoint, tmp_path: Path
) -> None:
    with mock.patch.object(provision, "run_limited") as run:
        run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        code, _ = provision._run(["/opt/uv", "sync"], cwd=str(tmp_path), timeout=5)
    spawned = run.call_args.args[0]
    assert code == 0
    assert spawned[0] == _SYSTEMD_RUN
    assert _scope_layers(spawned) == 1, spawned
    assert spawned[-2:] == ["/opt/uv", "sync"]


def test_engine_spawn_hands_one_scope_layer_to_the_spawn(
    scoping_chokepoint, tmp_path: Path
) -> None:
    with mock.patch.object(engine, "run_limited") as run:
        run.return_value = mock.Mock(returncode=0, stdout="{}", stderr="")
        result = engine._spawn(["/venv/python", "-c", "1"], cwd=str(tmp_path), timeout=5)
    spawned = run.call_args.args[0]
    assert result.returncode == 0
    assert spawned[0] == _SYSTEMD_RUN
    assert _scope_layers(spawned) == 1, spawned
    assert spawned[-3:] == ["/venv/python", "-c", "1"]
