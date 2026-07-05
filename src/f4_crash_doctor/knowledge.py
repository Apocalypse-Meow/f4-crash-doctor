"""Knowledge pack loading and declarative crash-signature matching.

Data-not-code: the crash signatures live in data/signatures.json and the
Fallout 4 calibration profile in data/f4_profile.json. This module only
knows how to load them and evaluate signatures against a parsed crash log,
a load-order report, and an environment scan.

A signature matches iff ALL of its scope blocks pass AND ALL of its named
conditions pass. Entries with empty scopes AND empty conditions are
documentation-only: they never match, but ship in the pack so an LLM can
read them from the knowledge resource. Signatures referencing an unknown
condition name are skipped silently (forward compatibility, never a crash).
"""

import json
from pathlib import Path
from typing import Callable

DATA_DIR: Path = Path(__file__).parent / "data"

_CONFIDENCE_RANK: dict[str, int] = {"high": 0, "medium": 1, "low": 2}

_SCOPES = ("exception", "call_stack", "plugins", "f4se_plugins", "modules")


def load_signatures(path: Path | None = None) -> list[dict]:
    """Load the crash-signature pack (JSON array of signature dicts)."""
    if path is None:
        path = DATA_DIR / "signatures.json"
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_profile(path: Path | None = None) -> dict:
    """Load the Fallout 4 calibration profile (paths, version matrix, notes)."""
    if path is None:
        path = DATA_DIR / "f4_profile.json"
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --- named environment/load-order conditions ------------------------------


def _cond_address_library_mismatch(
    parsed_log: dict, load_order: dict, environment: dict
) -> tuple[bool, str]:
    addr = environment.get("address_library", {}) or {}
    if addr.get("matches_game") is False:
        found = ", ".join(addr.get("versions", [])) or "none"
        game = environment.get("game_exe_version") or "unknown"
        return True, (
            f"Address Library versions on disk ({found}) do not match "
            f"the game exe version ({game})"
        )
    return False, ""


def _cond_plugins_missing_from_data(
    parsed_log: dict, load_order: dict, environment: dict
) -> tuple[bool, str]:
    # Under MO2 the on-disk Plugins.txt is not the real (virtualized) load
    # order, so a Plugins.txt-vs-Data mismatch is meaningless — never fire
    # a high-confidence missing-master finding from it (plan 8.1).
    managers = environment.get("managers") or {}
    if managers.get("conclusion") == "mo2" or (managers.get("mo2") or {}).get(
        "managing_fallout4"
    ):
        return False, ""
    if load_order.get("plugins_txt_reliable") is False:
        return False, ""
    missing = load_order.get("in_plugins_not_in_data") or []
    if missing:
        return True, (
            "Enabled in Plugins.txt but missing from the Data folder: "
            + ", ".join(str(m) for m in missing)
        )
    return False, ""


def _cond_plugin_ff_index(
    parsed_log: dict, load_order: dict, environment: dict
) -> tuple[bool, str]:
    at_ff = [
        p.get("name", "?")
        for p in parsed_log.get("plugins", [])
        if p.get("index") == "FF"
    ]
    if at_ff:
        return True, "Plugin(s) loaded at index FF (past the 255 cap): " + ", ".join(at_ff)
    return False, ""


CONDITION_CHECKS: dict[str, Callable[[dict, dict, dict], tuple[bool, str]]] = {
    "address_library_mismatch": _cond_address_library_mismatch,
    "plugins_missing_from_data": _cond_plugins_missing_from_data,
    "plugin_ff_index": _cond_plugin_ff_index,
}


# --- scope-block matching --------------------------------------------------


def _haystack(parsed_log: dict, scope: str) -> list[str]:
    """Build the list of searchable lines for a scope from the parsed log."""
    if scope == "exception":
        exc = parsed_log.get("exception") or {}
        return [
            exc.get("raw") or "",
            exc.get("type") or "",
            exc.get("address") or "",
            exc.get("module") or "",
        ]
    if scope == "call_stack":
        return [f.get("raw", "") for f in parsed_log.get("call_stack", [])]
    if scope == "plugins":
        return [p.get("name", "") for p in parsed_log.get("plugins", [])]
    if scope == "f4se_plugins":
        return [p.get("name", "") for p in parsed_log.get("f4se_plugins", [])]
    if scope == "modules":
        return [str(m) for m in parsed_log.get("modules", [])]
    return []


def _match_block(block: dict, parsed_log: dict) -> tuple[bool, list[str]]:
    """Evaluate one scope block. Returns (passed, evidence_lines)."""
    lines = _haystack(parsed_log, block.get("scope", ""))
    lower = [line.lower() for line in lines]
    any_of = [t.lower() for t in block.get("any_of", []) or []]
    all_of = [t.lower() for t in block.get("all_of", []) or []]
    none_of = [t.lower() for t in block.get("none_of", []) or []]
    min_count = block.get("min_count", 1)

    evidence: list[str] = []

    for term in none_of:
        if any(term in line for line in lower):
            return False, []

    for term in all_of:
        hits = [lines[i] for i, line in enumerate(lower) if term in line]
        if not hits:
            return False, []
        evidence.append(hits[0].strip())

    if any_of:
        count = 0
        for i, line in enumerate(lower):
            for term in any_of:
                if term in line:
                    count += 1
                    evidence.append(lines[i].strip())
        if count < min_count:
            return False, []

    # dedupe evidence, preserving order
    seen: set[str] = set()
    deduped = [e for e in evidence if e and not (e in seen or seen.add(e))]
    return True, deduped


# --- log-vs-environment staleness notes -------------------------------------


def _version_tuple(value) -> tuple[int, ...] | None:
    """Parse '1.11.221.0' → (1, 11, 221); None when absent or unparseable."""
    if not value:
        return None
    try:
        parts = tuple(int(p) for p in str(value).strip().split("."))
    except ValueError:
        return None
    while parts and parts[-1] == 0:
        parts = parts[:-1]
    return parts or None


def staleness_notes(
    parsed_log: dict, load_order: dict, environment: dict, findings: list[dict]
) -> list[str]:
    """Advisory notes when the crash log contradicts the live machine.

    Field lesson from the 2026-07-05 live validation (design doc section 10):
    users routinely diagnose stale or foreign logs — their newest log is often
    weeks old. Notes, never findings: severity stays with real signatures.
    """
    notes: list[str] = []

    log_raw = parsed_log.get("game_version")
    env_raw = environment.get("game_exe_version")
    log_v = _version_tuple(log_raw)
    env_v = _version_tuple(env_raw)
    if log_v and env_v and log_v != env_v:
        notes.append(
            f"staleness check: this crash log records game version {log_raw} "
            f"but the installed game is {env_raw} — the crash may predate the "
            "current setup. Treat the findings as historical unless the user "
            "confirms the crash is recent."
        )

    # Plugin-absence check only when the live listings are trustworthy: under
    # MO2 both Plugins.txt and the Data folder are virtualized, and empty
    # listings mean "the scan saw nothing", not "the plugin was removed".
    managers = environment.get("managers") or {}
    if managers.get("conclusion") == "mo2" or (managers.get("mo2") or {}).get(
        "managing_fallout4"
    ):
        return notes
    if load_order.get("plugins_txt_reliable") is False:
        return notes

    live: set[str] = {
        str(e.get("name", "")).lower() for e in load_order.get("entries") or []
    }
    live |= {str(n).lower() for n in load_order.get("data_plugins") or []}
    live.discard("")
    if not live:
        return notes

    evidence = "\n".join(
        line for f in findings for line in f.get("matched_evidence") or []
    ).lower()
    gone = [
        name
        for p in parsed_log.get("plugins") or []
        if (name := str(p.get("name", "")).strip())
        and name.lower() in evidence
        and name.lower() not in live
    ]
    if gone:
        notes.append(
            "staleness check: plugin(s) referenced by the findings are no "
            "longer in the live load order: "
            + ", ".join(dict.fromkeys(gone))
            + " — those findings may already be resolved."
        )
    return notes


def match_signatures(
    parsed_log: dict,
    load_order: dict | None = None,
    environment: dict | None = None,
    signatures: list[dict] | None = None,
) -> list[dict]:
    """Match the signature pack against a parsed crash log.

    Returns findings sorted by severity (highest first), then confidence
    (high > medium > low). Each finding:
    {"id", "name", "severity", "confidence", "cause", "fix",
     "matched_evidence": [str], "sources": [str]}
    """
    load_order = load_order or {}
    environment = environment or {}
    if signatures is None:
        signatures = load_signatures()

    findings: list[dict] = []
    for sig in signatures:
        scopes = sig.get("scopes", []) or []
        conditions = sig.get("conditions", []) or []

        # documentation-only entries never match
        if not scopes and not conditions:
            continue
        # unknown condition name -> skip the signature, never crash
        if any(name not in CONDITION_CHECKS for name in conditions):
            continue

        evidence: list[str] = []
        matched = True

        for block in scopes:
            passed, block_evidence = _match_block(block, parsed_log)
            if not passed:
                matched = False
                break
            evidence.extend(block_evidence)
        if not matched:
            continue

        for name in conditions:
            hit, cond_evidence = CONDITION_CHECKS[name](
                parsed_log, load_order, environment
            )
            if not hit:
                matched = False
                break
            evidence.append(cond_evidence)
        if not matched:
            continue

        findings.append(
            {
                "id": sig["id"],
                "name": sig["name"],
                "severity": sig["severity"],
                "confidence": sig["confidence"],
                "cause": sig["cause"],
                "fix": sig["fix"],
                "matched_evidence": evidence,
                "sources": sig.get("sources", []),
            }
        )

    findings.sort(
        key=lambda f: (-f["severity"], _CONFIDENCE_RANK.get(f["confidence"], 3))
    )
    return findings
