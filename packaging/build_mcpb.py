"""Stage and pack the F4 Crash Doctor MCPB bundle.

Usage (from m0/):  uv run python packaging/build_mcpb.py
Output:            m0/dist/f4-crash-doctor.mcpb

Stages manifest + the minimal server payload (pyproject, lock, src/) into
dist/bundle/, then packs it with the official CLI (npx @anthropic-ai/mcpb).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

M0 = Path(__file__).resolve().parent.parent
BUNDLE = M0 / "dist" / "bundle"
OUT = M0 / "dist" / "f4-crash-doctor.mcpb"


def stage() -> None:
    if BUNDLE.exists():
        shutil.rmtree(BUNDLE)
    server = BUNDLE / "server"
    server.mkdir(parents=True)

    shutil.copy2(M0 / "packaging" / "manifest.json", BUNDLE / "manifest.json")
    shutil.copy2(M0 / "pyproject.toml", server / "pyproject.toml")
    # pyproject declares readme = "README.md"; hatchling refuses to build the
    # package unless the file sits next to it inside server/.
    shutil.copy2(M0 / "README.md", server / "README.md")
    shutil.copy2(M0 / "uv.lock", server / "uv.lock")
    shutil.copytree(
        M0 / "src",
        server / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copy2(M0 / "README.md", BUNDLE / "README.md")


def pack() -> int:
    cmd = ["npx", "-y", "@anthropic-ai/mcpb", "pack", str(BUNDLE), str(OUT)]
    print("+", " ".join(cmd))
    return subprocess.call(cmd, shell=(sys.platform == "win32"))


if __name__ == "__main__":
    stage()
    print(f"staged bundle at {BUNDLE}")
    code = pack()
    if code == 0 and OUT.exists():
        print(f"packed: {OUT} ({OUT.stat().st_size:,} bytes)")
    sys.exit(code)
