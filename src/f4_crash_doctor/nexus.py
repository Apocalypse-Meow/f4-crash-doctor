"""Read-only Nexus Mods client for F4 Crash Doctor.

Adapted from F:\\LLM-Tools\\mcp-nexus\\server.py, keeping only the read-only
surface: auth validation and mod inspection. The original download_file tool
is deliberately NOT carried over — this module must never write to disk
(advisory-only constraint, plan section 2).

Public functions follow the project error convention: expected failures
(missing API key, bad reference, non-200 responses, network trouble) return
{"error": msg} dicts and never raise to the caller.
"""

import os
import re

import httpx
from dotenv import load_dotenv

load_dotenv()

NEXUS_API = "https://api.nexusmods.com/v1"
USER_AGENT = "f4-crash-doctor/0.1.0"

_MISSING_KEY_MSG = (
    "NEXUS_API_KEY not set. Mod lookups are optional — every other tool "
    "(crash log parsing, environment scan, diagnosis) works without it. "
    "To enable Nexus lookups, put NEXUS_API_KEY=<your key> in a .env file "
    "or the environment (get a key at nexusmods.com under Site Preferences > API Keys)."
)


def _client() -> httpx.Client:
    """Build an httpx client for the Nexus API.

    Raises RuntimeError when NEXUS_API_KEY is missing; callers that must not
    raise (check_auth / inspect_mod) catch it and return an error dict.
    """
    api_key = os.environ.get("NEXUS_API_KEY", "")
    if not api_key:
        raise RuntimeError(_MISSING_KEY_MSG)
    return httpx.Client(
        base_url=NEXUS_API,
        headers={
            "apikey": api_key,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
        timeout=30.0,
    )


def parse_mod_ref(url_or_id: str) -> tuple[str, int]:
    """Parse a Nexus URL or 'game/mod_id' string into (game, mod_id).

    Accepts e.g. https://www.nexusmods.com/fallout4/mods/47359 or 'fallout4/47359'.
    Raises ValueError when the reference cannot be parsed.
    """
    m = re.match(r"https?://(?:www\.)?nexusmods\.com/([^/]+)/mods/(\d+)/?", url_or_id)
    if m:
        return m.group(1), int(m.group(2))

    m = re.match(r"^([a-z0-9_-]+)/(\d+)$", url_or_id.strip())
    if m:
        return m.group(1), int(m.group(2))

    raise ValueError(f"not a Nexus URL or game/mod_id: {url_or_id!r}")


def check_auth() -> dict:
    """Validate the Nexus API key and report account status. Never raises."""
    try:
        with _client() as c:
            r = c.get("/users/validate.json")
        if r.status_code != 200:
            return {"error": f"validate failed: {r.status_code}", "body": r.text[:200]}
        d = r.json()
        return {
            "name": d.get("name"),
            "is_premium": d.get("is_premium"),
            "is_supporter": d.get("is_supporter"),
            "user_id": d.get("user_id"),
        }
    except RuntimeError as e:
        return {"error": str(e)}
    except httpx.HTTPError as e:
        return {"error": f"network error talking to Nexus: {e}"}
    except Exception as e:  # never raise to the client
        return {"error": f"{type(e).__name__}: {e}"}


def inspect_mod(url_or_id: str) -> dict:
    """Get mod metadata and file list for a Nexus mod (read-only). Never raises.

    Accepts either a Nexus URL (e.g. https://www.nexusmods.com/fallout4/mods/47359)
    or 'game/mod_id' (e.g. 'fallout4/47359').
    """
    try:
        game, mod_id = parse_mod_ref(url_or_id)
    except ValueError as e:
        return {"error": str(e)}

    try:
        with _client() as c:
            info_r = c.get(f"/games/{game}/mods/{mod_id}.json")
            files_r = c.get(f"/games/{game}/mods/{mod_id}/files.json")
    except RuntimeError as e:
        return {"error": str(e)}
    except httpx.HTTPError as e:
        return {"error": f"network error talking to Nexus: {e}"}
    except Exception as e:  # never raise to the client
        return {"error": f"{type(e).__name__}: {e}"}

    if info_r.status_code != 200:
        return {"error": f"mod info failed: {info_r.status_code}", "body": info_r.text[:200]}
    if files_r.status_code != 200:
        return {"error": f"file list failed: {files_r.status_code}", "body": files_r.text[:200]}

    info = info_r.json()
    files = files_r.json().get("files", [])

    return {
        "game": game,
        "mod_id": mod_id,
        "name": info.get("name"),
        "summary": info.get("summary"),
        "version": info.get("version"),
        "author": info.get("author"),
        "endorsement_count": info.get("endorsement_count"),
        "downloads": info.get("download_count"),
        "updated_unix": info.get("updated_timestamp"),
        "url": f"https://www.nexusmods.com/{game}/mods/{mod_id}",
        "files": [
            {
                "file_id": f.get("file_id"),
                "name": f.get("name"),
                "category": f.get("category_name"),
                "version": f.get("version"),
                "size_kb": f.get("size_kb"),
                "uploaded_unix": f.get("uploaded_timestamp"),
                "description": (f.get("description") or "")[:200],
            }
            for f in files
            if f.get("category_name") in ("MAIN", "OPTIONAL", "OLD_VERSION", "MISCELLANEOUS")
        ],
    }
