"""Staleness notes: diagnose_crash must flag a log that contradicts the machine.

Field finding from the 2026-07-05 live validation (design doc section 10):
the pipeline matched planted signatures correctly but never noticed the log
came from game 1.11.191 on a 1.11.221 machine, with a finding-referenced
plugin that is no longer installed. Real users routinely diagnose stale or
foreign logs; these advisory notes keep such findings honestly framed.
"""

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

pytest.importorskip("f4_crash_doctor.knowledge")

from f4_crash_doctor import knowledge  # noqa: E402
from f4_crash_doctor import server  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
NVFLEX_FIXTURE = FIXTURES / "synthetic" / "crash-2026-06-02-nvflex.log"


def _parsed(game_version="1.11.191", plugins=None):
    return {
        "game_version": game_version,
        "plugins": plugins
        if plugins is not None
        else [{"name": "overflow_test.esp", "index": "FF"}],
    }


def _load_order(reliable=True, entries=(), data_plugins=("NAC.esp",)):
    return {
        "plugins_txt_reliable": reliable,
        "entries": [{"name": n, "enabled": True} for n in entries],
        "data_plugins": list(data_plugins),
    }


def _env(game_exe_version="1.11.221.0", conclusion="manual", mo2_managing=False):
    return {
        "game_exe_version": game_exe_version,
        "managers": {
            "conclusion": conclusion,
            "mo2": {"managing_fallout4": mo2_managing},
        },
    }


FF_FINDING = {
    "id": "SIG-PLUGIN-LIMIT-FF",
    "matched_evidence": [
        "Plugin(s) loaded at index FF (past the 255 cap): overflow_test.esp"
    ],
}


# --- game-version note -------------------------------------------------------


def test_version_mismatch_produces_note_with_both_versions():
    notes = knowledge.staleness_notes(
        _parsed(game_version="1.11.191"), _load_order(), _env("1.11.221.0"), []
    )
    assert any("1.11.191" in n and "1.11.221" in n for n in notes)


def test_matching_versions_produce_no_note_despite_zero_suffix():
    notes = knowledge.staleness_notes(
        _parsed(game_version="1.11.221"), _load_order(), _env("1.11.221.0"), []
    )
    assert notes == []


def test_missing_version_on_either_side_is_silent():
    assert (
        knowledge.staleness_notes(
            _parsed(game_version=None), _load_order(), _env("1.11.221.0"), []
        )
        == []
    )
    assert (
        knowledge.staleness_notes(
            _parsed(game_version="1.11.191"), _load_order(), _env(None), []
        )
        == []
    )


# --- finding-referenced-plugin note ------------------------------------------


def test_finding_plugin_absent_from_live_setup_is_noted():
    notes = knowledge.staleness_notes(
        _parsed(game_version=None), _load_order(), _env(None), [FF_FINDING]
    )
    assert any("overflow_test.esp" in n and "no longer" in n for n in notes)


def test_finding_plugin_still_present_is_silent():
    lo = _load_order(data_plugins=("NAC.esp", "Overflow_Test.esp"))
    notes = knowledge.staleness_notes(
        _parsed(game_version=None), lo, _env(None), [FF_FINDING]
    )
    assert notes == []


def test_log_plugins_not_referenced_by_findings_are_ignored():
    parsed = _parsed(
        game_version=None,
        plugins=[{"name": "overflow_test.esp", "index": "FF"},
                 {"name": "gone_but_unrelated.esp", "index": "0A"}],
    )
    lo = _load_order(data_plugins=("NAC.esp", "overflow_test.esp"))
    notes = knowledge.staleness_notes(parsed, lo, _env(None), [FF_FINDING])
    assert notes == []


def test_mo2_suppresses_plugin_absence_note():
    notes = knowledge.staleness_notes(
        _parsed(game_version=None),
        _load_order(),
        _env(None, conclusion="mo2", mo2_managing=True),
        [FF_FINDING],
    )
    assert notes == []


def test_unreliable_plugins_txt_suppresses_plugin_absence_note():
    notes = knowledge.staleness_notes(
        _parsed(game_version=None), _load_order(reliable=False), _env(None), [FF_FINDING]
    )
    assert notes == []


def test_empty_live_listings_are_treated_as_unknown_not_absent():
    notes = knowledge.staleness_notes(
        _parsed(game_version=None),
        _load_order(entries=(), data_plugins=()),
        _env(None),
        [FF_FINDING],
    )
    assert notes == []


# --- wiring through diagnose_crash -------------------------------------------


@pytest.mark.skipif(not NVFLEX_FIXTURE.exists(), reason="fixture missing")
def test_diagnose_crash_surfaces_staleness_notes(monkeypatch):
    monkeypatch.setattr(
        server.environment, "scan_environment", lambda: _env("1.11.221.0")
    )
    monkeypatch.setattr(
        server.environment,
        "get_load_order",
        lambda managers=None: _load_order(data_plugins=("NAC.esp",)),
    )

    result = server.diagnose_crash(path=str(NVFLEX_FIXTURE))

    assert "error" not in result
    joined = "\n".join(result["notes"])
    assert "1.11.191" in joined and "1.11.221" in joined
    assert "overflow_test.esp" in joined
