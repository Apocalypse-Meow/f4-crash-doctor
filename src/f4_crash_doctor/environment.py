"""Fallout 4 environment scanner for the F4 Crash Doctor MCP server.

Read-only discovery of:
- the game install root (env var -> calibration profile -> Steam library scan)
- Fallout4.exe / F4SE / Address Library / Buffout 4 versions
- the Plugins.txt load order, cross-checked against the Data folder
- which mod manager owns the install (MO2 / Vortex / manual), per plan 8.1

Every function here is pure-read: nothing on disk or in the registry is ever
written, moved, or deleted. Expected failures (missing files, no registry key,
non-Windows host) degrade to None / empty values plus a warnings entry —
they never raise.
"""

import os
import re
from pathlib import Path

_BUFFOUT_RE = re.compile(r"(Buffout4(?:AE|NG)?)\s+v([\d.]+)")
_F4SE_DLL_RE = re.compile(r"f4se_(\d+)_(\d+)_(\d+)\.dll", re.IGNORECASE)
_ADDRLIB_BIN_RE = re.compile(r"version-(\d+)-(\d+)-(\d+)-(\d+)\.bin", re.IGNORECASE)
_VDF_PATH_RE = re.compile(r'"path"\s+"([^"]+)"')

PLUGIN_EXTS = (".esm", ".esp", ".esl")

# Plugins the game loads implicitly: they are NEVER listed in Plugins.txt,
# so their absence from the load order is normal, not a mismatch.
IMPLICIT_PLUGINS = {
    "fallout4.esm",
    "dlcrobot.esm",
    "dlcworkshop01.esm",
    "dlccoast.esm",
    "dlcworkshop02.esm",
    "dlcworkshop03.esm",
    "dlcnukaworld.esm",
    "dlcultrahighresolution.esm",
}

# Creation Club content (also loaded implicitly, via Fallout4.ccc):
# e.g. ccBGSFO4044-HellfirePowerArmor.esl
_CC_PLUGIN_RE = re.compile(r"^cc\w{3}fo4\d+", re.IGNORECASE)

DEFAULT_STEAM_DIR = Path(r"C:\Program Files (x86)\Steam")


# ---------------------------------------------------------------------------
# small safe helpers (never raise)
# ---------------------------------------------------------------------------


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _has_game_exe(root: Path) -> bool:
    return _is_file(root / "Fallout4.exe")


def _glob_names(directory: Path, pattern: str) -> list[str]:
    """Sorted filenames in directory matching pattern; [] on any failure."""
    try:
        return sorted(p.name for p in directory.glob(pattern) if p.is_file())
    except OSError:
        return []


def _documents_dir() -> Path:
    """The user's Documents folder. Delegates to crashlog._documents_dir so
    both modules always resolve the SAME path (F4_DOCS_DIR override ->
    SHGetKnownFolderPath, which honors OneDrive Documents redirection ->
    ~/Documents). Never raises."""
    try:
        from f4_crash_doctor import crashlog

        return crashlog._documents_dir()
    except Exception:
        pass
    env = os.environ.get("F4_DOCS_DIR", "")
    if env:
        return Path(env)
    return Path.home() / "Documents"


# ---------------------------------------------------------------------------
# game root discovery
# ---------------------------------------------------------------------------


def _steam_scan() -> Path | None:
    """Find Fallout 4 by walking Steam's library folders. None if not found."""
    steam_dir: Path | None = None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"
        ) as key:
            install_path, _ = winreg.QueryValueEx(key, "InstallPath")
            steam_dir = Path(install_path)
    except (ImportError, OSError):
        steam_dir = None
    if steam_dir is None or not _is_dir(steam_dir):
        steam_dir = DEFAULT_STEAM_DIR

    vdf = steam_dir / "steamapps" / "libraryfolders.vdf"
    try:
        text = vdf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    for raw in _VDF_PATH_RE.findall(text):
        library = Path(raw.replace("\\\\", "\\"))
        candidate = library / "steamapps" / "common" / "Fallout 4"
        if _has_game_exe(candidate):
            return candidate
    return None


def find_game_root(profile: dict | None = None) -> Path | None:
    """Locate the Fallout 4 install root, or None.

    Order: F4_GAME_ROOT env var -> profile["known_roots"] -> Steam library
    scan (registry InstallPath, then libraryfolders.vdf). A candidate only
    counts if it actually contains Fallout4.exe. Never raises.
    """
    env_root = os.environ.get("F4_GAME_ROOT", "")
    if env_root:
        candidate = Path(env_root)
        if _has_game_exe(candidate):
            return candidate

    for entry in (profile or {}).get("known_roots", []):
        try:
            candidate = Path(entry)
        except (TypeError, ValueError):
            continue
        if _has_game_exe(candidate):
            return candidate

    return _steam_scan()


# ---------------------------------------------------------------------------
# version probes
# ---------------------------------------------------------------------------


def get_exe_version(path: Path) -> str | None:
    """File version of a Windows executable as 'A.B.C.D', or None.

    Uses the Win32 version API via ctypes; returns None on any failure
    (non-Windows, missing file, no version resource, empty file, ...).
    """
    try:
        import ctypes

        version = ctypes.windll.version  # type: ignore[attr-defined]
        path_str = str(path)
        size = version.GetFileVersionInfoSizeW(path_str, None)
        if not size:
            return None
        buffer = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(path_str, 0, size, buffer):
            return None
        value = ctypes.c_void_p()
        value_len = ctypes.c_uint()
        if not version.VerQueryValueW(
            buffer, "\\", ctypes.byref(value), ctypes.byref(value_len)
        ):
            return None
        if not value.value or value_len.value < 52:  # sizeof(VS_FIXEDFILEINFO)
            return None
        fixed = ctypes.cast(value, ctypes.POINTER(ctypes.c_uint32 * 13)).contents
        if fixed[0] != 0xFEEF04BD:  # dwSignature sanity check
            return None
        ms, ls = fixed[2], fixed[3]  # dwFileVersionMS, dwFileVersionLS
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return None


def parse_buffout_runtime_log(text: str) -> dict:
    """Extract Buffout's name/version from its runtime log text.

    Matches lines like '[15:27:04.389] [12564] [I] Buffout4AE v1.7.1.0'.
    Returns {"name": ..., "version": ...} or {} if no match.
    """
    m = _BUFFOUT_RE.search(text)
    if not m:
        return {}
    return {"name": m.group(1), "version": m.group(2)}


# ---------------------------------------------------------------------------
# load order
# ---------------------------------------------------------------------------


def _decode_text_file(raw: bytes) -> str:
    """Decode a small Windows text file: BOM-sniffed UTF-16/UTF-8, then strict
    UTF-8, then the ANSI codepage (the FO4 launcher writes Plugins.txt in
    ANSI), then lossy UTF-8 as a last resort. Never raises."""
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("mbcs")  # Windows ANSI codepage
    except (UnicodeDecodeError, LookupError):
        return raw.decode("utf-8", errors="replace")


def _read_ccc_plugins(data_dir: Path) -> set[str]:
    """Lowercased plugin names listed in <game root>/Fallout4.ccc, or set()."""
    try:
        raw = (data_dir.parent / "Fallout4.ccc").read_bytes()
    except OSError:
        return set()
    return {
        line.strip().lower()
        for line in _decode_text_file(raw).splitlines()
        if line.strip()
    }


def get_load_order(
    plugins_txt: Path | None = None,
    data_dir: Path | None = None,
    managers: dict | None = None,
) -> dict:
    """Parse Plugins.txt and cross-check enabled entries against Data.

    Defaults: %LOCALAPPDATA%/Fallout4/Plugins.txt and <game root>/Data.
    Missing file/dir degrades to None path + warning; when either side is
    unavailable both cross-check lists are []. Never raises.

    Implicitly loaded plugins (Fallout4.esm, the official DLC ESMs, and
    Creation Club content listed in Fallout4.ccc) are never in Plugins.txt,
    so they are exempted from in_data_not_in_plugins.

    MO2 awareness (plan 8.1): when `managers` (a detect_managers result, or
    one detected here when defaults are used) concludes MO2 manages this
    game, the on-disk Plugins.txt is NOT the real load order — MO2 keeps the
    live one in its profile and presents it via its virtual file system. In
    that case plugins_txt_reliable is False, both cross-check lists are left
    empty, and a warning explains why.
    """
    warnings: list[str] = []

    root: Path | None = None
    used_default_plugins_txt = plugins_txt is None
    if plugins_txt is None:
        plugins_txt = (
            Path(os.environ.get("LOCALAPPDATA", "")) / "Fallout4" / "Plugins.txt"
        )
    if data_dir is None:
        root = find_game_root()
        if root is not None:
            data_dir = root / "Data"

    # --- mod-manager awareness (only self-detect on the default live path,
    # so explicit-path callers/tests stay hermetic) ---
    if managers is None and used_default_plugins_txt:
        try:
            managers = detect_managers(game_root=root)
        except Exception:
            managers = None
    mo2_managing = bool(
        managers
        and (
            managers.get("conclusion") == "mo2"
            or (managers.get("mo2") or {}).get("managing_fallout4")
        )
    )

    # --- Plugins.txt side ---
    plugins_txt_path: str | None = None
    entries: list[dict] = []
    try:
        text = _decode_text_file(plugins_txt.read_bytes())
        plugins_txt_path = str(plugins_txt)
    except OSError:
        warnings.append(f"Plugins.txt not found or unreadable: {plugins_txt}")
        text = None
    if text is not None:
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            enabled = line.startswith("*")
            name = line[1:].strip() if enabled else line
            if name:
                entries.append({"name": name, "enabled": enabled})

    # --- Data side ---
    data_dir_str: str | None = None
    data_plugins: list[str] = []
    counts_by_ext = {"esm": 0, "esp": 0, "esl": 0}
    if data_dir is not None and _is_dir(data_dir):
        data_dir_str = str(data_dir)
        try:
            for child in sorted(data_dir.iterdir()):
                ext = child.suffix.lower()
                if ext in PLUGIN_EXTS and child.is_file():
                    data_plugins.append(child.name)
                    counts_by_ext[ext[1:]] += 1
        except OSError as e:
            data_dir_str = None
            data_plugins = []
            counts_by_ext = {"esm": 0, "esp": 0, "esl": 0}
            warnings.append(f"could not list Data dir {data_dir}: {e}")
    elif data_dir is not None:
        warnings.append(f"Data dir not found: {data_dir}")
    else:
        warnings.append("Data dir could not be resolved (game root unknown)")

    # --- cross-check (only when both sides resolved) ---
    in_plugins_not_in_data: list[str] = []
    in_data_not_in_plugins: list[str] = []
    implicit_in_data: list[str] = []
    if mo2_managing:
        warnings.append(
            "Mod Organizer 2 manages this game: the on-disk Plugins.txt is "
            "not the real load order (MO2 keeps the live one in its profile "
            "and virtualizes mod files), so the Data cross-check was skipped. "
            "Ask the user to check the load order in MO2's right pane instead."
        )
    elif plugins_txt_path is not None and data_dir_str is not None:
        ccc_plugins = _read_ccc_plugins(data_dir)
        data_lower = {n.lower() for n in data_plugins}
        entry_lower = {e["name"].lower() for e in entries}
        in_plugins_not_in_data = [
            e["name"]
            for e in entries
            if e["enabled"] and e["name"].lower() not in data_lower
        ]
        for n in data_plugins:
            low = n.lower()
            if low in entry_lower:
                continue
            # implicitly loaded: base game, DLC, Creation Club — never in
            # Plugins.txt, so not a mismatch
            if (
                low in IMPLICIT_PLUGINS
                or low in ccc_plugins
                or _CC_PLUGIN_RE.match(n)
            ):
                implicit_in_data.append(n)
                continue
            in_data_not_in_plugins.append(n)

    return {
        "plugins_txt_path": plugins_txt_path,
        "plugins_txt_reliable": not mo2_managing,
        "entries": entries,
        "enabled_count": sum(1 for e in entries if e["enabled"]),
        "data_dir": data_dir_str,
        "data_plugins": data_plugins,
        "in_plugins_not_in_data": in_plugins_not_in_data,
        "in_data_not_in_plugins": in_data_not_in_plugins,
        "implicit_plugins_in_data": implicit_in_data,
        "counts_by_ext": counts_by_ext,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# mod manager detection (plan 8.1: detect, never write)
# ---------------------------------------------------------------------------


_MO2_GAMENAME_RE = re.compile(r"^\s*gameName\s*=\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)


def _mo2_ini_manages_fallout4(ini: Path) -> bool | None:
    """Whether an MO2 ModOrganizer.ini is for Fallout 4.

    True/False when gameName is readable, None when it cannot be determined
    (unreadable file / no gameName key). Never raises.
    """
    try:
        text = _decode_text_file(ini.read_bytes())
    except OSError:
        return None
    m = _MO2_GAMENAME_RE.search(text)
    if not m:
        return None
    return "fallout4" in m.group(1).lower().replace(" ", "")


def detect_managers(
    game_root: Path | None = None,
    localappdata: Path | None = None,
    appdata: Path | None = None,
) -> dict:
    """Detect which mod manager owns THIS game's install (plan 8.1).

    Read-only, never raises. Detection is per-game: game-specific evidence
    (an MO2 instance whose ModOrganizer.ini says gameName=Fallout 4, a
    portable ModOrganizer.ini next to the game, Vortex deployment markers
    physically inside Data) always outranks game-agnostic evidence (a bare
    %LOCALAPPDATA%\\ModOrganizer directory, which exists for MO2 instances
    of ANY game and survives MO2 uninstalls).

    Conclusion:
    - 'mo2'    — an MO2 instance for Fallout 4 was found and Vortex is not
                 deploying to this Data folder.
    - 'vortex' — Vortex is managing Fallout 4 and no FO4 MO2 instance found.
    - 'unknown' + low confidence — both managers show Fallout-4-specific
                 evidence (the model should ask the user which one is live).
    - 'manual' — a game root is known and neither manager claims it. This is
                 an absence-of-evidence conclusion; confidence is 'low'
                 whenever it cannot be corroborated (portable MO2 installs
                 living in their own folder are invisible to this scan), so
                 consumers should ask the user before giving file-level
                 instructions when confidence is 'low'.
    - 'unknown' — no game root and no manager evidence.
    """
    if localappdata is None:
        env = os.environ.get("LOCALAPPDATA", "")
        localappdata = Path(env) if env else None
    if appdata is None:
        env = os.environ.get("APPDATA", "")
        appdata = Path(env) if env else None

    notes: list[str] = []
    mo2_evidence: list[str] = []
    mo2_fo4_evidence: list[str] = []

    # MO2 instance registry: %LOCALAPPDATA%\ModOrganizer\<instance>\ModOrganizer.ini
    if localappdata is not None:
        mo2_dir = localappdata / "ModOrganizer"
        if _exists(mo2_dir):
            mo2_evidence.append(str(mo2_dir))
            try:
                instance_inis = sorted(mo2_dir.glob("*/ModOrganizer.ini"))
            except OSError:
                instance_inis = []
            for ini in instance_inis:
                if _mo2_ini_manages_fallout4(ini):
                    mo2_fo4_evidence.append(str(ini))

    # Portable MO2 sitting in (or next to) the game folder.
    if game_root is not None:
        for ini in (game_root / "ModOrganizer.ini", game_root.parent / "ModOrganizer.ini"):
            if _is_file(ini):
                mo2_evidence.append(str(ini))
                # An ini adjacent to this game is FO4 evidence unless its
                # gameName explicitly says otherwise.
                if _mo2_ini_manages_fallout4(ini) is not False:
                    mo2_fo4_evidence.append(str(ini))

    vortex_evidence: list[str] = []
    vortex_installed = False
    vortex_managing = False
    if appdata is not None:
        vortex_dir = appdata / "Vortex"
        if _exists(vortex_dir):
            vortex_installed = True
            vortex_evidence.append(str(vortex_dir))
        if _is_dir(vortex_dir / "fallout4"):
            vortex_managing = True
            vortex_evidence.append(str(vortex_dir / "fallout4"))
    if game_root is not None:
        for marker in ("__folder_managed_by_vortex", "vortex.deployment.json"):
            marker_path = game_root / "Data" / marker
            if _exists(marker_path):
                vortex_managing = True
                vortex_evidence.append(str(marker_path))

    mo2_managing = bool(mo2_fo4_evidence)
    confidence = "high"

    if mo2_managing and vortex_managing:
        conclusion = "unknown"
        confidence = "low"
        notes.append(
            "Both an MO2 Fallout 4 instance and Vortex deployment evidence "
            "were found — ask the user which manager they actually use for "
            "Fallout 4 before phrasing any fix."
        )
    elif mo2_managing:
        conclusion = "mo2"
    elif vortex_managing:
        conclusion = "vortex"
        if mo2_evidence:
            notes.append(
                "An MO2 installation exists but no Fallout 4 instance was "
                "found in it; Vortex is deploying to this game's Data folder, "
                "so Vortex is treated as the manager."
            )
    elif game_root is not None:
        conclusion = "manual"
        confidence = "low"
        notes.append(
            "No manager evidence was found, but portable MO2 installs in "
            "their own folder leave no trace this scan can see — confirm with "
            "the user how they install mods before giving file-level "
            "instructions."
        )
    else:
        conclusion = "unknown"
        confidence = "low"

    return {
        "mo2": {
            "detected": bool(mo2_evidence),
            "managing_fallout4": mo2_managing,
            "evidence": mo2_evidence,
            "fallout4_evidence": mo2_fo4_evidence,
        },
        "vortex": {
            "installed": vortex_installed,
            "managing_fallout4": vortex_managing,
            "evidence": vortex_evidence,
        },
        "conclusion": conclusion,
        "confidence": confidence,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# full environment scan
# ---------------------------------------------------------------------------


def scan_environment(
    game_root: Path | None = None, profile: dict | None = None
) -> dict:
    """One-shot read-only snapshot of the Fallout 4 modding environment.

    Every key is always present; anything unresolvable degrades to
    None/False/[] plus an entry in 'warnings'. Never raises.
    """
    warnings: list[str] = []

    if profile is None:
        try:
            from f4_crash_doctor.knowledge import load_profile

            profile = load_profile()
        except Exception as e:
            profile = {}
            warnings.append(f"calibration profile unavailable: {e}")

    if game_root is None:
        game_root = find_game_root(profile)
    elif not isinstance(game_root, Path):
        game_root = Path(game_root)

    # --- game exe + F4SE (game root) ---
    game_exe_version: str | None = None
    f4se = {"installed": False, "loader_present": False, "dll_version": None}
    if game_root is not None:
        exe = game_root / "Fallout4.exe"
        if _is_file(exe):
            game_exe_version = get_exe_version(exe)
            if game_exe_version is None:
                warnings.append("could not read Fallout4.exe file version")
        else:
            warnings.append(f"Fallout4.exe not found in {game_root}")
        f4se["loader_present"] = _is_file(game_root / "f4se_loader.exe")
        f4se_dlls = _glob_names(game_root, "f4se_*.dll")
        f4se["installed"] = bool(f4se_dlls)
        for name in f4se_dlls:
            m = _F4SE_DLL_RE.fullmatch(name)
            if m:
                f4se["dll_version"] = ".".join(m.groups())
                break
    else:
        warnings.append("game root not found (set F4_GAME_ROOT or check Steam install)")

    # --- Data / F4SE plugin directory ---
    address_library: dict = {"present": False, "versions": [], "matches_game": None}
    buffout4: dict = {"installed": False, "variant": None, "version": None}
    other_crash_loggers: list[str] = []
    f4se_plugin_dlls: list[str] = []
    ba2_count: int | None = None

    data_dir = game_root / "Data" if game_root is not None else None
    if data_dir is not None and _is_dir(data_dir):
        ba2_count = len(_glob_names(data_dir, "*.ba2"))
        plugins_dir = data_dir / "F4SE" / "Plugins"
        if _is_dir(plugins_dir):
            for name in _glob_names(plugins_dir, "version-*.bin"):
                m = _ADDRLIB_BIN_RE.fullmatch(name)
                if m:
                    address_library["present"] = True
                    address_library["versions"].append(
                        f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
                    )
            f4se_plugin_dlls = _glob_names(plugins_dir, "*.dll")
            other_crash_loggers = [
                n for n in f4se_plugin_dlls if "crashlogger" in n.lower()
            ]
            dlls_lower = {n.lower() for n in f4se_plugin_dlls}
            if "buffout4ae.dll" in dlls_lower:
                buffout4["installed"] = True
                buffout4["variant"] = "AE"
            elif "buffout4.dll" in dlls_lower:
                buffout4["installed"] = True
                buffout4["variant"] = "OG"
        else:
            warnings.append(f"F4SE plugin dir not found: {plugins_dir}")
    elif game_root is not None:
        warnings.append(f"Data dir not found: {data_dir}")

    if game_exe_version is not None:
        short = ".".join(game_exe_version.split(".")[:3])
        address_library["matches_game"] = short in address_library["versions"]

    # --- documents dir + crash logs (via crashlog module when available) ---
    docs = _documents_dir()
    try:
        from f4_crash_doctor import crashlog

        crash_dirs = crashlog.default_crash_dirs()
        crash_log_count = len(crashlog.find_crash_logs(crash_dirs))
    except Exception as e:
        crash_dirs = [
            docs / "My Games" / "Fallout4" / "F4SE" / "Crash Logs",
            docs / "My Games" / "Fallout4" / "F4SE",
        ]
        crash_log_count = 0
        warnings.append(f"crashlog module unavailable, crash log count skipped: {e}")

    # --- Buffout runtime version from its own log ---
    f4se_docs_dir = docs / "My Games" / "Fallout4" / "F4SE"
    for log_name in ("Buffout4AE.log", "Buffout4.log"):
        try:
            text = (f4se_docs_dir / log_name).read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            continue
        info = parse_buffout_runtime_log(text)
        if info:
            buffout4["version"] = info["version"]
            break

    return {
        "game_root": str(game_root) if game_root is not None else None,
        "game_exe_version": game_exe_version,
        "f4se": f4se,
        "address_library": address_library,
        "buffout4": buffout4,
        "other_crash_loggers": other_crash_loggers,
        "f4se_plugin_dlls": f4se_plugin_dlls,
        "ba2_count": ba2_count,
        "documents_dir": str(docs),
        "crash_log_dirs_checked": [str(d) for d in crash_dirs],
        "crash_log_count": crash_log_count,
        "managers": detect_managers(game_root=game_root),
        "warnings": warnings,
    }
