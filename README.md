# F4 Crash Doctor

**Advisory-only Fallout 4 crash diagnosis for Claude Desktop, Claude Code, or any MCP client.**

When your modded Fallout 4 crashes, this tool reads the crash log, your load order, and your game setup, then explains the likely cause and fix in plain English — right inside your Claude conversation.

It is an **MCP server** — MCP (Model Context Protocol) is the standard way to give an AI assistant like Claude safe, well-defined abilities on your PC. This one gives Claude the ability to *read* your Fallout 4 crash information, nothing more.

## Safety promise

> **This tool never writes, moves, deletes, or modifies any file. Ever.**
>
> It has **zero** tools capable of changing anything — every tool it exposes is read-only. It only looks at your crash logs, your plugin list, and your game folder, then tells you what it found.
>
> Because it never touches your install, it works safely **alongside Vortex or Mod Organizer 2**. It even detects which mod manager you use, so the advice you get is phrased for *your* setup (e.g. "disable this in Vortex" vs "untick it in MO2's left pane").

## What you need

- **Windows 10 or 11**
- **Fallout 4** (Steam) with:
  - **F4SE** (Fallout 4 Script Extender — a loader many mods require, run via `f4se_loader.exe`)
  - **Buffout 4** — the mod that actually writes crash logs when the game crashes. Without it there is nothing to diagnose. Get it here: <https://www.nexusmods.com/fallout4/mods/47359>
- **uv** — a small free tool that runs Python programs without any setup on your part. One-line install in PowerShell:

  ```powershell
  winget install astral-sh.uv
  ```

  or, if winget isn't available:

  ```powershell
  irm https://astral.sh/uv/install.ps1 | iex
  ```

You do **not** need to install Python yourself — uv handles it.

## Install

### Easiest: the one-file bundle (Claude Desktop)

Download `f4-crash-doctor.mcpb` from the [latest release](https://github.com/Apocalypse-Meow/f4-crash-doctor/releases/latest), then in Claude Desktop open
**Settings → Extensions → Advanced settings** and click **Install Extension…** — pick the
downloaded file. (Drag-and-dropping the file onto the Extensions page works on some machines, but
Windows refuses the drop with a 🚫 cursor when it doesn't know the `.mcpb` file type — the
**Install Extension…** button works everywhere.) Claude Desktop asks two optional questions
(game folder — leave empty to auto-detect; Nexus API key — skip unless you want mod lookups), and
you're done. Skip straight to **First use** below.

### Manual install (Claude Code, other MCP clients, or if you prefer a terminal)

1. Download or clone this repository somewhere permanent, e.g. `C:\Tools\f4-crash-doctor`.
2. Open a terminal (PowerShell) in that folder.
3. Run:

   ```powershell
   uv sync
   ```

   This downloads the few libraries the tool needs. It takes a few seconds and only needs to be done once.

## Hook it up to Claude Desktop

1. Open your Claude Desktop config file. It lives at:

   ```
   %APPDATA%\Claude\claude_desktop_config.json
   ```

   (Paste that path into the File Explorer address bar. If the file doesn't exist, create it.)

2. Add this block (or merge `f4-crash-doctor` into an existing `mcpServers` section):

   ```json
   {
     "mcpServers": {
       "f4-crash-doctor": {
         "command": "uv",
         "args": ["run", "--directory", "C:\\path\\to\\f4-crash-doctor", "f4-crash-doctor"]
       }
     }
   }
   ```

   **Replace `C:\\path\\to\\f4-crash-doctor`** with the real path to this folder, keeping the double backslashes (e.g. `C:\\Tools\\f4-crash-doctor`).

   > **If the server never shows up in Claude** (the most common setup hiccup): Claude Desktop may not find `uv` right after you install it, because the updated PATH hasn't reached Claude yet. The reliable fix is to use the **full path to uv.exe** as the command instead of just `"uv"`:
   >
   > ```json
   > "command": "C:\\Users\\<your-username>\\.local\\bin\\uv.exe"
   > ```
   >
   > (That's where both installers put it. To confirm, run `where.exe uv` in PowerShell.) Alternatively, sign out of Windows and back in — or reboot — so the new PATH reaches every program, and keep `"command": "uv"`.

3. Restart Claude Desktop (fully quit from the system tray, then reopen).

## Hook it up to Claude Code

One command:

```powershell
claude mcp add f4-crash-doctor -- uv run --directory C:\path\to\f4-crash-doctor f4-crash-doctor
```

Again, replace the path with the real location of this folder.

## First use

After your next crash, just ask:

> **"Diagnose my last Fallout 4 crash."**

Claude should call `scan_game_environment` and `diagnose_crash` and walk you through what happened and what to do about it.

For a guided, step-by-step diagnosis workflow, use the built-in prompt **`diagnose-fallout4-crash`** (in Claude Desktop it appears in the prompts/attachments menu once the server is connected).

## Optional: Nexus Mods lookups

Two tools (`lookup_mod` and `check_nexus_auth`) can fetch mod info from Nexus Mods, which needs a free personal API key:

1. Get your key from nexusmods.com → your avatar → **Settings** → **API Keys**.
2. Provide it one of two ways:
   - Create a file named `.env` in the repository folder containing:

     ```
     NEXUS_API_KEY=your-key-here
     ```

   - Or add it to the Claude Desktop JSON block:

     ```json
     "f4-crash-doctor": {
       "command": "uv",
       "args": ["run", "--directory", "C:\\path\\to\\f4-crash-doctor", "f4-crash-doctor"],
       "env": { "NEXUS_API_KEY": "your-key-here" }
     }
     ```

**Everything else works without a key** — only the two Nexus tools need it.

## The tools (all read-only)

| Tool | What it does |
| --- | --- |
| `list_crash_logs` | Lists your Buffout 4 crash logs, newest first |
| `get_crash_log` | Reads and parses one crash log (newest by default) |
| `get_load_order` | Reads your plugin load order and cross-checks it against your Data folder |
| `scan_game_environment` | Checks game version, F4SE, Buffout 4, Address Library, and which mod manager you use |
| `diagnose_crash` | The full pipeline: parses the log, scans your setup, and matches known crash signatures |
| `lookup_mod` | Looks up a mod on Nexus Mods (needs API key) |
| `check_nexus_auth` | Confirms your Nexus API key works (needs API key) |

## Troubleshooting

- **Dragging the `.mcpb` onto Extensions shows a 🚫 no-drop cursor** — your Windows has no `.mcpb`
  file association, so the drop (and double-clicking the file) has nowhere to go. Use
  **Settings → Extensions → Advanced settings → Install Extension…** instead — it always works.
- **The server doesn't appear in Claude / never connects** — almost always Claude Desktop failing to find `uv`. Use the full path to `uv.exe` as the `"command"` (see the note in the install section above: usually `C:\\Users\\<your-username>\\.local\\bin\\uv.exe`), or sign out of Windows and back in so the freshly installed `uv` is on every program's PATH. Also double-check the config file is valid JSON (a missing comma is enough to break it) and that you fully quit Claude Desktop from the system tray before reopening.
- **"No crash logs found"** — Buffout 4 hasn't logged a crash yet. The folder `Documents\My Games\Fallout4\F4SE\Crash Logs` is only created the first time the game actually crashes with Buffout 4 installed. If the game has crashed but there's still no log, double-check Buffout 4 is installed and loading.
- **OneDrive moved my Documents folder** — handled automatically; the tool asks Windows where your real Documents folder is. If it still looks in the wrong place, set the environment variable `F4_DOCS_DIR` to your Documents path (the one containing `My Games`).
- **Game installed somewhere unusual** — the tool finds Steam libraries automatically, but if your Fallout 4 lives somewhere it can't find, set `F4_GAME_ROOT` to the folder containing `Fallout4.exe`.

## For developers

```powershell
uv sync          # install dependencies (note: needs src/f4_crash_doctor present; harmless to re-run)
uv run pytest    # run the test suite
```

Source layout: `src/f4_crash_doctor/` (package), `tests/` (pytest), `src/f4_crash_doctor/data/` (signature knowledge pack, calibration profile, prompt kit — data, not code), `packaging/` (MCPB bundle build — `uv run python packaging/build_mcpb.py`).
