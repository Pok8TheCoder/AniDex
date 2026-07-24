"""AnimePahe play URL parsing, metadata, and stream resolution."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from anidex.services.pahe_browser import PAHE_HOME, browser_fetch, browser_resolve_kwik_m3u8

PLAY_URL_RE = re.compile(
    r"animepahe\.(?:pw|com|org)/play/"
    r"([0-9a-f-]{36})/([0-9a-f]{64})",
    re.I,
)

KWIK_URL_RE = re.compile(
    r"https://kwik\.[a-z]+/e/[A-Za-z0-9]+",
    re.I,
)

SOURCE_RE = re.compile(
    r'data-src="(https://kwik[^"]+)"[^>]*data-fansub="([^"]*)"[^>]*'
    r'data-resolution="(\d+)"[^>]*data-audio="([^"]*)"',
    re.I,
)

SOURCE_RE_ALT = re.compile(
    r'data-resolution="(\d+)"[^>]*data-audio="([^"]*)"[^>]*'
    r'data-fansub="([^"]*)"[^>]*data-src="(https://kwik[^"]+)"',
    re.I,
)

# Flexible: any button/div that has both data-src (kwik) and data-audio
SOURCE_BLOCK_RE = re.compile(
    r'<(?:button|a|div)[^>]*\bdata-src="(https://kwik[^"]+)"[^>]*>',
    re.I,
)

TITLE_EP_RE = re.compile(
    r"^(?P<title>.+?)\s+Ep\.?\s*(?P<ep>\d+(?:\.\d+)?)\s*::\s*animepahe",
    re.I,
)

H1_EP_RE = re.compile(
    r"Watch\s+(?P<title>.+?)\s*[-–]\s*(?P<ep>\d+(?:\.\d+)?)\s*Online",
    re.I,
)

SEASON_RE = re.compile(
    r"(?P<title>.+?)\s+(?:"
    r"Season\s+(?P<n>\d+)"
    r"|S(?P<s>\d+)\b"
    r"|(?P<ord>\d+)(?:st|nd|rd|th)\s+Season"
    r"|Part\s+(?P<p>\d+)"
    r")\s*$",
    re.I,
)


@dataclass
class PaheSource:
    url: str
    resolution: int
    audio: str
    fansub: str

    @property
    def label(self) -> str:
        return f"{self.fansub} {self.resolution}p {self.audio.upper()}".strip()


@dataclass
class PahePlayMeta:
    anime_title: str
    episode: float | None
    episode2: float | None = None
    episode_title: str = ""
    season: int | None = None
    anime_session: str = ""
    episode_session: str = ""
    total_episodes: int | None = None
    disc: str = ""
    snapshot: str = ""

    @property
    def display(self) -> str:
        title = self.anime_title or "Unknown"
        if self.episode is None:
            return title
        ep = _fmt_ep(self.episode)
        if self.episode2 and self.episode2 > self.episode:
            ep = f"{ep}-{_fmt_ep(self.episode2)}"
        if self.season:
            text = f"{title} - S{self.season:02d}E{ep}"
        else:
            text = f"{title} - Episode {ep}"
        if self.episode_title:
            text = f'{text} "{self.episode_title}"'
        return text

    def filename_stem(self, source: PaheSource | None = None) -> str:
        title = self.anime_title or "episode"
        if self.episode is not None:
            ep = _fmt_ep(self.episode)
            if self.episode2 and self.episode2 > self.episode:
                ep = f"{ep}-{_fmt_ep(self.episode2)}"
            if self.season:
                stem = f"{title} - S{self.season:02d}E{ep}"
            else:
                stem = f"{title} - E{ep}"
        else:
            stem = title
        if source and source.label.strip():
            stem = f"{stem} [{source.label}]"
        return stem


@dataclass
class ResolvedStream:
    m3u8: str
    source: PaheSource | None
    referer: str
    meta: PahePlayMeta | None = None


@dataclass
class DownloadResult:
    path: Path
    meta: PahePlayMeta | None = None
    source: PaheSource | None = None


def _fmt_ep(n: float) -> str:
    if float(n).is_integer():
        return f"{int(n):02d}"
    return str(n).rstrip("0").rstrip(".")


def parse_play_url(text: str) -> tuple[str, str]:
    m = PLAY_URL_RE.search(text.strip())
    if not m:
        raise ValueError(
            "Expected an AnimePahe play URL, e.g.\n"
            "https://animepahe.pw/play/<anime-session>/<episode-session>"
        )
    return m.group(1).lower(), m.group(2).lower()


def is_kwik_url(text: str) -> bool:
    return bool(KWIK_URL_RE.search(text.strip()))


def _parse_season(title: str) -> tuple[str, int | None]:
    m = SEASON_RE.search(title.strip())
    if not m:
        return title.strip(), None
    n = m.group("n") or m.group("s") or m.group("ord") or m.group("p")
    try:
        season = int(n) if n else None
    except ValueError:
        season = None
    base = m.group("title").strip(" -–")
    return base or title.strip(), season


def parse_play_meta(html: str, *, play_url: str = "") -> PahePlayMeta:
    anime_session = ""
    episode_session = ""
    if play_url:
        try:
            anime_session, episode_session = parse_play_url(play_url)
        except ValueError:
            pass

    title = ""
    episode: float | None = None
    season: int | None = None
    total: int | None = None

    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    page_title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    tm = TITLE_EP_RE.search(page_title)
    if tm:
        title = tm.group("title").strip()
        episode = float(tm.group("ep"))

    if not title or episode is None:
        h1 = ""
        for hm in re.finditer(r"<h1[^>]*>([\s\S]*?)</h1>", html, re.I):
            h1 = re.sub(r"<[^>]+>", " ", hm.group(1))
            h1 = re.sub(r"\s+", " ", h1).strip()
            if h1:
                break
        hm = H1_EP_RE.search(h1)
        if hm:
            title = title or hm.group("title").strip()
            if episode is None:
                episode = float(hm.group("ep"))

    tm_total = re.search(r"(\d+)\s+Episodes?", html, re.I)
    if tm_total:
        try:
            total = int(tm_total.group(1))
        except ValueError:
            total = None

    if title:
        title, season = _parse_season(title)

    return PahePlayMeta(
        anime_title=title,
        episode=episode,
        season=season,
        anime_session=anime_session,
        episode_session=episode_session,
        total_episodes=total,
    )


def enrich_meta_from_release_api(
    meta: PahePlayMeta,
    api_json: str | dict,
) -> PahePlayMeta:
    """Merge episode fields from AnimePahe /api?m=release response."""
    try:
        data = json.loads(api_json) if isinstance(api_json, str) else api_json
    except Exception:
        return meta
    rows = data.get("data") or []
    match = None
    if meta.episode_session:
        for row in rows:
            if str(row.get("session", "")).lower() == meta.episode_session.lower():
                match = row
                break
    if match is None and meta.episode is not None:
        for row in rows:
            try:
                if float(row.get("episode", -1)) == float(meta.episode):
                    match = row
                    break
            except (TypeError, ValueError):
                continue
    if not match:
        if data.get("total") and not meta.total_episodes:
            try:
                meta.total_episodes = int(data["total"])
            except (TypeError, ValueError):
                pass
        return meta

    try:
        meta.episode = float(match.get("episode", meta.episode or 0)) or meta.episode
    except (TypeError, ValueError):
        pass
    try:
        ep2 = float(match.get("episode2") or 0)
        meta.episode2 = ep2 if ep2 > 0 else None
    except (TypeError, ValueError):
        pass
    meta.episode_title = (match.get("title") or meta.episode_title or "").strip()
    meta.disc = (match.get("disc") or meta.disc or "").strip()
    meta.snapshot = (match.get("snapshot") or meta.snapshot or "").strip()
    meta.episode_session = str(
        match.get("session") or meta.episode_session or ""
    ).lower()
    if data.get("total") and not meta.total_episodes:
        try:
            meta.total_episodes = int(data["total"])
        except (TypeError, ValueError):
            pass
    return meta


def parse_sources(html: str) -> list[PaheSource]:
    sources: list[PaheSource] = []
    seen: set[str] = set()

    def add(url: str, fansub: str, res: int, audio: str) -> None:
        if url in seen:
            return
        seen.add(url)
        sources.append(
            PaheSource(url=url, resolution=int(res), audio=audio, fansub=fansub)
        )

    for url, fansub, res, audio in SOURCE_RE.findall(html):
        add(url, fansub, int(res), audio)
    for res, audio, fansub, url in SOURCE_RE_ALT.findall(html):
        add(url, fansub, int(res), audio)

    # Attribute-order-agnostic parse of each kwik button/tag
    for m in SOURCE_BLOCK_RE.finditer(html):
        tag = m.group(0)
        url = m.group(1)
        if url in seen:
            continue
        audio_m = re.search(r'data-audio="([^"]*)"', tag, re.I)
        res_m = re.search(r'data-resolution="(\d+)"', tag, re.I)
        fansub_m = re.search(r'data-fansub="([^"]*)"', tag, re.I)
        if not audio_m and not res_m:
            continue
        add(
            url,
            fansub_m.group(1) if fansub_m else "",
            int(res_m.group(1)) if res_m else 0,
            audio_m.group(1) if audio_m else "",
        )

    if not sources:
        for url in KWIK_URL_RE.findall(html):
            add(url, "", 0, "")
    sources.sort(key=lambda s: s.resolution, reverse=True)
    return sources


def _norm_audio(audio: str) -> str:
    a = (audio or "").strip().casefold()
    if a in ("eng", "en", "english", "dub"):
        return "eng"
    if a in ("jpn", "jp", "jap", "japanese", "sub"):
        return "jpn"
    return a


def pick_source(
    sources: list[PaheSource],
    *,
    resolution: int = 1080,
    audio: str = "jpn",
) -> PaheSource:
    if not sources:
        raise RuntimeError("No Kwik sources found on the play page.")
    want = _norm_audio(audio)
    by_audio = [s for s in sources if _norm_audio(s.audio) == want]
    if not by_audio:
        available = sorted(
            {
                (_norm_audio(s.audio) or s.audio or "?").upper()
                for s in sources
            }
        )
        raise RuntimeError(
            f"No {want.upper()} audio on this episode. "
            f"Available: {', '.join(available) or 'unknown'}."
        )
    for s in by_audio:
        if s.resolution == resolution:
            return s
    # Closest resolution at or below requested, else highest for that audio
    below = [s for s in by_audio if s.resolution and s.resolution <= resolution]
    if below:
        return max(below, key=lambda s: s.resolution)
    return max(by_audio, key=lambda s: s.resolution or 0)


def fetch_play_html(play_url: str) -> str:
    return browser_fetch(play_url, referer=PAHE_HOME, kwik=False)


def resolve_kwik(kwik_url: str, *, referer: str = PAHE_HOME) -> str:
    return browser_resolve_kwik_m3u8(kwik_url, referer=referer)


def resolve_play_url(
    play_url: str,
    *,
    resolution: int = 1080,
    audio: str = "jpn",
) -> ResolvedStream:
    html = fetch_play_html(play_url.strip())
    meta = parse_play_meta(html, play_url=play_url.strip())
    sources = parse_sources(html)
    src = pick_source(sources, resolution=resolution, audio=audio)
    m3u8 = resolve_kwik(src.url, referer=play_url.strip())
    return ResolvedStream(
        m3u8=m3u8,
        source=src,
        referer=src.url.rsplit("/", 1)[0] + "/",
        meta=meta,
    )


def resolve_input(
    text: str,
    *,
    resolution: int = 1080,
    audio: str = "jpn",
) -> ResolvedStream:
    text = text.strip()
    if is_kwik_url(text):
        m3u8 = resolve_kwik(text)
        return ResolvedStream(m3u8=m3u8, source=None, referer=text)
    return resolve_play_url(text, resolution=resolution, audio=audio)
