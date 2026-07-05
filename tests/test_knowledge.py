"""Tests for f4_crash_doctor.knowledge: pack schema, matcher semantics, e2e."""

import json
from pathlib import Path

import pytest

from f4_crash_doctor.knowledge import (
    CONDITION_CHECKS,
    DATA_DIR,
    load_profile,
    load_signatures,
    match_signatures,
)

FIXTURES = Path(__file__).parent / "fixtures"
SYNTHETIC = FIXTURES / "synthetic"

REQUIRED_SIG_KEYS = {
    "id", "name", "severity", "confidence", "scopes", "conditions",
    "cause", "fix", "sources",
}
CONFIDENCE_VALUES = {"high", "medium", "low"}

PROFILE_KEYS = {
    "game", "crash_log_dirs", "plugins_txt", "data_dir_relative",
    "known_roots", "version_matrix", "plugin_cap", "esl_slot",
    "frequent_crashers", "known_conflicts", "notes",
}


def make_log(
    exception: dict | None = None,
    call_stack_lines: list[str] | None = None,
    plugins: list[dict] | None = None,
    modules: list[str] | None = None,
    f4se_plugins: list[dict] | None = None,
) -> dict:
    """Minimal parsed-log dict matching the frozen parser schema."""
    exc = {"raw": None, "type": None, "address": None, "module": None}
    exc.update(exception or {})
    return {
        "exception": exc,
        "call_stack": [{"raw": line} for line in (call_stack_lines or [])],
        "plugins": plugins or [],
        "modules": modules or [],
        "f4se_plugins": f4se_plugins or [],
    }


# --- data pack schema -------------------------------------------------------


def test_signatures_schema() -> None:
    sigs = load_signatures()
    assert isinstance(sigs, list) and len(sigs) >= 19
    ids = [s["id"] for s in sigs]
    assert len(ids) == len(set(ids)), "signature ids must be unique"
    for sig in sigs:
        missing = REQUIRED_SIG_KEYS - set(sig)
        assert not missing, f"{sig.get('id')} missing keys: {missing}"
        assert isinstance(sig["severity"], int) and 1 <= sig["severity"] <= 6
        assert sig["confidence"] in CONFIDENCE_VALUES
        assert isinstance(sig["scopes"], list)
        assert isinstance(sig["conditions"], list)
        assert all(isinstance(c, str) for c in sig["conditions"])
        assert isinstance(sig["sources"], list)
        assert sig["cause"].strip() and sig["fix"].strip()


def test_signatures_json_roundtrip() -> None:
    raw = (DATA_DIR / "signatures.json").read_text(encoding="utf-8")
    sigs = json.loads(raw)
    assert json.loads(json.dumps(sigs)) == sigs


def test_signature_conditions_are_known() -> None:
    for sig in load_signatures():
        for name in sig["conditions"]:
            assert name in CONDITION_CHECKS, (
                f"{sig['id']} references unknown condition {name!r}"
            )


def test_profile_loads_with_all_keys() -> None:
    profile = load_profile()
    missing = PROFILE_KEYS - set(profile)
    assert not missing, f"profile missing keys: {missing}"
    assert profile["game"] == "fallout4"
    assert profile["plugin_cap"] == 255
    assert profile["esl_slot"] == "FE"
    games = [row["game"] for row in profile["version_matrix"]]
    assert "1.11.191" in games


# --- matcher unit tests -----------------------------------------------------


def sig(id_: str = "T-1", severity: int = 3, confidence: str = "medium",
        scopes: list | None = None, conditions: list | None = None) -> dict:
    return {
        "id": id_, "name": id_, "severity": severity, "confidence": confidence,
        "scopes": scopes or [], "conditions": conditions or [],
        "cause": "test cause", "fix": "test fix", "sources": [],
    }


def test_any_of_hit() -> None:
    log = make_log(call_stack_lines=["[ 0] 0x1 flexRelease_x64.dll+0AB"])
    sigs = [sig(scopes=[{"scope": "call_stack", "any_of": ["flexRelease_x64.dll"]}])]
    found = match_signatures(log, signatures=sigs)
    assert [f["id"] for f in found] == ["T-1"]


def test_any_of_case_insensitive() -> None:
    log = make_log(call_stack_lines=["[ 0] 0x1 FLEXRELEASE_X64.DLL+0AB"])
    sigs = [sig(scopes=[{"scope": "call_stack", "any_of": ["flexrelease_x64.dll"]}])]
    assert match_signatures(log, signatures=sigs)


def test_all_of_requirement() -> None:
    exc = {"raw": 'Unhandled exception "EXCEPTION_ACCESS_VIOLATION" at 0x7FF7',
           "type": "EXCEPTION_ACCESS_VIOLATION"}
    log = make_log(exception=exc)
    both = sig(scopes=[{"scope": "exception",
                        "all_of": ["EXCEPTION_ACCESS_VIOLATION", "0x7FF7"]}])
    unmet = sig("T-2", scopes=[{"scope": "exception",
                                "all_of": ["EXCEPTION_ACCESS_VIOLATION",
                                           "not-in-there"]}])
    found = match_signatures(log, signatures=[both, unmet])
    assert [f["id"] for f in found] == ["T-1"]


def test_none_of_exclusion() -> None:
    log = make_log(call_stack_lines=["[ 0] 0x1 d3d11.dll+0AB"])
    sigs = [sig(scopes=[{"scope": "call_stack", "any_of": ["d3d11.dll"],
                         "none_of": ["d3d11.dll"]}])]
    assert match_signatures(log, signatures=sigs) == []


def test_min_count_two() -> None:
    sigs = [sig(scopes=[{"scope": "call_stack", "any_of": ["nvwgf2umx.dll"],
                         "min_count": 2}])]
    one = make_log(call_stack_lines=["[ 0] 0x1 nvwgf2umx.dll+0AB"])
    assert match_signatures(one, signatures=sigs) == []
    two = make_log(call_stack_lines=["[ 0] 0x1 nvwgf2umx.dll+0AB",
                                     "[ 1] 0x2 nvwgf2umx.dll+0CD"])
    assert len(match_signatures(two, signatures=sigs)) == 1


def test_condition_address_library_mismatch() -> None:
    sigs = [sig(conditions=["address_library_mismatch"])]
    env = {"address_library": {"matches_game": False}}
    found = match_signatures(make_log(), environment=env, signatures=sigs)
    assert len(found) == 1
    assert match_signatures(
        make_log(), environment={"address_library": {"matches_game": True}},
        signatures=sigs) == []
    assert match_signatures(make_log(), signatures=sigs) == []


def test_condition_plugins_missing_from_data() -> None:
    sigs = [sig(conditions=["plugins_missing_from_data"])]
    lo = {"in_plugins_not_in_data": ["x.esp"]}
    found = match_signatures(make_log(), load_order=lo, signatures=sigs)
    assert len(found) == 1
    assert any("x.esp" in e for e in found[0]["matched_evidence"])
    assert match_signatures(make_log(), load_order={"in_plugins_not_in_data": []},
                            signatures=sigs) == []


def test_condition_plugins_missing_suppressed_under_mo2() -> None:
    # Plan 8.1: under MO2 the on-disk Plugins.txt is not the real load order,
    # so SIG-MISSING-MASTER must not fire from a Plugins.txt/Data mismatch.
    sigs = [sig(conditions=["plugins_missing_from_data"])]
    lo = {"in_plugins_not_in_data": ["x.esp"]}

    env_mo2 = {"managers": {"conclusion": "mo2"}}
    assert match_signatures(make_log(), load_order=lo, environment=env_mo2,
                            signatures=sigs) == []

    env_mo2b = {"managers": {"conclusion": "unknown",
                             "mo2": {"managing_fallout4": True}}}
    assert match_signatures(make_log(), load_order=lo, environment=env_mo2b,
                            signatures=sigs) == []

    lo_unreliable = {"in_plugins_not_in_data": ["x.esp"],
                     "plugins_txt_reliable": False}
    assert match_signatures(make_log(), load_order=lo_unreliable,
                            signatures=sigs) == []

    # non-MO2 managers still fire
    env_vortex = {"managers": {"conclusion": "vortex"}}
    assert len(match_signatures(make_log(), load_order=lo,
                                environment=env_vortex, signatures=sigs)) == 1


def test_condition_plugin_ff_index() -> None:
    sigs = [sig(conditions=["plugin_ff_index"])]
    log = make_log(plugins=[
        {"index": "00", "name": "Fallout4.esm", "is_light": False},
        {"index": "FF", "name": "overflow.esp", "is_light": False},
    ])
    found = match_signatures(log, signatures=sigs)
    assert len(found) == 1
    assert any("overflow.esp" in e for e in found[0]["matched_evidence"])
    no_ff = make_log(plugins=[{"index": "00", "name": "Fallout4.esm",
                               "is_light": False}])
    assert match_signatures(no_ff, signatures=sigs) == []


def test_unknown_condition_skipped_without_exception() -> None:
    sigs = [sig(conditions=["not_a_real_condition"]),
            sig("T-2", scopes=[{"scope": "modules", "any_of": ["a.dll"]}])]
    found = match_signatures(make_log(modules=["a.dll"]), signatures=sigs)
    assert [f["id"] for f in found] == ["T-2"]


def test_documentation_only_never_matches() -> None:
    assert match_signatures(make_log(modules=["anything.dll"]),
                            signatures=[sig()]) == []


def test_shipped_documentation_only_entry_exists_but_never_matches() -> None:
    sigs = load_signatures()
    doc = [s for s in sigs if not s["scopes"] and not s["conditions"]]
    assert any(s["id"] == "SIG-F4SE-VERSION" for s in doc)
    log = make_log(call_stack_lines=["f4se f4se f4se"])
    found = match_signatures(log, signatures=sigs)
    assert "SIG-F4SE-VERSION" not in [f["id"] for f in found]


def test_severity_desc_then_confidence_ordering() -> None:
    sigs = [
        sig("LOW-SEV", severity=2, confidence="high",
            scopes=[{"scope": "modules", "any_of": ["hit.dll"]}]),
        sig("HIGH-SEV-LOWCONF", severity=5, confidence="low",
            scopes=[{"scope": "modules", "any_of": ["hit.dll"]}]),
        sig("HIGH-SEV-HIGHCONF", severity=5, confidence="high",
            scopes=[{"scope": "modules", "any_of": ["hit.dll"]}]),
    ]
    found = match_signatures(make_log(modules=["hit.dll"]), signatures=sigs)
    assert [f["id"] for f in found] == [
        "HIGH-SEV-HIGHCONF", "HIGH-SEV-LOWCONF", "LOW-SEV"]


def test_matched_evidence_contains_hitting_line() -> None:
    line = "[ 3] 0x7FF7 Fallout4.exe+123 -> LooseFileAsyncStream"
    log = make_log(call_stack_lines=["[ 0] 0x1 other.dll+0AB", line])
    sigs = [sig(scopes=[{"scope": "call_stack",
                         "any_of": ["LooseFileAsyncStream"]}])]
    found = match_signatures(log, signatures=sigs)
    assert found[0]["matched_evidence"]
    assert line in found[0]["matched_evidence"]


def test_finding_shape() -> None:
    log = make_log(modules=["hit.dll"])
    found = match_signatures(
        log, signatures=[sig(scopes=[{"scope": "modules", "any_of": ["hit.dll"]}])])
    f = found[0]
    assert set(f) == {"id", "name", "severity", "confidence", "cause", "fix",
                      "matched_evidence", "sources"}


# --- end-to-end over synthetic fixture logs (authored by the crashlog task) --

try:
    from f4_crash_doctor.crashlog import parse_crash_log_file
    HAVE_PARSER = True
except ImportError:
    parse_crash_log_file = None  # type: ignore[assignment]
    HAVE_PARSER = False


def _e2e_ids(fixture: Path) -> list[str]:
    parsed = parse_crash_log_file(fixture)
    assert "error" not in parsed
    return [f["id"] for f in match_signatures(parsed)]


BA2_LOG = SYNTHETIC / "crash-2026-06-01-ba2limit.log"
NVFLEX_LOG = SYNTHETIC / "crash-2026-06-02-nvflex.log"
STACKOVERFLOW_LOG = SYNTHETIC / "crash-2026-06-03-stackoverflow.log"


@pytest.mark.skipif(not HAVE_PARSER, reason="crashlog module not present yet")
@pytest.mark.skipif(not BA2_LOG.exists(), reason="synthetic fixture not present")
def test_e2e_ba2_limit() -> None:
    assert "SIG-BA2-LIMIT" in _e2e_ids(BA2_LOG)


@pytest.mark.skipif(not HAVE_PARSER, reason="crashlog module not present yet")
@pytest.mark.skipif(not NVFLEX_LOG.exists(), reason="synthetic fixture not present")
def test_e2e_nvflex_and_plugin_limit() -> None:
    ids = _e2e_ids(NVFLEX_LOG)
    assert "SIG-WEAPON-DEBRIS-NVFLEX" in ids
    assert "SIG-PLUGIN-LIMIT-FF" in ids


@pytest.mark.skipif(not HAVE_PARSER, reason="crashlog module not present yet")
@pytest.mark.skipif(not STACKOVERFLOW_LOG.exists(),
                    reason="synthetic fixture not present")
def test_e2e_stack_overflow() -> None:
    assert "SIG-STACK-OVERFLOW" in _e2e_ids(STACKOVERFLOW_LOG)
