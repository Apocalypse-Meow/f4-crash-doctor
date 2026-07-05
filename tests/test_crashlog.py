"""Tests for f4_crash_doctor.crashlog: parsing, discovery, malformed input."""

import os
from pathlib import Path

import pytest

from f4_crash_doctor import crashlog

FIXTURES = Path(__file__).parent / "fixtures"
SYNTHETIC = FIXTURES / "synthetic"

BA2LIMIT_LOG = SYNTHETIC / "crash-2026-06-01-ba2limit.log"
NVFLEX_LOG = SYNTHETIC / "crash-2026-06-02-nvflex.log"
STACKOVERFLOW_LOG = SYNTHETIC / "crash-2026-06-03-stackoverflow.log"

ALL_SECTIONS = [
    "SETTINGS",
    "SYSTEM SPECS",
    "PROBABLE CALL STACK",
    "REGISTERS",
    "STACK",
    "MODULES",
    "F4SE PLUGINS",
    "PLUGINS",
]

EXPECTED_KEYS = {
    "game_version",
    "buffout_version",
    "exception",
    "settings",
    "system_specs",
    "call_stack",
    "registers",
    "stack",
    "modules",
    "f4se_plugins",
    "plugins",
    "sections_found",
    "parse_warnings",
}


# ---------------------------------------------------------------- synthetic


@pytest.mark.parametrize("path", [BA2LIMIT_LOG, NVFLEX_LOG, STACKOVERFLOW_LOG])
def test_synthetic_common_shape(path: Path):
    parsed = crashlog.parse_crash_log(path.read_text(encoding="utf-8"))
    assert EXPECTED_KEYS <= set(parsed.keys())
    assert parsed["game_version"] == "1.11.191"
    assert parsed["buffout_version"] == "1.28.6"
    assert parsed["sections_found"] == ALL_SECTIONS
    assert parsed["exception"]["type"] is not None
    assert parsed["exception"]["address"].startswith("0x")
    assert parsed["exception"]["module"] is not None
    assert parsed["settings"]  # populated SETTINGS
    assert parsed["system_specs"]
    assert parsed["registers"]
    assert parsed["stack"]
    assert parsed["modules"]


def test_ba2limit_log():
    parsed = crashlog.parse_crash_log(BA2LIMIT_LOG.read_text(encoding="utf-8"))
    exc = parsed["exception"]
    assert exc["type"] == "EXCEPTION_ACCESS_VIOLATION"
    assert exc["address"] == "0x7FF7A5F0C2A0"
    assert exc["module"] == "Fallout4.exe"
    assert exc["module_offset"] == "174C2A0"

    frames = parsed["call_stack"]
    assert len(frames) == 12
    loose = [f for f in frames if "LooseFileAsyncStream" in f["raw"]]
    assert len(loose) >= 2

    f0 = frames[0]
    assert f0["index"] == 0
    assert isinstance(f0["index"], int)
    assert f0["address"] == "0x7FF7A5F0C2A0"
    assert f0["module"] == "Fallout4.exe"
    assert f0["offset"] == "174C2A0"
    assert f0["symbol"] == "BSResource::LooseFileAsyncStream::DoOpen+0x40"
    # frame 4 has no "-> symbol" part
    f4 = frames[4]
    assert f4["symbol"] is None
    assert f4["module"] == "Fallout4.exe"

    plugins = parsed["plugins"]
    assert len(plugins) >= 17
    assert {"index": "00", "name": "Fallout4.esm", "is_light": False} in plugins
    light = [p for p in plugins if p["index"] == "FE:001"]
    assert light and light[0]["is_light"] is True
    assert all(p["is_light"] for p in plugins if p["index"].startswith("FE:"))

    f4se = parsed["f4se_plugins"]
    assert {"name": "Buffout4.dll", "version": "1.28.6"} in f4se
    assert {"name": "f4ee.dll", "version": "1.0.18"} in f4se

    assert parsed["settings"]["Patches"]["Achievements"] == "true"
    assert "Fallout4.exe" in parsed["modules"]
    assert parsed["parse_warnings"] == []


def test_nvflex_log():
    parsed = crashlog.parse_crash_log(NVFLEX_LOG.read_text(encoding="utf-8"))
    assert parsed["exception"]["type"] == "EXCEPTION_ACCESS_VIOLATION"
    assert parsed["exception"]["module"] == "flexRelease_x64.dll"

    flex = [f for f in parsed["call_stack"] if f["module"] == "flexRelease_x64.dll"]
    assert len(flex) >= 3
    assert all(f["symbol"] is None or isinstance(f["symbol"], str) for f in flex)

    overflow = [p for p in parsed["plugins"] if p["index"] == "FF"]
    assert overflow == [{"index": "FF", "name": "overflow_test.esp", "is_light": False}]


def test_stackoverflow_log():
    parsed = crashlog.parse_crash_log(STACKOVERFLOW_LOG.read_text(encoding="utf-8"))
    exc = parsed["exception"]
    assert exc["type"] == "EXCEPTION_STACK_OVERFLOW"
    assert exc["module"] == "Fallout4.exe"
    assert exc["raw"].startswith('Unhandled exception "EXCEPTION_STACK_OVERFLOW"')
    assert len(parsed["call_stack"]) == 14
    # F4SE plugin listed without a version
    gc = [p for p in parsed["f4se_plugins"] if p["name"] == "GCBugFix.dll"]
    assert gc == [{"name": "GCBugFix.dll", "version": None}]


# ------------------------------------------------------------- malformed


def test_empty_string():
    parsed = crashlog.parse_crash_log("")
    assert EXPECTED_KEYS <= set(parsed.keys())
    assert parsed["game_version"] is None
    assert parsed["buffout_version"] is None
    assert parsed["exception"]["type"] is None
    assert parsed["sections_found"] == []
    assert parsed["call_stack"] == []
    assert parsed["plugins"] == []
    assert parsed["parse_warnings"]  # something was flagged


def test_no_headers_garbage():
    parsed = crashlog.parse_crash_log("this is not a crash log\n\x00\x01\xff binary junk\n")
    assert EXPECTED_KEYS <= set(parsed.keys())
    assert parsed["sections_found"] == []
    assert any("section not found" in w for w in parsed["parse_warnings"])


def test_missing_plugins_section_warns():
    text = BA2LIMIT_LOG.read_text(encoding="utf-8")
    truncated = text[: text.index("PLUGINS:\n\t[00]")]
    parsed = crashlog.parse_crash_log(truncated)
    assert "PLUGINS" not in parsed["sections_found"]
    assert parsed["plugins"] == []
    assert any("PLUGINS" in w and "not found" in w for w in parsed["parse_warnings"])
    # other sections still parsed
    assert parsed["call_stack"]
    assert parsed["f4se_plugins"]


def test_stack_truncated_to_50():
    stack_lines = "\n".join(f"\t[RSP+{i:<4X}] 0x{i:016X}   (void*)" for i in range(200))
    text = (
        "Fallout 4 v1.11.191\n"
        "Buffout 4 v1.28.6\n\n"
        'Unhandled exception "EXCEPTION_ACCESS_VIOLATION" at 0x7FF7A5579300 '
        "Fallout4.exe+0DB9300\n\n"
        "STACK:\n" + stack_lines + "\n"
    )
    parsed = crashlog.parse_crash_log(text)
    assert len(parsed["stack"]) == 50
    assert any("truncated" in w.lower() for w in parsed["parse_warnings"])


def test_never_raises_on_weird_inputs():
    for text in ["\x00" * 512, "PLUGINS:", "SETTINGS:\n===\n[[[", "Unhandled exception"]:
        parsed = crashlog.parse_crash_log(text)
        assert EXPECTED_KEYS <= set(parsed.keys())


# ------------------------------------------------------------- file layer


def test_parse_crash_log_file_adds_metadata(tmp_path: Path):
    p = tmp_path / "crash-2026-06-01-ba2limit.log"
    p.write_text(BA2LIMIT_LOG.read_text(encoding="utf-8"), encoding="utf-8")
    parsed = crashlog.parse_crash_log_file(p)
    assert parsed["source_path"] == str(p)
    assert "T" in parsed["modified_iso"]
    assert parsed["game_version"] == "1.11.191"


def test_parse_crash_log_file_missing_returns_error(tmp_path: Path):
    parsed = crashlog.parse_crash_log_file(tmp_path / "nope.log")
    assert set(parsed.keys()) == {"error"}


# Users frequently re-save logs before sharing them (Notepad 'UTF-8 with
# BOM', old-Notepad 'Unicode' = UTF-16 LE, PowerShell '>' redirection).
# The parser must survive all of these, not just Buffout's own plain UTF-8.


def test_parse_crash_log_file_utf8_bom(tmp_path: Path):
    p = tmp_path / "crash-bom.log"
    p.write_bytes(BA2LIMIT_LOG.read_text(encoding="utf-8").encode("utf-8-sig"))
    parsed = crashlog.parse_crash_log_file(p)
    assert parsed["game_version"] == "1.11.191"
    assert parsed["exception"]["type"] == "EXCEPTION_ACCESS_VIOLATION"
    assert parsed["parse_warnings"] == []


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be"])
def test_parse_crash_log_file_utf16_with_bom(tmp_path: Path, encoding: str):
    text = BA2LIMIT_LOG.read_text(encoding="utf-8")
    bom = b"\xff\xfe" if encoding == "utf-16-le" else b"\xfe\xff"
    p = tmp_path / f"crash-{encoding}.log"
    p.write_bytes(bom + text.encode(encoding))
    parsed = crashlog.parse_crash_log_file(p)
    assert parsed["game_version"] == "1.11.191"
    assert parsed["sections_found"] == ALL_SECTIONS
    assert parsed["parse_warnings"] == []


def test_parse_crash_log_file_utf16_without_bom(tmp_path: Path):
    text = BA2LIMIT_LOG.read_text(encoding="utf-8")
    p = tmp_path / "crash-utf16-nobom.log"
    p.write_bytes(text.encode("utf-16-le"))  # no BOM
    parsed = crashlog.parse_crash_log_file(p)
    assert parsed["game_version"] == "1.11.191"
    assert parsed["sections_found"] == ALL_SECTIONS


# ------------------------------------------------------------- discovery


def test_find_crash_logs_ordering_and_filtering(tmp_path: Path):
    older = tmp_path / "crash-a.log"
    newer = tmp_path / "crash-b.log"
    decoy = tmp_path / "notacrash.log"
    for f in (older, newer, decoy):
        f.write_text("x", encoding="utf-8")
    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_800_000_000, 1_800_000_000))

    found = crashlog.find_crash_logs([tmp_path])
    assert found == [newer, older]

    # missing dirs are skipped, duplicates deduped
    found2 = crashlog.find_crash_logs([tmp_path, tmp_path / "missing", tmp_path])
    assert found2 == [newer, older]


def test_default_crash_dirs_env_override(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("F4_DOCS_DIR", str(tmp_path))
    dirs = crashlog.default_crash_dirs()
    assert len(dirs) == 2
    for d in dirs:
        assert d.is_relative_to(tmp_path)
    assert dirs[0] == tmp_path / "My Games" / "Fallout4" / "F4SE" / "Crash Logs"
    assert dirs[1] == tmp_path / "My Games" / "Fallout4" / "F4SE"


def test_default_crash_dirs_never_raises(monkeypatch):
    monkeypatch.delenv("F4_DOCS_DIR", raising=False)
    dirs = crashlog.default_crash_dirs()
    assert len(dirs) == 2
    assert all(isinstance(d, Path) for d in dirs)


def test_parse_crash_log_file_refuses_oversized(tmp_path):
    """Files beyond MAX_LOG_BYTES are refused with an error dict, unread."""
    from f4_crash_doctor import crashlog

    big = tmp_path / "crash-2026-01-01-huge.log"
    big.write_bytes(b"x" * (crashlog.MAX_LOG_BYTES + 1))
    result = crashlog.parse_crash_log_file(big)
    assert "error" in result
    assert "Refusing" in result["error"]
