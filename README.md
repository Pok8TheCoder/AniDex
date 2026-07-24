# AniDex

Desktop anime & manga tracker. **Local SQLite is source of truth** — your lists stay on this PC. Pull metadata from MAL / MangaDex when needed; optional MAL OAuth + XML import/export.

## Features

- **Anime library** — Watching / Completed / On Hold / Dropped / Plan to Watch
- **Manga library** — Reading / Completed / On Hold / Dropped / Plan to Read
- **Fuzzy search** — MAL (anime) and MangaDex (manga); no full catalog mirror
- **Onboarding** — start empty or import a MAL XML export
- **MAL sync** — XML import/export always; OAuth PKCE pull/push when connected
- **MangaDex chapter PDF** — paste a chapter URL from a manga’s detail panel (original quality via existing exporter)
- **AnimePahe download** — paste play/Kwik URL; browser session bypasses ad player popups
- **Upcoming countdowns** — current (+ next) MAL season timers; **For You** ranks titles on your list / genre taste first, with Soonest / Name sorts

## Setup

### PC (Windows / Linux / macOS)

```powershell
cd d:\Cursor\MangaCrawler
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# AnimePahe downloads (Chrome + Playwright) — skip on phones:
pip install -r requirements-pahe.txt
playwright install chromium
```

### Termux (Android)

Skip Playwright, PyAV, curl_cffi, and **img2pdf** (pulls pikepdf/qpdf — fails to build on Android). Termux runs the web UI + peer sync; anime MP4s and offline manga come from the PC.

```bash
pkg update
pkg install python git
cd ~/git/AniDex
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py --lan
```

Then pair with the PC under **Settings → Peer sync** (same token + each other’s LAN URL).

On PC, for AnimePahe + PDF CLI extras:

```powershell
pip install -r requirements-pahe.txt
# or PDF only: pip install -r requirements-pdf.txt
playwright install chromium
```

## Run

```powershell
python app.py
```

Opens on **localhost only** (`127.0.0.1:8787`). Data lives in `%APPDATA%\AniDex\` (`anidex.db`, cover cache, MAL tokens) — not in this repo.

### Phone / LAN preview

```powershell
python app.py --lan
```

Prints a LAN URL like `http://192.168.x.x:8787/`. On your phone (same Wi‑Fi), open it and log in — default **user** / **pwd** (change in Settings). Passwords are stored with PBKDF2 (never plaintext). Three failed logins locks that IP until you unlock it from Settings on the PC.

### Peer sync (PC ↔ Termux)

Mesh-merge libraries and media between two AniDex instances on the same Wi‑Fi. Progress uses **max** (never silently clobber watched/read progress); list status uses last-write-wins.

1. On **both** devices: `python app.py --lan`
2. Open **Settings → Peer sync** on each device
3. Copy the **sync token** from one device onto the other (must match)
4. Set **Peer URL** to the other device’s LAN URL (e.g. `http://192.168.1.10:8787`)
5. Tap **Sync now** (or run CLI below)

Synced: anime/manga list entries, episode/chapter progress, scores, manga read page positions, anime MP4 downloads, offline manga chapters.

Not synced: HLS stream sessions, MAL OAuth tokens, Cloudflare browser profile.

CLI:

```powershell
python -m anidex.sync identity
python -m anidex.sync set-peer http://192.168.x.x:8787
python -m anidex.sync set-token <same-token-as-peer>
python -m anidex.sync sync
# or:
python -m anidex.sync sync --peer http://192.168.x.x:8787
```

### GitHub / secrets

Do **not** commit:
- `.pahe_browser_profile/` (Cloudflare/browser cookies)
- `output/` downloads
- `.env`, tokens, or any `*.db`

`.gitignore` already excludes these. MAL OAuth tokens and the SQLite DB stay under `%APPDATA%\AniDex\`.

## MAL API (search + OAuth)

1. Open [MAL API config](https://myanimelist.net/apiconfig) and create a client
2. Set **App Redirect URL** to exactly: `http://127.0.0.1:58432/callback`
3. Paste the **Client ID** in AniDex → Settings → Save
4. Use **Connect with OAuth…** for live sync, or **Import XML** anytime

Export your list from MAL’s site if you prefer file-based restore.

## MangaDex PDF CLI (still available)

```powershell
python mangadex_to_pdf.py "https://mangadex.org/chapter/<uuid>/1"
```

## Anime download (AnimePahe)

Requires **Google Chrome** installed (for AnimePahe). Episode downloads use built-in Python HLS code — no separate ffmpeg install.

1. **Settings → Pass AnimePahe Cloudflare** — a Chrome window opens; wait until animepahe.pw loads (skip ad tabs).
2. **Anime tab → Download episode** — paste a play URL like:
   `https://animepahe.pw/play/<anime-session>/<episode-session>`
3. Pick quality/audio → **Download MP4**

AniDex loads pages through a saved Chrome profile (real navigation, not the ad player), resolves Kwik → m3u8, then downloads segments in Python and saves MP4. You can also paste a **Kwik URL** directly.

CLI:

```powershell
python animepahe_download.py "https://animepahe.pw/play/..."
```

After first-time setup **on PC**:

```powershell
playwright install chromium
```

## License

MIT — see [LICENSE](LICENSE). Respect MAL / MangaDex terms and rights holders.
