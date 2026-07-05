"""Smoke test: the staged MCPB bundle's server must actually boot.

Regression guard for the 2026-07-05 field failure: the bundle staged
pyproject.toml into server/ without the README.md it declares, so
hatchling refused to build the package and Claude Desktop killed the
server on first start. Stages the bundle exactly like
packaging/build_mcpb.py, then boots it the way Claude Desktop does
(``uv run --directory <bundle>/server f4-crash-doctor``) and requires
a JSON-RPC initialize response on stdout.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

M0 = Path(__file__).resolve().parents[1]

INITIALIZE = (
    json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "bundle-smoke", "version": "0"},
            },
        }
    )
    + "\n"
)


def _load_build_module():
    spec = importlib.util.spec_from_file_location(
        "build_mcpb", M0 / "packaging" / "build_mcpb.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_staged_bundle_boots(tmp_path, monkeypatch):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv not on PATH")

    build = _load_build_module()
    monkeypatch.setattr(build, "BUNDLE", tmp_path / "bundle")
    build.stage()
    server_dir = tmp_path / "bundle" / "server"

    proc = subprocess.run(
        [uv, "run", "--directory", str(server_dir), "f4-crash-doctor"],
        input=INITIALIZE,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert '"result"' in proc.stdout, (
        "bundle server never answered initialize\n"
        f"exit code: {proc.returncode}\n"
        f"stdout: {proc.stdout!r}\n"
        f"stderr (tail): {proc.stderr[-2000:]}"
    )
