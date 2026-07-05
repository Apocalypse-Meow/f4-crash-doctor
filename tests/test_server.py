"""Server-wiring tests: read-only tool registry, prompt, resources, pipeline.

The registry test is the project's hard safety property (design doc section 2):
the set of registered tools must equal the read-only allowlist, so any
accidentally added tool fails the test suite. NOTE: there is no CI yet — this
gate only fires when `uv run pytest` is actually run, so run it before
shipping any change (add CI once the repo lives on a forge).
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

FIXTURES = Path(__file__).parent / "fixtures"

# Sibling modules (crashlog/environment/knowledge) are authored by parallel
# tasks; skip the whole module gracefully if they are not present yet.
pytest.importorskip("f4_crash_doctor.crashlog")
pytest.importorskip("f4_crash_doctor.environment")
pytest.importorskip("f4_crash_doctor.knowledge")

from f4_crash_doctor import knowledge  # noqa: E402
from f4_crash_doctor import nexus  # noqa: E402
from f4_crash_doctor import server  # noqa: E402

BA2_FIXTURE = FIXTURES / "synthetic" / "crash-2026-06-01-ba2limit.log"


def _registered_tool_names() -> set[str]:
    """Enumerate tool names from the FastMCP instance across mcp API variants."""
    tm = getattr(server.mcp, "_tool_manager", None)
    if tm is not None and hasattr(tm, "list_tools"):
        return {t.name for t in tm.list_tools()}
    return {t.name for t in asyncio.run(server.mcp.list_tools())}


def test_tool_registry_equals_read_only_allowlist():
    assert _registered_tool_names() == set(server.READ_ONLY_TOOLS)


def test_no_tool_name_suggests_writing():
    forbidden = ("write", "delete", "move", "install", "download")
    for name in _registered_tool_names():
        for word in forbidden:
            assert word not in name.lower(), f"tool {name!r} contains {word!r}"


def test_prompt_returns_advisory_text():
    text = server.diagnose_fallout4_crash()
    assert isinstance(text, str)
    assert text.strip()
    assert "advisory" in text.lower()


def test_prompt_kit_resource_matches_prompt():
    text = server.prompt_kit_resource()
    assert isinstance(text, str)
    assert text.strip()
    assert text == server.diagnose_fallout4_crash()


@pytest.mark.skipif(
    not (knowledge.DATA_DIR / "f4_profile.json").exists(),
    reason="f4_profile.json authored by a parallel task",
)
def test_profile_resource_is_valid_json():
    text = server.profile_resource()
    assert isinstance(text, str)
    assert text.strip()
    profile = json.loads(text)
    assert isinstance(profile, dict)


@pytest.mark.skipif(
    not (knowledge.DATA_DIR / "signatures.json").exists(),
    reason="signatures.json authored by a parallel task",
)
def test_knowledge_resource_is_valid_json():
    text = server.knowledge_resource()
    assert isinstance(text, str)
    assert text.strip()
    signatures = json.loads(text)
    assert isinstance(signatures, list)


@pytest.mark.skipif(
    not BA2_FIXTURE.exists(), reason="synthetic fixture not yet created"
)
def test_diagnose_crash_end_to_end(tmp_path, monkeypatch):
    if not (knowledge.DATA_DIR / "signatures.json").exists():
        pytest.skip("signatures.json authored by a parallel task")
    if not (knowledge.DATA_DIR / "f4_profile.json").exists():
        pytest.skip("f4_profile.json authored by a parallel task")

    # Point every discovery path at an empty fake tree so the test never
    # touches the real game install (read-only or not).
    fake_root = tmp_path / "game"
    fake_docs = tmp_path / "docs"
    fake_local = tmp_path / "localappdata"
    fake_roaming = tmp_path / "appdata"
    for d in (fake_root, fake_docs, fake_local, fake_roaming):
        d.mkdir()
    monkeypatch.setenv("F4_GAME_ROOT", str(fake_root))
    monkeypatch.setenv("F4_DOCS_DIR", str(fake_docs))
    monkeypatch.setenv("LOCALAPPDATA", str(fake_local))
    monkeypatch.setenv("APPDATA", str(fake_roaming))

    result = server.diagnose_crash(path=str(BA2_FIXTURE))

    assert isinstance(result, dict)
    assert "error" not in result, f"unexpected error: {result.get('error')}"
    assert "findings" in result
    assert "log_summary" in result
    assert isinstance(result["findings"], list)
    assert result["log_summary"]["source_path"] == str(BA2_FIXTURE)
    assert "environment" in result
    assert "load_order" in result
    assert "notes" in result


def test_get_crash_log_refuses_non_crash_log_paths(tmp_path, monkeypatch):
    # README promise: 'It only looks at your crash logs...' — an arbitrary
    # absolute path must be refused, not read.
    monkeypatch.setenv("F4_DOCS_DIR", str(tmp_path / "docs"))
    secret = tmp_path / "anything.txt"
    secret.write_text("SYSTEM SPECS:\n\tprivate stuff\n", encoding="utf-8")

    result = server.get_crash_log(path=str(secret))

    assert "error" in result
    assert "refusing" in result["error"]
    assert "private stuff" not in json.dumps(result)

    result2 = server.diagnose_crash(path=str(secret))
    assert "error" in result2
    assert "refusing" in result2["error"]


def test_get_crash_log_accepts_crash_named_file_anywhere(tmp_path, monkeypatch):
    # A copied/shared log keeps Buffout's crash-*.log naming and must work
    # from any folder (e.g. the user's desktop).
    monkeypatch.setenv("F4_DOCS_DIR", str(tmp_path / "docs"))
    copied = tmp_path / "crash-2026-06-01-copy.log"
    copied.write_text(BA2_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    result = server.get_crash_log(path=str(copied))

    assert "error" not in result
    assert result["game_version"] == "1.11.191"


def test_get_crash_log_accepts_any_name_inside_crash_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("F4_DOCS_DIR", str(tmp_path))
    crash_dir = tmp_path / "My Games" / "Fallout4" / "F4SE" / "Crash Logs"
    crash_dir.mkdir(parents=True)
    renamed = crash_dir / "mylog.txt"
    renamed.write_text(BA2_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    result = server.get_crash_log(path=str(renamed))

    assert "error" not in result
    assert result["game_version"] == "1.11.191"


def test_lookup_mod_without_key_errors_and_makes_no_network_call(monkeypatch):
    monkeypatch.delenv("NEXUS_API_KEY", raising=False)

    def _no_network(*args, **kwargs):
        raise AssertionError("network client constructed without an API key")

    monkeypatch.setattr(nexus.httpx, "Client", _no_network)

    result = server.lookup_mod("fallout4/47359")
    assert isinstance(result, dict)
    assert "error" in result
    assert "NEXUS_API_KEY" in result["error"]
    # The message must tell users the key is optional.
    assert "optional" in result["error"].lower()


def test_check_nexus_auth_without_key_errors(monkeypatch):
    monkeypatch.delenv("NEXUS_API_KEY", raising=False)

    def _no_network(*args, **kwargs):
        raise AssertionError("network client constructed without an API key")

    monkeypatch.setattr(nexus.httpx, "Client", _no_network)

    result = server.check_nexus_auth()
    assert "error" in result
    assert "NEXUS_API_KEY" in result["error"]


def test_parse_mod_ref_accepts_urls_and_ids():
    assert nexus.parse_mod_ref("https://www.nexusmods.com/fallout4/mods/47359") == (
        "fallout4",
        47359,
    )
    assert nexus.parse_mod_ref("fallout4/47359") == ("fallout4", 47359)
    with pytest.raises(ValueError):
        nexus.parse_mod_ref("not a mod reference")
