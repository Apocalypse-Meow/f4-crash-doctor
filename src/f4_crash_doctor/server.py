"""F4 Crash Doctor — advisory-only Fallout 4 crash diagnosis MCP server.

Seven READ-ONLY tools: find and parse Buffout 4 crash logs, read the live
load order, scan the game environment (versions, F4SE, mod managers), match
crash signatures from the bundled knowledge pack, and look up mods on Nexus.

Hard safety property: this server exposes ZERO tools that write, move,
delete, or modify any file. Every tool only reads from disk (or the Nexus
API). tests/test_server.py asserts the registered tool set equals
READ_ONLY_TOOLS, so an accidental new tool fails the test suite — run
`uv run pytest` before shipping any change (there is no CI yet, so the
guarantee is only as good as that habit).

Error convention: expected failures return {"error": msg} dicts; tools never
raise to the client.
"""

from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from f4_crash_doctor import crashlog, environment, knowledge, nexus

mcp = FastMCP("f4-crash-doctor")

# The complete, exhaustive tool surface. If a tool is not in this list it
# must not exist; test_server.py enforces this (advisory-only guarantee).
READ_ONLY_TOOLS = [
    "list_crash_logs",
    "get_crash_log",
    "get_load_order",
    "scan_game_environment",
    "diagnose_crash",
    "lookup_mod",
    "check_nexus_auth",
]


def _error(e: Exception) -> dict:
    """Convert an unexpected exception into the standard error dict."""
    return {"error": f"{type(e).__name__}: {e}"}


def _is_allowed_log_path(p: Path) -> bool:
    """Confine explicit paths to crash logs (README: 'It only looks at your
    crash logs...'). Allowed: anything inside a default crash-log directory,
    or a file whose name matches Buffout's crash-*.log naming (so users can
    point at a copied/shared log). Everything else is refused."""
    if fnmatch(p.name.lower(), "crash-*.log"):
        return True
    try:
        resolved = p.resolve()
    except OSError:
        return False
    for d in crashlog.default_crash_dirs():
        try:
            if resolved.is_relative_to(d.resolve()):
                return True
        except OSError:
            continue
    return False


def _resolve_log_path(path: str | None) -> tuple[Path | None, dict | None]:
    """Resolve an explicit path or the newest crash log.

    Returns (path, None) on success or (None, error_dict) when no log can
    be found or the path is not a crash log.
    """
    if path is not None:
        p = Path(path)
        if not _is_allowed_log_path(p):
            return None, {
                "error": (
                    f"refusing to read {p}: this tool only reads Buffout 4 "
                    "crash logs (files named crash-*.log, or files inside "
                    "the crash-log folders). Use list_crash_logs to see "
                    "valid paths."
                ),
                "allowed_dirs": [str(d) for d in crashlog.default_crash_dirs()],
            }
        return p, None
    logs = crashlog.find_crash_logs()
    if not logs:
        return None, {
            "error": "no crash logs found",
            "searched_dirs": [str(d) for d in crashlog.default_crash_dirs()],
        }
    return logs[0], None


@mcp.tool()
def list_crash_logs(limit: int = 10) -> dict:
    """List Buffout 4 crash logs on this machine, newest first.

    Searches the standard Fallout 4 crash-log folders under the user's
    Documents directory (override with the F4_DOCS_DIR environment variable).
    Returns up to `limit` entries with absolute path, last-modified timestamp
    (ISO 8601) and size in KB, plus the directories that were searched.
    Read-only: nothing is opened or modified.
    """
    try:
        searched = [str(d) for d in crashlog.default_crash_dirs()]
        paths = crashlog.find_crash_logs()
        logs = []
        for p in paths[: max(limit, 0)]:
            try:
                st = p.stat()
                logs.append(
                    {
                        "path": str(p),
                        "modified_iso": datetime.fromtimestamp(st.st_mtime).isoformat(
                            timespec="seconds"
                        ),
                        "size_kb": round(st.st_size / 1024, 1),
                    }
                )
            except OSError:
                continue
        return {"logs": logs, "searched_dirs": searched, "count": len(logs)}
    except Exception as e:
        return _error(e)


@mcp.tool()
def get_crash_log(path: str | None = None) -> dict:
    """Parse a Buffout 4 crash log into structured sections.

    With no `path`, parses the NEWEST crash log found in the standard
    folders; pass an absolute path (e.g. from list_crash_logs) to parse a
    specific one. Explicit paths must be crash logs: either inside the
    standard crash-log folders or named crash-*.log — anything else is
    refused with an error. Returns the exception (type, address, faulting module),
    probable call stack, F4SE plugin list, plugin load order as logged,
    modules, settings, and any parse warnings. The raw STACK dump is
    truncated to 50 lines to keep responses small. Returns
    {"error": "no crash logs found", "searched_dirs": [...]} when there is
    nothing to parse.
    """
    try:
        target, err = _resolve_log_path(path)
        if err is not None:
            return err
        return crashlog.parse_crash_log_file(target)
    except Exception as e:
        return _error(e)


@mcp.tool()
def get_load_order() -> dict:
    """Read the live Fallout 4 load order and cross-check it against Data.

    Parses %LOCALAPPDATA%\\Fallout4\\Plugins.txt ('*' prefix = enabled) and
    lists the .esm/.esp/.esl files actually present in the game's Data
    folder. Reports both mismatch directions: enabled plugins whose file is
    missing from Data (a classic missing-master crash cause) and files in
    Data that are not in the load order. Implicitly loaded plugins
    (Fallout4.esm, official DLC, Creation Club content) are never listed in
    Plugins.txt and are excluded from the mismatch lists. When Mod Organizer
    2 manages the game, the on-disk Plugins.txt is NOT the real load order
    (MO2 virtualizes it): plugins_txt_reliable is false, the cross-check is
    skipped, and a warning explains why. Read-only.
    """
    try:
        return environment.get_load_order()
    except Exception as e:
        return _error(e)


@mcp.tool()
def scan_game_environment() -> dict:
    """Scan the installed Fallout 4 for versions, F4SE health, and mod managers.

    Discovers the game root (F4_GAME_ROOT env var, known locations, then all
    Steam libraries), then reports: game exe version, F4SE loader/DLL
    version, Address Library presence and whether it matches the game
    version, Buffout 4 variant + version, a crash_logging report (which DLLs
    write crash logs; a recognized_bundle means those DLLs ship together as
    ONE mod — e.g. Buffout 4 AE + CrashLoggerAE — and are NOT rival loggers;
    trust its notes over pattern-matched folklore), all F4SE plugin DLLs,
    BA2 archive count, crash-log folders, and which mod manager is in use
    (MO2 / Vortex / manual / unknown). Call this FIRST in any diagnosis
    session — version mismatches here explain many crashes before the log
    is even read. Read-only.
    """
    try:
        return environment.scan_environment()
    except Exception as e:
        return _error(e)


def _environment_only(err: dict) -> dict:
    """Diagnose from the environment when there is no crash log to read.

    Returns the SAME shape as a full diagnosis so callers do not branch: a
    `log_summary` of None, the findings the environment alone supports, and
    notes explaining why the absence of a log is itself diagnostic rather than
    an obstacle.
    """
    env = environment.scan_environment()
    lo = environment.get_load_order(managers=env.get("managers"))
    findings = knowledge.match_signatures({}, lo, env)

    notes = [
        "NO CRASH LOG WAS FOUND, and that is not necessarily a problem to solve "
        "before helping. The findings below come from the installed files alone.",
        "If F4SE is not loading, there will never be a crash log: the crash "
        "logger (Buffout 4 / CrashLogger) is itself an F4SE plugin, so it cannot "
        "run to record its own failure. An empty crash-log folder alongside a "
        "version mismatch below is consistent with that, not evidence against it.",
    ]
    notes += knowledge.staleness_notes({}, lo, env, findings)
    if not findings:
        notes.append(
            "No environment signature matched either. Ask the user what changed "
            "recently -- a game update, a new mod, a mod manager change -- and "
            "whether the game starts at all, crashes on launch, or crashes in "
            "play. Those three cases have different causes."
        )
    else:
        notes.append(
            "Read the findings as the environment's own account of what is wrong. "
            "Check the versions reported below against each other before "
            "recommending anything: the game exe, the F4SE loader and the Address "
            "Library must all match, and a fix naming only one of them can leave "
            "the user exactly where they started."
        )

    return {
        "log_summary": None,
        "no_crash_log": err.get("error"),
        "searched_dirs": err.get("searched_dirs", []),
        "findings": findings,
        "environment": env,
        "load_order": lo,
        "notes": notes,
    }


@mcp.tool()
def diagnose_crash(path: str | None = None) -> dict:
    """One-shot crash diagnosis: parse log + scan environment + match signatures.

    Runs the full advisory pipeline: parses the given crash log (or the
    newest one when `path` is omitted), scans the game environment, reads
    the live load order, and matches everything against the bundled crash
    signature knowledge pack. Returns a compact log summary, ranked findings
    (severity then confidence, each with cause, suggested user fix, and the
    matched evidence lines to verify against), plus the full environment and
    load-order dicts. Findings are ADVISORY ONLY — this server never changes
    any file; fixes are actions for the user to take themselves.
    """
    try:
        target, err = _resolve_log_path(path)
        if err is not None:
            # An explicitly refused path is a caller error: return it.
            if path is not None:
                return err
            # "No crash logs found" is NOT a dead end, and treating it as one
            # was a real defect. The most common breakage in modded Fallout 4 --
            # a game update that leaves F4SE behind -- produces NO crash log at
            # all, because the crash logger is itself an F4SE plugin and cannot
            # run to record its own failure. The environment scan diagnoses that
            # case completely on its own. Verified 2026-08-29 against a live
            # break: this tool returned a bare error while a severity-6,
            # high-confidence finding sat one call away.
            return _environment_only(err)
        parsed = crashlog.parse_crash_log_file(target)
        if "error" in parsed and "exception" not in parsed:
            return parsed

        env = environment.scan_environment()
        # Reuse the scan's manager detection so the load-order cross-check
        # knows when Plugins.txt is unreliable (MO2's virtual file system).
        lo = environment.get_load_order(managers=env.get("managers"))
        findings = knowledge.match_signatures(parsed, lo, env)

        notes: list[str] = knowledge.staleness_notes(parsed, lo, env, findings)
        if not findings:
            notes.append(
                "no signatures matched this crash log — that does not mean the "
                "crash is unexplainable. Reason from the exception type, the top "
                "of the probable call stack, and any mods the user installed or "
                "updated recently; ask the user what changed before the crashes "
                "started."
            )

        return {
            "log_summary": {
                "source_path": parsed.get("source_path"),
                "game_version": parsed.get("game_version"),
                "buffout_version": parsed.get("buffout_version"),
                "exception": parsed.get("exception"),
                "parse_warnings": parsed.get("parse_warnings", []),
            },
            "findings": findings,
            "environment": env,
            "load_order": lo,
            "notes": notes,
        }
    except Exception as e:
        return _error(e)


@mcp.tool()
def lookup_mod(url_or_id: str) -> dict:
    """Look up a mod on Nexus Mods (read-only; requires NEXUS_API_KEY).

    Accepts a Nexus URL (https://www.nexusmods.com/fallout4/mods/47359) or
    'game/mod_id' (e.g. 'fallout4/47359'). Returns mod metadata (name,
    version, author, summary) and the available files with versions and
    upload dates — useful for checking whether a suspect mod has a newer
    version or a compatibility patch. Never downloads anything. Without
    NEXUS_API_KEY this returns an error explaining that mod lookups are
    optional; every other tool still works.
    """
    try:
        return nexus.inspect_mod(url_or_id)
    except Exception as e:
        return _error(e)


@mcp.tool()
def check_nexus_auth() -> dict:
    """Verify the Nexus API key works and report account status.

    Returns the account name, premium/supporter flags, and user id. Use this
    to troubleshoot lookup_mod failures. NEXUS_API_KEY is optional — only
    the two Nexus lookup tools need it.
    """
    try:
        return nexus.check_auth()
    except Exception as e:
        return _error(e)


@mcp.prompt(name="diagnose-fallout4-crash")
def diagnose_fallout4_crash() -> str:
    """Diagnosis workflow and advisory-only phrasing rules for Fallout 4 crashes."""
    return (knowledge.DATA_DIR / "prompt_kit.md").read_text(encoding="utf-8")


@mcp.resource("f4crashdoctor://prompt-kit")
def prompt_kit_resource() -> str:
    """The crash-diagnosis prompt kit (same text as the diagnose-fallout4-crash prompt)."""
    return (knowledge.DATA_DIR / "prompt_kit.md").read_text(encoding="utf-8")


@mcp.resource("f4crashdoctor://calibration-profile")
def profile_resource() -> str:
    """Fallout 4 calibration profile: paths, version matrix, known-issue mod lists (raw JSON)."""
    return (knowledge.DATA_DIR / "f4_profile.json").read_text(encoding="utf-8")


@mcp.resource("f4crashdoctor://knowledge-pack")
def knowledge_resource() -> str:
    """Crash signature knowledge pack (raw JSON)."""
    return (knowledge.DATA_DIR / "signatures.json").read_text(encoding="utf-8")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
