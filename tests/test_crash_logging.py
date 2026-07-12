"""Tests for the crash-logging report in f4_crash_doctor.environment.

UAT finding #1 (2026-07-10): the old `other_crash_loggers` field framed
CrashLoggerAE.dll as a RIVAL of Buffout 4, and the client model turned that
frame into "you have two crash loggers — delete one" advice. In reality both
DLLs ship in the single 'Buffout 4 - Anniversary Edition' archive (Nexus mod
99911): Buffout4AE.dll is the engine-fixes component, CrashLoggerAE.dll is
the crash-log writer. The scan must present recognized pairings as one
package, and reserve warnings for genuinely independent logger combinations
— phrased as confirm-with-the-user questions, never as removal advice.
"""

from f4_crash_doctor import environment
from test_environment import build_fake_game, isolate_env

AE_BUNDLE = ["Buffout4AE.dll", "CrashLoggerAE.dll"]


def report(dlls, variant):
    return environment.crash_logging_report(dlls, variant)


# ---------------------------------------------------------------------------
# recognized bundle: Buffout 4 AE + CrashLoggerAE = one package
# ---------------------------------------------------------------------------


def test_ae_bundle_recognized_as_one_package():
    r = report(AE_BUNDLE + ["f4ee.dll"], "AE")

    assert r["crash_logger_dlls"] == ["CrashLoggerAE.dll"]
    assert r["recognized_bundle"] is not None
    assert "Anniversary Edition" in r["recognized_bundle"]
    joined = " ".join(r["notes"])
    assert "components of ONE mod" in joined
    assert "neither file should be removed" in joined
    assert "not a conflict" in joined.lower()


def test_ae_bundle_identifies_the_log_writer():
    r = report(AE_BUNDLE, "AE")

    joined = " ".join(r["notes"])
    assert "CrashLoggerAE.dll" in joined
    assert "writes" in joined


def test_ae_bundle_detection_is_case_insensitive():
    r = report(["BUFFOUT4AE.DLL", "CRASHLOGGERAE.DLL"], "AE")

    assert r["recognized_bundle"] is not None


# ---------------------------------------------------------------------------
# genuinely independent loggers: confirm with the user, never advise removal
# ---------------------------------------------------------------------------


def test_og_plus_independent_logger_asks_user():
    r = report(["Buffout4.dll", "CrashLogger.dll"], "OG")

    assert r["recognized_bundle"] is None
    assert r["crash_logger_dlls"] == ["CrashLogger.dll"]
    joined = " ".join(r["notes"])
    assert "ask the user" in joined
    assert "CrashLogger.dll" in joined
    # confirm-with-user phrasing only — no removal directives from the scan
    assert "delete" not in joined.lower()


def test_ae_bundle_plus_extra_independent_logger():
    r = report(AE_BUNDLE + ["CrashLogger.dll"], "AE")

    # the bundle is still recognized...
    assert r["recognized_bundle"] is not None
    # ...and only the EXTRA dll is flagged as independent
    ask_note = next(n for n in r["notes"] if "ask the user" in n)
    assert "CrashLogger.dll" in ask_note
    assert "CrashLoggerAE.dll" not in ask_note


# ---------------------------------------------------------------------------
# incomplete / minimal / absent installs
# ---------------------------------------------------------------------------


def test_ae_without_companion_flags_incomplete_install():
    r = report(["Buffout4AE.dll"], "AE")

    assert r["crash_logger_dlls"] == []
    joined = " ".join(r["notes"])
    assert "without its companion" in joined
    assert "CrashLoggerAE.dll" in joined


def test_og_alone_provides_its_own_logging():
    r = report(["Buffout4.dll"], "OG")

    assert r["recognized_bundle"] is None
    joined = " ".join(r["notes"])
    assert "Buffout 4" in joined
    assert all("conflict" not in n.lower() for n in r["notes"])


def test_lone_crash_logger_is_reported_neutrally():
    r = report(["CrashLoggerAE.dll"], None)

    joined = " ".join(r["notes"])
    assert "CrashLoggerAE.dll" in joined
    assert all("conflict" not in n.lower() for n in r["notes"])


def test_no_crash_logging_at_all_is_called_out():
    r = report(["f4ee.dll"], None)

    assert r["crash_logger_dlls"] == []
    assert any("No crash-logging plugin detected" in n for n in r["notes"])


# ---------------------------------------------------------------------------
# scan_environment wiring: crash_logging replaces other_crash_loggers
# ---------------------------------------------------------------------------


def test_scan_environment_reports_crash_logging(tmp_path, monkeypatch):
    isolate_env(monkeypatch, tmp_path)
    root = build_fake_game(tmp_path)  # fake install ships the AE pair

    env = environment.scan_environment(game_root=root, profile={})

    # the rival frame is gone for good
    assert "other_crash_loggers" not in env
    cl = env["crash_logging"]
    assert cl["crash_logger_dlls"] == ["CrashLoggerAE.dll"]
    assert cl["recognized_bundle"] is not None
    assert "Anniversary Edition" in cl["recognized_bundle"]
    assert any("components of ONE mod" in n for n in cl["notes"])
