"""diagnose_crash must still diagnose when there is no crash log.

Found against a live break on 2026-08-29. A Steam update replaced Fallout4.exe
(1.11.221 -> 1.11.240) and left F4SE behind, which is the most common failure in
modded Fallout 4. `diagnose_crash` returned:

    {"error": "no crash logs found", "searched_dirs": [...]}

and nothing else -- while a severity-6, high-confidence finding sat one call away
in the environment scan.

**The absence of a log is not an absence of evidence; here it IS the evidence.**
The crash logger (Buffout 4 / CrashLogger) is itself an F4SE plugin, so when F4SE
will not load it cannot run to record its own failure. An empty crash-log folder
next to a version mismatch is exactly what that looks like.

These tests pin the fallback and, equally, pin that an explicitly refused path
still errors -- the fallback must not become a way around path validation.
"""

from __future__ import annotations

import pytest

from f4_crash_doctor import server

# @mcp.tool() wraps the function; call the underlying one.
diagnose = getattr(server.diagnose_crash, "fn", server.diagnose_crash)


@pytest.fixture
def no_logs(monkeypatch):
    monkeypatch.setattr(server.crashlog, "find_crash_logs", lambda *a, **k: [])


def test_no_log_still_returns_a_diagnosis(no_logs):
    r = diagnose()
    assert "error" not in r, "a missing log must not be reported as a bare error"
    assert r["log_summary"] is None
    assert r["no_crash_log"] == "no crash logs found"
    assert "environment" in r and "findings" in r


def test_no_log_explains_why_the_log_is_missing(no_logs):
    """The reasoning the model needs and cannot infer from an empty folder.

    Asserts the SPECIFIC claim, not just the words. An earlier version checked
    for "f4se" and "plugin" anywhere in the notes and passed even with the
    explanation deleted, because other notes happen to contain both words --
    caught by mutation testing.
    """
    notes = " ".join(diagnose()["notes"]).lower()
    assert "crash logger" in notes, "must name the crash logger specifically"
    assert "record its own failure" in notes, (
        "must state WHY there is no log: the logger is itself an F4SE plugin, "
        "so it cannot run to record its own failure"
    )


def test_no_log_steers_against_a_one_component_fix(no_logs):
    """The registered efficacy miss: a fix naming only the Address Library
    leaves the user exactly where they started, because F4SE itself is stale."""
    notes = " ".join(diagnose()["notes"]).lower()
    if diagnose()["findings"]:
        assert "address library" in notes and "f4se" in notes


def test_environment_findings_survive_the_fallback(no_logs, monkeypatch):
    """A broken environment must produce findings even with no log at all."""
    broken = {
        "game_exe_version": "1.11.240.0",
        "f4se": {"installed": True, "dll_version": "1.11.221"},
        "address_library": {"present": True, "versions": ["1.11.221"], "matches_game": False},
        "managers": {},
    }
    monkeypatch.setattr(server.environment, "scan_environment", lambda: broken)
    monkeypatch.setattr(server.environment, "get_load_order", lambda **k: {})
    ids = [f["id"] for f in diagnose()["findings"]]
    assert "SIG-ADDRLIB-MISMATCH" in ids, f"expected the mismatch finding, got {ids}"


def test_healthy_environment_with_no_log_makes_no_claim(no_logs, monkeypatch):
    """No log and nothing wrong: say so and ask, do not invent a cause."""
    healthy = {
        "game_exe_version": "1.11.240.0",
        "f4se": {"installed": True, "dll_version": "1.11.240"},
        "address_library": {"present": True, "versions": ["1.11.240"], "matches_game": True},
        "managers": {},
    }
    monkeypatch.setattr(server.environment, "scan_environment", lambda: healthy)
    monkeypatch.setattr(server.environment, "get_load_order", lambda **k: {})
    r = diagnose()
    assert r["findings"] == []
    assert "what changed" in " ".join(r["notes"]).lower()


def test_an_explicitly_refused_path_still_errors():
    """The fallback must not become a bypass for path validation."""
    r = diagnose(path=r"C:\Windows\System32\config\SAM")
    assert "error" in r
    assert "refusing to read" in r["error"]
    assert "findings" not in r
