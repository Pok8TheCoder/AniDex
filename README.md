# LocalFun

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

```powershell
cd d:\Cursor\MangaCrawler
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

```powershell
python app.py
```

Opens on **localhost only** (`127.0.0.1:8787`). Data lives in `%APPDATA%\LocalFun\` (`localfun.db`, cover cache, MAL tokens) — not in this repo.

### Phone / LAN preview

```powershell
python app.py --lan
```

Prints a LAN URL like `http://192.168.x.x:8787/`. On your phone (same Wi‑Fi), open it and log in — default **user** / **pwd** (change in Settings). Passwords are stored with PBKDF2 (never plaintext). Three failed logins locks that IP until you unlock it from Settings on the PC.

### GitHub / secrets

Do **not** commit:
- `.pahe_browser_profile/` (Cloudflare/browser cookies)
- `output/` downloads
- `.env`, tokens, or any `*.db`

`.gitignore` already excludes these. MAL OAuth tokens and the SQLite DB stay under `%APPDATA%\LocalFun\`.

## MAL API (search + OAuth)

1. Open [MAL API config](https://myanimelist.net/apiconfig) and create a client
2. Set **App Redirect URL** to exactly: `http://127.0.0.1:58432/callback`
3. Paste the **Client ID** in LocalFun → Settings → Save
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

LocalFun loads pages through a saved Chrome profile (real navigation, not the ad player), resolves Kwik → m3u8, then downloads segments in Python and saves MP4. You can also paste a **Kwik URL** directly.

CLI:

```powershell
python animepahe_download.py "https://animepahe.pw/play/..."
```

After first-time setup:

```powershell
playwright install chromium
```

## License

MIT — see [LICENSE](LICENSE). Respect MAL / MangaDex terms and rights holders.
