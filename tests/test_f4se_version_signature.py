"""SIG-F4SE-VERSION must fire when F4SE does not match the game.

Until 2026-08-29 it could not fire at all: it carried no conditions and no
scopes, so the matcher's "documentation-only entries never match" rule skipped
it. That was invisible until a real break exposed it -- a Steam update took the
game to 1.11.240 with F4SE still at 1.11.221, and only the Address Library
finding appeared.

That gap matters because the Address Library is DOWNSTREAM. Update it alone and
F4SE still refuses to load, so the game is exactly as broken and the user has
done everything they were told. The root cause now fires, at a higher severity
so it is presented first.

The version comparison is the delicate part: F4SE reports three components
("1.11.221"), the exe four ("1.11.240.0"). Comparing the raw strings would fire
on every healthy install.
"""

from __future__ import annotations

from f4_crash_doctor import knowledge

SIGS = knowledge.load_signatures()


def env(game: str | None, f4se_ver: str | None, installed: bool = True) -> dict:
    return {
        "game_exe_version": game,
        "f4se": {"installed": installed, "dll_version": f4se_ver},
        "address_library": {"present": True, "versions": ["1.11.221"], "matches_game": True},
        "managers": {},
    }


def ids(environment: dict) -> list[str]:
    return [f["id"] for f in knowledge.match_signatures({}, {}, environment, SIGS)]


def test_fires_on_the_real_break():
    """The exact state produced by the 2026-08-29 Steam update."""
    assert "SIG-F4SE-VERSION" in ids(env("1.11.240.0", "1.11.221"))


def test_silent_when_versions_match_despite_different_component_counts():
    """F4SE says '1.11.221', the exe says '1.11.221.0'. Comparing raw strings
    would fire on every healthy install ever."""
    assert "SIG-F4SE-VERSION" not in ids(env("1.11.221.0", "1.11.221"))


def test_silent_when_f4se_is_not_installed():
    """Not installed is a different problem and a different conversation."""
    assert "SIG-F4SE-VERSION" not in ids(env("1.11.240.0", None, installed=False))


def test_the_installed_guard_is_load_bearing_on_its_own():
    """`installed: False` must silence it even if a version string lingers.

    Added after mutation testing: deleting the `installed` guard passed every
    other test, because the only fixture exercising it also had a null
    dll_version, so the NEXT guard caught it. The state below is defensive
    rather than observed -- today's scanner sets dll_version to None when F4SE
    is absent -- but a guard nothing exercises is a guard that can be deleted by
    accident.
    """
    stale = env("1.11.240.0", "1.11.221", installed=False)
    assert "SIG-F4SE-VERSION" not in ids(stale)


def test_silent_when_a_version_is_unknown():
    """Never guess a mismatch from missing data."""
    assert "SIG-F4SE-VERSION" not in ids(env(None, "1.11.221"))
    assert "SIG-F4SE-VERSION" not in ids(env("1.11.240.0", None))


def test_both_findings_appear_after_a_game_update():
    """A game update breaks both, so both must be reported.

    An earlier version of this test asserted F4SE was presented FIRST, on the
    reasoning that the Address Library is downstream of it. That ordering is not
    guaranteed -- the schema caps severity at 6 and both sit there -- so the
    assertion was removed rather than left to pass by accident.
    """
    broken = env("1.11.240.0", "1.11.221")
    broken["address_library"]["matches_game"] = False
    found = knowledge.match_signatures({}, {}, broken, SIGS)
    order = [f["id"] for f in found]
    assert "SIG-F4SE-VERSION" in order and "SIG-ADDRLIB-MISMATCH" in order
    # Both sit at severity 6 (the schema caps there), so presentation order is
    # NOT guaranteed and must not be relied on. The robust property is that
    # either finding, read alone, sends the user to both components --
    # test_both_fix_texts_send_the_user_to_both_components pins that.


def test_both_fix_texts_send_the_user_to_both_components():
    """The registered efficacy miss. Either finding read alone must still tell
    the user that BOTH F4SE and the Address Library have to match."""
    for sid in ("SIG-F4SE-VERSION", "SIG-ADDRLIB-MISMATCH"):
        sig = next(s for s in SIGS if s["id"] == sid)
        text = (sig["cause"] + " " + sig["fix"]).lower()
        assert "f4se" in text, f"{sid} never mentions F4SE"
        assert "address library" in text, f"{sid} never mentions the Address Library"


def test_f4se_fix_names_where_to_get_it_and_the_downgrade_option():
    sig = next(s for s in SIGS if s["id"] == "SIG-F4SE-VERSION")
    assert "silverlock" in sig["fix"].lower()
    assert "downgrade" in sig["fix"].lower(), (
        "in the days after an update there may be no F4SE build yet; the user "
        "needs to know waiting or downgrading are the options"
    )
