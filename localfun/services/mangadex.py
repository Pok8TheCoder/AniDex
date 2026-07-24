from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

API = "https://api.mangadex.org"


@dataclass
class MangaDexResult:
    mangadex_id: str
    title: str
    title_english: str | None
    status: str | None
    year: int | None
    cover_url: str | None
    synopsis: str | None
    raw: dict[str, Any]


class MangaDexError(RuntimeError):
    pass


class MangaDexClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "LocalFun/0.1 (desktop tracker; personal use)",
                "Accept": "application/json",
            }
        )

    def search(self, query: str, limit: int = 20) -> list[MangaDexResult]:
        q = query.strip()
        if not q:
            return []
        r = self.session.get(
            f"{API}/manga",
            params=[
                ("title", q),
                ("limit", str(limit)),
                ("includes[]", "cover_art"),
                ("order[relevance]", "desc"),
                ("contentRating[]", "safe"),
                ("contentRating[]", "suggestive"),
                ("contentRating[]", "erotica"),
            ],
            timeout=60,
        )
        if r.status_code >= 400:
            raise MangaDexError(f"MangaDex search failed: {r.status_code}")
        data = r.json()
        out: list[MangaDexResult] = []
        for item in data.get("data", []):
            out.append(self._parse(item))
        return out

    def get_manga(self, mangadex_id: str) -> MangaDexResult:
        r = self.session.get(
            f"{API}/manga/{mangadex_id}",
            params=[("includes[]", "cover_art")],
            timeout=60,
        )
        if r.status_code >= 400:
            raise MangaDexError(f"MangaDex get failed: {r.status_code}")
        return self._parse(r.json()["data"])

    def list_chapters(
        self,
        mangadex_id: str,
        *,
        lang: str = "en",
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return (chapters, total). Chapters sorted ascending by chapter number."""
        params: list[tuple[str, str]] = [
            ("limit", str(min(100, max(1, limit)))),
            ("offset", str(max(0, offset))),
            ("translatedLanguage[]", lang),
            ("order[chapter]", "asc"),
            ("order[publishAt]", "asc"),
            ("includeFutureUpdates", "0"),
            ("contentRating[]", "safe"),
            ("contentRating[]", "suggestive"),
            ("contentRating[]", "erotica"),
            ("contentRating[]", "pornographic"),
        ]
        r = self.session.get(
            f"{API}/manga/{mangadex_id}/feed",
            params=params,
            timeout=60,
        )
        if r.status_code >= 400:
            raise MangaDexError(f"MangaDex feed failed: {r.status_code}")
        data = r.json()
        total = int(data.get("total") or 0)
        out: list[dict[str, Any]] = []
        for item in data.get("data") or []:
            attrs = item.get("attributes") or {}
            out.append(
                {
                    "id": item.get("id"),
                    "chapter": attrs.get("chapter") or "?",
                    "title": attrs.get("title") or "",
                    "pages": attrs.get("pages"),
                    "volume": attrs.get("volume"),
                    "translated_language": attrs.get("translatedLanguage"),
                    "publish_at": attrs.get("publishAt"),
                }
            )
        return out, total

    def fetch_cover_bytes(self, url: str) -> bytes:
        r = self.session.get(
            url,
            headers={"Referer": "https://mangadex.org/"},
            timeout=60,
        )
        if r.status_code >= 400:
            raise MangaDexError(f"Cover download failed: {r.status_code}")
        return r.content

    def _parse(self, item: dict[str, Any]) -> MangaDexResult:
        attrs = item.get("attributes") or {}
        titles = attrs.get("title") or {}
        alt = attrs.get("altTitles") or []
        title = (
            titles.get("en")
            or titles.get("ja-ro")
            or next(iter(titles.values()), "Unknown")
        )
        title_en = titles.get("en")
        for a in alt:
            if "en" in a:
                title_en = title_en or a["en"]
                break
        desc = attrs.get("description") or {}
        synopsis = desc.get("en") or next(iter(desc.values()), None)
        cover_url = None
        for rel in item.get("relationships") or []:
            if rel.get("type") == "cover_art":
                file_name = (rel.get("attributes") or {}).get("fileName")
                if file_name:
                    cover_url = (
                        f"https://uploads.mangadex.org/covers/"
                        f"{item['id']}/{file_name}.256.jpg"
                    )
                break
        return MangaDexResult(
            mangadex_id=item["id"],
            title=title,
            title_english=title_en,
            status=attrs.get("status"),
            year=attrs.get("year"),
            cover_url=cover_url,
            synopsis=synopsis,
            raw=item,
        )
