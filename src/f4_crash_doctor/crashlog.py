"""Buffout 4 crash-log discovery and parsing (read-only).

Three public functions:
- default_crash_dirs: where Buffout 4 drops crash logs on this machine
- find_crash_logs: newest-first list of crash-*.log files
- parse_crash_log / parse_crash_log_file: structured dict from a log's text

Everything here is strictly read-only and never raises for expected failures:
parse_crash_log returns a full-shape dict (with parse_warnings) for ANY input,
and parse_crash_log_file returns {"error": ...} if the file cannot be read.
"""

import os
import re
from datetime import datetime
from pathlib import Path

# Literal section headers as they appear at line start in Buffout 4 logs,
# in their canonical order. Any of them may be absent from a given log.
SECTION_HEADERS = [
    "SETTINGS:",
    "SYSTEM SPECS:",
    "PROBABLE CALL STACK:",
    "REGISTERS:",
    "STACK:",
    "MODULES:",
    "F4SE PLUGINS:",
    "PLUGINS:",
]

STACK_LINE_LIMIT = 50

_GAME_VERSION_RE = re.compile(r"^Fallout\s?4\s+v([\d.]+)")
_BUFFOUT_VERSION_RE = re.compile(r"^Buffout\s?4(?:\s+NG)?\s+v([\d.]+)")
_EXCEPTION_RE = re.compile(
    r'Unhandled exception\s+"([A-Z0-9_]+)"\s+at\s+(0x[0-9A-Fa-f]+)'
    r"(?:\s+(\S+)\+([0-9A-Fa-f]+))?"
)
_MODULE_OFFSET_RE = re.compile(r"^(\S+)\+([0-9A-Fa-f]+)$")
_CALL_STACK_FRAME_RE = re.compile(
    r"^\[\s*(\d+)\]\s+(0x[0-9A-Fa-f]+)(?:\s+(.*))?$"
)
_PLUGIN_RE = re.compile(r"^\[([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{3})?)\]\s+(.+)$")
_F4SE_PLUGIN_VERSION_RE = re.compile(r"\bv([\d.]+)")
_SETTINGS_SECTION_RE = re.compile(r"^\[([^\]]+)\]$")


def _documents_dir() -> Path:
    """Resolve the user's Documents folder. Never raises."""
    env = os.environ.get("F4_DOCS_DIR")
    if env:
        return Path(env)
    try:
        import ctypes
        from ctypes import wintypes

        # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
        class _GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        guid = _GUID(
            0xFDD39AD0,
            0x238F,
            0x46AF,
            (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7),
        )
        out = ctypes.c_wchar_p()
        res = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), 0, None, ctypes.byref(out)
        )
        if res == 0 and out.value:
            docs = Path(out.value)
            ctypes.windll.ole32.CoTaskMemFree(out)
            return docs
    except Exception:
        pass
    return Path.home() / "Documents"


def default_crash_dirs() -> list[Path]:
    """Default directories where Buffout 4 writes crash logs. Never raises."""
    docs = _documents_dir()
    base = docs / "My Games" / "Fallout4" / "F4SE"
    return [base / "Crash Logs", base]


def find_crash_logs(search_dirs: list[Path] | None = None) -> list[Path]:
    """Find crash-*.log files in the given dirs, newest modification first."""
    if search_dirs is None:
        search_dirs = default_crash_dirs()
    found: dict[Path, Path] = {}
    for d in search_dirs:
        try:
            if not d.is_dir():
                continue
            for p in d.glob("crash-*.log"):
                key = p.resolve()
                if key not in found:
                    found[key] = p
        except OSError:
            continue

    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    return sorted(found.values(), key=_mtime, reverse=True)


def _empty_result() -> dict:
    return {
        "game_version": None,
        "buffout_version": None,
        "exception": {
            "type": None,
            "address": None,
            "module": None,
            "module_offset": None,
            "raw": None,
        },
        "settings": {},
        "system_specs": [],
        "call_stack": [],
        "registers": [],
        "stack": [],
        "modules": [],
        "f4se_plugins": [],
        "plugins": [],
        "sections_found": [],
        "parse_warnings": [],
    }


def _find_segments(lines: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    """Split lines into (preamble, {header-without-colon: section lines})."""
    boundaries: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        for header in SECTION_HEADERS:
            if line.startswith(header):
                boundaries.append((i, header))
                break
    preamble_end = boundaries[0][0] if boundaries else len(lines)
    segments: dict[str, list[str]] = {}
    for n, (start, header) in enumerate(boundaries):
        end = boundaries[n + 1][0] if n + 1 < len(boundaries) else len(lines)
        name = header.rstrip(":")
        if name not in segments:  # first occurrence wins
            segments[name] = lines[start + 1 : end]
    return lines[:preamble_end], segments


def _parse_settings(lines: list[str]) -> dict[str, dict[str, str]]:
    settings: dict[str, dict[str, str]] = {}
    current: str | None = None
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        m = _SETTINGS_SECTION_RE.match(line)
        if m:
            current = m.group(1)
            settings.setdefault(current, {})
            continue
        for sep in (":", "="):
            if sep in line:
                key, _, value = line.partition(sep)
                section = current if current is not None else ""
                settings.setdefault(section, {})[key.strip()] = value.strip()
                break
    return settings


def _parse_call_stack(lines: list[str]) -> list[dict]:
    frames: list[dict] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        m = _CALL_STACK_FRAME_RE.match(line)
        if not m:
            continue
        rest = (m.group(3) or "").strip()
        module: str | None = None
        offset: str | None = None
        symbol: str | None = None
        if rest:
            parts = re.split(r"\s*->\s*", rest, maxsplit=1)
            mod_part = parts[0].strip()
            if len(parts) == 2 and parts[1].strip():
                symbol = parts[1].strip()
            mo = _MODULE_OFFSET_RE.match(mod_part)
            if mo:
                module, offset = mo.group(1), mo.group(2)
            elif mod_part:
                module = mod_part
        frames.append(
            {
                "index": int(m.group(1)),
                "address": m.group(2),
                "module": module,
                "offset": offset,
                "symbol": symbol,
                "raw": line,
            }
        )
    return frames


def _parse_f4se_plugins(lines: list[str]) -> list[dict]:
    plugins: list[dict] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        name = line.split()[0]
        vm = _F4SE_PLUGIN_VERSION_RE.search(line[len(name):])
        plugins.append({"name": name, "version": vm.group(1) if vm else None})
    return plugins


def _parse_plugins(lines: list[str]) -> list[dict]:
    plugins: list[dict] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        m = _PLUGIN_RE.match(line)
        if not m:
            continue
        index = m.group(1)
        plugins.append(
            {"index": index, "name": m.group(2).strip(), "is_light": ":" in index}
        )
    return plugins


def _stripped(lines: list[str]) -> list[str]:
    return [line.strip() for line in lines if line.strip()]


def parse_crash_log(text: str) -> dict:
    """Parse Buffout 4 crash-log text into a structured dict.

    Never raises: any input (empty, binary garbage, partial logs) yields the
    full-shape dict, with problems noted in parse_warnings.
    """
    result = _empty_result()
    warnings: list[str] = result["parse_warnings"]
    try:
        lines = text.splitlines()
        preamble, segments = _find_segments(lines)
        result["sections_found"] = [
            h.rstrip(":") for h in SECTION_HEADERS if h.rstrip(":") in segments
        ]

        # --- preamble: versions + exception line ---
        for line in preamble:
            stripped = line.strip()
            if result["game_version"] is None:
                m = _GAME_VERSION_RE.match(stripped)
                if m:
                    result["game_version"] = m.group(1).rstrip(".")
                    continue
            if result["buffout_version"] is None:
                m = _BUFFOUT_VERSION_RE.match(stripped)
                if m:
                    result["buffout_version"] = m.group(1).rstrip(".")

        for i, line in enumerate(preamble):
            m = _EXCEPTION_RE.search(line)
            if not m:
                continue
            exc = result["exception"]
            exc["type"] = m.group(1)
            exc["address"] = m.group(2)
            exc["raw"] = line.strip()
            if m.group(3):
                exc["module"], exc["module_offset"] = m.group(3), m.group(4)
            else:
                # module+offset may be on the next non-empty line
                for follow in preamble[i + 1 :]:
                    follow = follow.strip()
                    if not follow:
                        continue
                    mo = _MODULE_OFFSET_RE.match(follow)
                    if mo:
                        exc["module"], exc["module_offset"] = mo.group(1), mo.group(2)
                    break
            break

        if result["game_version"] is None:
            warnings.append("game version line not found")
        if result["buffout_version"] is None:
            warnings.append("Buffout 4 version line not found")
        if result["exception"]["raw"] is None:
            warnings.append("unhandled exception line not found")

        # --- sections ---
        if "SETTINGS" in segments:
            result["settings"] = _parse_settings(segments["SETTINGS"])
        if "SYSTEM SPECS" in segments:
            result["system_specs"] = _stripped(segments["SYSTEM SPECS"])
        if "PROBABLE CALL STACK" in segments:
            result["call_stack"] = _parse_call_stack(segments["PROBABLE CALL STACK"])
        if "REGISTERS" in segments:
            result["registers"] = _stripped(segments["REGISTERS"])
        if "STACK" in segments:
            stack_lines = _stripped(segments["STACK"])
            if len(stack_lines) > STACK_LINE_LIMIT:
                warnings.append(
                    f"STACK truncated to first {STACK_LINE_LIMIT} of "
                    f"{len(stack_lines)} lines"
                )
                stack_lines = stack_lines[:STACK_LINE_LIMIT]
            result["stack"] = stack_lines
        if "MODULES" in segments:
            result["modules"] = [
                line.split()[0] for line in _stripped(segments["MODULES"])
            ]
        if "F4SE PLUGINS" in segments:
            result["f4se_plugins"] = _parse_f4se_plugins(segments["F4SE PLUGINS"])
        if "PLUGINS" in segments:
            result["plugins"] = _parse_plugins(segments["PLUGINS"])

        for header in SECTION_HEADERS:
            name = header.rstrip(":")
            if name not in segments:
                warnings.append(f"section not found: {name}")
    except Exception as e:  # absolute never-raise guarantee
        warnings.append(f"parse error: {e}")
    return result


def _decode_log_bytes(raw: bytes) -> str:
    """Decode crash-log bytes tolerantly. Buffout writes plain UTF-8, but
    users re-save/share logs as 'UTF-8 with BOM' (Notepad) or UTF-16
    ('Unicode' save, PowerShell '>' redirection); sniff those so the parser
    still sees clean lines. Never raises."""
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="replace")
    # BOM-less UTF-16 heuristic: text files never contain NUL bytes in UTF-8
    sample = raw[:1024]
    if sample and b"\x00" in sample:
        nul_even = sample[::2].count(0)
        nul_odd = sample[1::2].count(0)
        if nul_odd > nul_even and nul_odd >= len(sample) // 4:
            return raw.decode("utf-16-le", errors="replace")
        if nul_even > nul_odd and nul_even >= len(sample) // 4:
            return raw.decode("utf-16-be", errors="replace")
    return raw.decode("utf-8", errors="replace")


# Real Buffout 4 logs are tens to hundreds of KB; anything this large is not one.
MAX_LOG_BYTES = 5 * 1024 * 1024


def parse_crash_log_file(path: Path) -> dict:
    """Read and parse a crash log file; {"error": msg} if unreadable."""
    path = Path(path)
    try:
        size = path.stat().st_size
        if size > MAX_LOG_BYTES:
            return {
                "error": (
                    f"{path} is {size} bytes — far larger than any Buffout 4 "
                    f"crash log (limit {MAX_LOG_BYTES}). Refusing to read it."
                )
            }
        text = _decode_log_bytes(path.read_bytes())
        mtime = path.stat().st_mtime
    except OSError as e:
        return {"error": f"cannot read crash log {path}: {e}"}
    result = parse_crash_log(text)
    result["source_path"] = str(path)
    result["modified_iso"] = datetime.fromtimestamp(mtime).isoformat()
    return result
