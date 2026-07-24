from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

# MAL XML status integers
_STATUS_TO_INT = {
    "watching": 1,
    "completed": 2,
    "on_hold": 3,
    "dropped": 4,
    "plan_to_watch": 6,
}
_INT_TO_STATUS = {v: k for k, v in _STATUS_TO_INT.items()}
_INT_TO_STATUS[5] = "plan_to_watch"  # legacy


@dataclass
class XmlAnimeRow:
    mal_id: int
    title: str
    episodes: int | None
    list_status: str
    progress: int
    score: int
    updated_at: str | None


def parse_mal_xml(path: Path | str) -> list[XmlAnimeRow]:
    tree = ET.parse(path)
    root = tree.getroot()
    rows: list[XmlAnimeRow] = []
    for anime in root.findall("anime"):
        mal_id = _text_int(anime, "series_animedb_id") or _text_int(anime, "anime_id")
        if not mal_id:
            continue
        title = _text(anime, "series_title") or _text(anime, "anime_title") or ""
        episodes = _text_int(anime, "series_episodes") or _text_int(anime, "anime_num_episodes")
        status_raw = _text(anime, "my_status") or ""
        if status_raw.isdigit():
            list_status = _INT_TO_STATUS.get(int(status_raw), "plan_to_watch")
        else:
            list_status = _normalize_status(status_raw)
        progress = _text_int(anime, "my_watched_episodes") or 0
        score = _text_int(anime, "my_score") or 0
        updated = _text(anime, "my_last_updated") or None
        rows.append(
            XmlAnimeRow(
                mal_id=mal_id,
                title=title,
                episodes=episodes,
                list_status=list_status,
                progress=progress,
                score=score,
                updated_at=updated,
            )
        )
    return rows


def export_mal_xml(
    entries: list[dict],
    *,
    username: str = "AniDex",
    path: Path | str,
) -> Path:
    """Export local list to MAL-compatible anime XML."""
    out = Path(path)
    total = len(entries)
    parts = [
        '<?xml version="1.0" encoding="UTF-8" ?>',
        "<myanimelist>",
        "  <myinfo>",
        f"    <user_name>{escape(username)}</user_name>",
        f"    <user_export_type>1</user_export_type>",
        f"    <user_total_anime>{total}</user_total_anime>",
        "  </myinfo>",
    ]
    for e in entries:
        status = e.get("list_status", "plan_to_watch")
        status_int = _STATUS_TO_INT.get(status, 6)
        mal_id = e.get("mal_id") or 0
        title = escape(str(e.get("title") or ""))
        episodes = e.get("episodes") or 0
        progress = e.get("progress") or 0
        score = e.get("score") or 0
        updated = e.get("updated_at") or datetime.utcnow().strftime("%Y-%m-%d")
        # MAL expects unix timestamp sometimes; string date is accepted on many importers
        parts.extend(
            [
                "  <anime>",
                f"    <series_animedb_id>{mal_id}</series_animedb_id>",
                f"    <series_title>{title}</series_title>",
                f"    <series_type>TV</series_type>",
                f"    <series_episodes>{episodes}</series_episodes>",
                f"    <my_id>0</my_id>",
                f"    <my_watched_episodes>{progress}</my_watched_episodes>",
                f"    <my_start_date>0000-00-00</my_start_date>",
                f"    <my_finish_date>0000-00-00</my_finish_date>",
                f"    <my_rated></my_rated>",
                f"    <my_score>{score}</my_score>",
                f"    <my_dvd></my_dvd>",
                f"    <my_storage></my_storage>",
                f"    <my_status>{status_int}</my_status>",
                f"    <my_comments></my_comments>",
                f"    <my_times_watched>0</my_times_watched>",
                f"    <my_rewatch_value></my_rewatch_value>",
                f"    <my_tags></my_tags>",
                f"    <my_rewatching>0</my_rewatching>",
                f"    <my_rewatching_ep>0</my_rewatching_ep>",
                f"    <my_discuss>1</my_discuss>",
                f"    <my_sns>13</my_sns>",
                f"    <update_on_import>1</update_on_import>",
                f"    <my_last_updated>{escape(str(updated))}</my_last_updated>",
                "  </anime>",
            ]
        )
    parts.append("</myanimelist>")
    out.write_text("\n".join(parts), encoding="utf-8")
    return out


def _text(el: ET.Element, tag: str) -> str:
    child = el.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _text_int(el: ET.Element, tag: str) -> int | None:
    t = _text(el, tag)
    if not t:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def _normalize_status(raw: str) -> str:
    s = raw.strip().lower().replace(" ", "_")
    aliases = {
        "watching": "watching",
        "completed": "completed",
        "on-hold": "on_hold",
        "on_hold": "on_hold",
        "dropped": "dropped",
        "plan_to_watch": "plan_to_watch",
        "plantowatch": "plan_to_watch",
        "plan to watch": "plan_to_watch",
    }
    return aliases.get(s, "plan_to_watch")
