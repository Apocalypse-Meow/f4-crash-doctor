"""Tests for f4_crash_doctor.environment.

Everything runs against fake trees in tmp_path — explicit paths are passed to
every call so the real game install, real registry, and real Documents folder
are never touched.
"""

from pathlib import Path

import pytest

from f4_crash_doctor import environment

FIXTURES = Path(__file__).parent / "fixtures"
REAL_BUFFOUT_LOG = FIXTURES / "real" / "Buffout4AE.log"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def build_fake_game(tmp_path: Path) -> Path:
    """Create a minimal fake Fallout 4 install and return its root."""
    root = tmp_path / "Fallout 4"
    plugins_dir = root / "Data" / "F4SE" / "Plugins"
    plugins_dir.mkdir(parents=True)

    (root / "Fallout4.exe").touch()  # empty -> no version resource
    (root / "f4se_loader.exe").touch()
    (root / "f4se_1_11_191.dll").touch()

    data = root / "Data"
    for name in ("Fallout4.esm", "DLCCoast.esm", "SomeMod.esp", "LightMod.esl"):
        (data / name).touch()
    for name in ("Fallout4 - Textures1.ba2", "SomeMod - Main.ba2"):
        (data / name).touch()

    (plugins_dir / "Buffout4AE.dll").touch()
    (plugins_dir / "CrashLoggerAE.dll").touch()
    (plugins_dir / "version-1-11-191-0.bin").touch()
    return root


def isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point every env-derived path at tmp_path so nothing real is read."""
    monkeypatch.setenv("F4_DOCS_DIR", str(tmp_path / "docs"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("F4_GAME_ROOT", raising=False)


# ---------------------------------------------------------------------------
# scan_environment
# ---------------------------------------------------------------------------


def test_scan_environment_fake_install(tmp_path, monkeypatch):
    isolate_env(monkeypatch, tmp_path)
    root = build_fake_game(tmp_path)

    env = environment.scan_environment(game_root=root, profile={})

    assert env["game_root"] == str(root)
    # empty exe file has no version resource
    assert env["game_exe_version"] is None

    assert env["f4se"]["installed"] is True
    assert env["f4se"]["loader_present"] is True
    assert env["f4se"]["dll_version"] == "1.11.191"

    assert env["address_library"]["present"] is True
    assert env["address_library"]["versions"] == ["1.11.191"]
    # exe version unknown -> can't say whether the library matches
    assert env["address_library"]["matches_game"] is None

    assert env["buffout4"]["installed"] is True
    assert env["buffout4"]["variant"] == "AE"

    assert env["crash_logging"]["crash_logger_dlls"] == ["CrashLoggerAE.dll"]
    assert "Buffout4AE.dll" in env["f4se_plugin_dlls"]
    assert env["ba2_count"] == 2

    # every promised key is present
    for key in (
        "game_root", "game_exe_version", "f4se", "address_library", "buffout4",
        "crash_logging", "f4se_plugin_dlls", "ba2_count", "documents_dir",
        "crash_log_dirs_checked", "crash_log_count", "managers", "warnings",
    ):
        assert key in env


def test_scan_environment_address_library_mismatch(tmp_path, monkeypatch):
    isolate_env(monkeypatch, tmp_path)
    root = build_fake_game(tmp_path)
    plugins_dir = root / "Data" / "F4SE" / "Plugins"
    (plugins_dir / "version-1-11-191-0.bin").unlink()
    (plugins_dir / "version-1-10-163-0.bin").touch()
    monkeypatch.setattr(environment, "get_exe_version", lambda p: "1.11.191.0")

    env = environment.scan_environment(game_root=root, profile={})

    assert env["game_exe_version"] == "1.11.191.0"
    assert env["address_library"]["versions"] == ["1.10.163"]
    assert env["address_library"]["matches_game"] is False


def test_scan_environment_no_game_root(tmp_path, monkeypatch):
    isolate_env(monkeypatch, tmp_path)
    # profile with a bogus known root, Steam scan will find nothing under tmp env
    env = environment.scan_environment(
        game_root=None, profile={"known_roots": [str(tmp_path / "nowhere")]}
    )
    # may still resolve via the real Steam scan on a dev machine with FO4
    # installed, so only assert the contract: keys exist and nothing raised
    assert "game_root" in env
    assert isinstance(env["warnings"], list)


# ---------------------------------------------------------------------------
# parse_buffout_runtime_log
# ---------------------------------------------------------------------------


def test_parse_buffout_runtime_log_real_format():
    line = "[15:27:04.389] [12564] [I] Buffout4AE v1.7.1.0"
    assert environment.parse_buffout_runtime_log(line) == {
        "name": "Buffout4AE",
        "version": "1.7.1.0",
    }


def test_parse_buffout_runtime_log_no_match():
    assert environment.parse_buffout_runtime_log("nothing to see here") == {}


@pytest.mark.skipif(
    not REAL_BUFFOUT_LOG.exists(), reason="real fixture copied by packaging task"
)
def test_parse_buffout_runtime_log_real_fixture():
    text = REAL_BUFFOUT_LOG.read_text(errors="replace")
    assert environment.parse_buffout_runtime_log(text)["version"] == "1.7.1.0"


# ---------------------------------------------------------------------------
# get_load_order
# ---------------------------------------------------------------------------


def test_get_load_order_cross_check(tmp_path):
    plugins_txt = tmp_path / "Plugins.txt"
    plugins_txt.write_text(
        "# this line is a comment\n"
        "*enabled.esp\n"
        "disabled.esp\n"
        "*missing.esp\n",
        encoding="utf-8",
    )
    data = tmp_path / "Data"
    data.mkdir()
    (data / "Enabled.ESP").touch()  # case differs from Plugins.txt on purpose
    (data / "disabled.esp").touch()
    (data / "extra.esm").touch()

    lo = environment.get_load_order(plugins_txt=plugins_txt, data_dir=data)

    assert lo["plugins_txt_path"] == str(plugins_txt)
    assert lo["enabled_count"] == 2
    assert lo["entries"] == [
        {"name": "enabled.esp", "enabled": True},
        {"name": "disabled.esp", "enabled": False},
        {"name": "missing.esp", "enabled": True},
    ]
    # case-insensitive: Enabled.ESP satisfies *enabled.esp
    assert lo["in_plugins_not_in_data"] == ["missing.esp"]
    assert "extra.esm" in lo["in_data_not_in_plugins"]
    assert "Enabled.ESP" not in lo["in_data_not_in_plugins"]
    assert lo["counts_by_ext"] == {"esm": 1, "esp": 2, "esl": 0}


def test_get_load_order_missing_plugins_txt(tmp_path):
    data = tmp_path / "Data"
    data.mkdir()
    (data / "orphan.esp").touch()

    lo = environment.get_load_order(
        plugins_txt=tmp_path / "no_such_dir" / "Plugins.txt", data_dir=data
    )

    assert lo["plugins_txt_path"] is None
    assert lo["entries"] == []
    assert lo["enabled_count"] == 0
    assert lo["warnings"]
    # one side missing -> both cross-check lists empty
    assert lo["in_plugins_not_in_data"] == []
    assert lo["in_data_not_in_plugins"] == []


def test_get_load_order_missing_data_dir(tmp_path):
    plugins_txt = tmp_path / "Plugins.txt"
    plugins_txt.write_text("*enabled.esp\n", encoding="utf-8")

    lo = environment.get_load_order(
        plugins_txt=plugins_txt, data_dir=tmp_path / "no_such_data"
    )

    assert lo["data_dir"] is None
    assert lo["data_plugins"] == []
    assert lo["warnings"]
    assert lo["in_plugins_not_in_data"] == []
    assert lo["in_data_not_in_plugins"] == []


def test_get_load_order_exempts_implicit_plugins(tmp_path):
    # Fallout4.esm, DLC ESMs and Creation Club content are loaded implicitly
    # and NEVER listed in Plugins.txt -> not a mismatch on a healthy install.
    plugins_txt = tmp_path / "Plugins.txt"
    plugins_txt.write_text("*SomeMod.esp\n", encoding="utf-8")
    data = tmp_path / "Data"
    data.mkdir()
    for name in (
        "Fallout4.esm",
        "DLCCoast.esm",
        "DLCNukaWorld.esm",
        "ccBGSFO4044-HellfirePowerArmor.esl",
        "SomeMod.esp",
        "orphan.esp",
    ):
        (data / name).touch()
    # a CC plugin known only via Fallout4.ccc (weird name, no cc*fo4* pattern)
    (data / "oddball.esl").touch()
    (tmp_path / "Fallout4.ccc").write_text("oddball.esl\n", encoding="utf-8")

    lo = environment.get_load_order(plugins_txt=plugins_txt, data_dir=data)

    assert lo["in_data_not_in_plugins"] == ["orphan.esp"]
    assert set(lo["implicit_plugins_in_data"]) == {
        "Fallout4.esm",
        "DLCCoast.esm",
        "DLCNukaWorld.esm",
        "ccBGSFO4044-HellfirePowerArmor.esl",
        "oddball.esl",
    }
    assert lo["in_plugins_not_in_data"] == []


def test_get_load_order_mo2_skips_cross_check(tmp_path):
    # Under MO2 the on-disk Plugins.txt is stale/virtualized: no missing-
    # master or extra-file conclusions may be drawn from it (plan 8.1).
    plugins_txt = tmp_path / "Plugins.txt"
    plugins_txt.write_text("*ghost.esp\n", encoding="utf-8")
    data = tmp_path / "Data"
    data.mkdir()
    (data / "real.esp").touch()

    lo = environment.get_load_order(
        plugins_txt=plugins_txt,
        data_dir=data,
        managers={"conclusion": "mo2", "mo2": {"managing_fallout4": True}},
    )

    assert lo["plugins_txt_reliable"] is False
    assert lo["in_plugins_not_in_data"] == []
    assert lo["in_data_not_in_plugins"] == []
    assert any("Mod Organizer 2" in w for w in lo["warnings"])
    # entries are still reported (as data, just not trusted for cross-checks)
    assert lo["entries"] == [{"name": "ghost.esp", "enabled": True}]


def test_get_load_order_non_mo2_managers_keeps_cross_check(tmp_path):
    plugins_txt = tmp_path / "Plugins.txt"
    plugins_txt.write_text("*missing.esp\n", encoding="utf-8")
    data = tmp_path / "Data"
    data.mkdir()

    lo = environment.get_load_order(
        plugins_txt=plugins_txt, data_dir=data, managers={"conclusion": "vortex"}
    )

    assert lo["plugins_txt_reliable"] is True
    assert lo["in_plugins_not_in_data"] == ["missing.esp"]


def test_get_load_order_ansi_encoded_plugins_txt(tmp_path):
    # The FO4 launcher writes Plugins.txt in the ANSI codepage; non-ASCII
    # names must still match correctly-decoded filesystem names.
    name = "Schönheit.esp"
    plugins_txt = tmp_path / "Plugins.txt"
    plugins_txt.write_bytes(f"*{name}\n".encode("cp1252"))
    data = tmp_path / "Data"
    data.mkdir()
    (data / name).touch()

    lo = environment.get_load_order(plugins_txt=plugins_txt, data_dir=data)

    assert lo["entries"] == [{"name": name, "enabled": True}]
    assert lo["in_plugins_not_in_data"] == []


def test_get_load_order_utf8_bom_plugins_txt(tmp_path):
    plugins_txt = tmp_path / "Plugins.txt"
    plugins_txt.write_bytes("*first.esp\n".encode("utf-8-sig"))
    data = tmp_path / "Data"
    data.mkdir()
    (data / "first.esp").touch()

    lo = environment.get_load_order(plugins_txt=plugins_txt, data_dir=data)

    assert lo["entries"] == [{"name": "first.esp", "enabled": True}]
    assert lo["in_plugins_not_in_data"] == []


# ---------------------------------------------------------------------------
# _documents_dir consistency
# ---------------------------------------------------------------------------


def test_documents_dir_matches_crashlog_module(tmp_path, monkeypatch):
    """environment and crashlog must resolve the SAME Documents folder —
    a GUID mismatch here once sent them to different places (OneDrive bug)."""
    from f4_crash_doctor import crashlog

    # with the override
    monkeypatch.setenv("F4_DOCS_DIR", str(tmp_path / "docs"))
    assert environment._documents_dir() == crashlog._documents_dir()

    # and via the real Windows known-folder API (read-only)
    monkeypatch.delenv("F4_DOCS_DIR", raising=False)
    assert environment._documents_dir() == crashlog._documents_dir()


# ---------------------------------------------------------------------------
# detect_managers
# ---------------------------------------------------------------------------


def _dirs(tmp_path: Path) -> tuple[Path, Path]:
    la = tmp_path / "localappdata"
    ad = tmp_path / "appdata"
    la.mkdir(exist_ok=True)
    ad.mkdir(exist_ok=True)
    return la, ad


def _make_mo2_instance(localappdata: Path, name: str, game_name: str) -> Path:
    """Create an MO2 instance-registry entry with a ModOrganizer.ini."""
    instance = localappdata / "ModOrganizer" / name
    instance.mkdir(parents=True)
    ini = instance / "ModOrganizer.ini"
    ini.write_text(f"[General]\ngameName={game_name}\n", encoding="utf-8")
    return ini


def test_detect_managers_mo2(tmp_path):
    la, ad = _dirs(tmp_path)
    ini = _make_mo2_instance(la, "Fallout 4", "Fallout 4")

    result = environment.detect_managers(localappdata=la, appdata=ad)

    assert result["mo2"]["detected"] is True
    assert result["mo2"]["managing_fallout4"] is True
    assert str(ini) in result["mo2"]["fallout4_evidence"]
    assert result["conclusion"] == "mo2"


def test_detect_managers_mo2_other_game_only_is_not_mo2(tmp_path):
    # MO2 installed for Skyrim only -> FO4 is not MO2-managed
    la, ad = _dirs(tmp_path)
    _make_mo2_instance(la, "Skyrim SE", "Skyrim Special Edition")
    root = build_fake_game(tmp_path)

    result = environment.detect_managers(game_root=root, localappdata=la, appdata=ad)

    assert result["mo2"]["detected"] is True  # something MO2-ish exists...
    assert result["mo2"]["managing_fallout4"] is False  # ...but not for FO4
    assert result["conclusion"] == "manual"
    assert result["confidence"] == "low"


def test_detect_managers_vortex_beats_leftover_mo2_dir(tmp_path):
    # Plan 8.1: per-game Vortex deployment markers must outrank a
    # game-agnostic (or other-game) ModOrganizer directory.
    la, ad = _dirs(tmp_path)
    _make_mo2_instance(la, "Skyrim SE", "Skyrim Special Edition")
    root = build_fake_game(tmp_path)
    (root / "Data" / "vortex.deployment.json").touch()

    result = environment.detect_managers(game_root=root, localappdata=la, appdata=ad)

    assert result["vortex"]["managing_fallout4"] is True
    assert result["conclusion"] == "vortex"
    assert result["notes"]  # explains why Vortex won


def test_detect_managers_empty_leftover_mo2_dir_plus_vortex(tmp_path):
    # A bare leftover %LOCALAPPDATA%\ModOrganizer (no instances) must not
    # outrank concrete Vortex evidence in this game's Data folder.
    la, ad = _dirs(tmp_path)
    (la / "ModOrganizer").mkdir()
    root = build_fake_game(tmp_path)
    (root / "Data" / "__folder_managed_by_vortex").touch()

    result = environment.detect_managers(game_root=root, localappdata=la, appdata=ad)

    assert result["conclusion"] == "vortex"


def test_detect_managers_both_fo4_managers_is_unknown(tmp_path):
    # Both an MO2 FO4 instance and Vortex deployment markers: ambiguous —
    # the model should ask the user, not guess.
    la, ad = _dirs(tmp_path)
    _make_mo2_instance(la, "Fallout 4", "Fallout 4")
    root = build_fake_game(tmp_path)
    (root / "Data" / "vortex.deployment.json").touch()

    result = environment.detect_managers(game_root=root, localappdata=la, appdata=ad)

    assert result["conclusion"] == "unknown"
    assert result["confidence"] == "low"
    assert result["notes"]


def test_detect_managers_portable_mo2_next_to_game(tmp_path):
    la, ad = _dirs(tmp_path)
    root = build_fake_game(tmp_path)
    (root / "ModOrganizer.ini").write_text(
        "[General]\ngameName=Fallout 4\n", encoding="utf-8"
    )

    result = environment.detect_managers(game_root=root, localappdata=la, appdata=ad)

    assert result["mo2"]["managing_fallout4"] is True
    assert result["conclusion"] == "mo2"


def test_detect_managers_manual_is_low_confidence(tmp_path):
    # 'manual' is absence-of-evidence (portable MO2 is invisible) -> the
    # result must carry low confidence so the prompt kit asks the user first.
    la, ad = _dirs(tmp_path)
    root = build_fake_game(tmp_path)

    result = environment.detect_managers(game_root=root, localappdata=la, appdata=ad)

    assert result["conclusion"] == "manual"
    assert result["confidence"] == "low"
    assert result["notes"]


def test_detect_managers_vortex_managing(tmp_path):
    la, ad = _dirs(tmp_path)
    (ad / "Vortex" / "fallout4").mkdir(parents=True)

    result = environment.detect_managers(localappdata=la, appdata=ad)

    assert result["vortex"]["installed"] is True
    assert result["vortex"]["managing_fallout4"] is True
    assert result["conclusion"] == "vortex"


def test_detect_managers_vortex_installed_not_managing(tmp_path):
    # this is the shape of Alex's real machine: Vortex present but Fallout 4
    # is modded manually
    la, ad = _dirs(tmp_path)
    (ad / "Vortex").mkdir()
    root = build_fake_game(tmp_path)

    result = environment.detect_managers(game_root=root, localappdata=la, appdata=ad)

    assert result["vortex"]["installed"] is True
    assert result["vortex"]["managing_fallout4"] is False
    assert result["mo2"]["detected"] is False
    assert result["conclusion"] == "manual"


def test_detect_managers_unknown(tmp_path):
    la, ad = _dirs(tmp_path)

    result = environment.detect_managers(game_root=None, localappdata=la, appdata=ad)

    assert result["mo2"]["detected"] is False
    assert result["vortex"]["installed"] is False
    assert result["conclusion"] == "unknown"


def test_detect_managers_vortex_deployment_marker(tmp_path):
    la, ad = _dirs(tmp_path)
    root = build_fake_game(tmp_path)
    (root / "Data" / "__folder_managed_by_vortex").touch()

    result = environment.detect_managers(game_root=root, localappdata=la, appdata=ad)

    assert result["vortex"]["managing_fallout4"] is True
    assert result["conclusion"] == "vortex"


# ---------------------------------------------------------------------------
# find_game_root
# ---------------------------------------------------------------------------


def test_find_game_root_env_var(tmp_path, monkeypatch):
    root = build_fake_game(tmp_path)
    monkeypatch.setenv("F4_GAME_ROOT", str(root))

    assert environment.find_game_root() == root


def test_find_game_root_profile_known_roots(tmp_path, monkeypatch):
    root = build_fake_game(tmp_path)
    monkeypatch.delenv("F4_GAME_ROOT", raising=False)

    assert environment.find_game_root({"known_roots": [str(root)]}) == root
