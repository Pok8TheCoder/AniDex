"""Conflict merge rules for AniDex peer sync."""

from __future__ import annotations

from typing import Any


def _ts(row: dict[str, Any], key: str = "updated_at") -> str:
    return str(row.get(key) or "")


def lww(local: dict[str, Any], remote: dict[str, Any], *fields: str) -> dict[str, Any]:
    """Last-write-wins on updated_at for selected fields; fill empties from other."""
    out = dict(local)
    remote_newer = _ts(remote) >= _ts(local)
    for f in fields:
        lv, rv = local.get(f), remote.get(f)
        if remote_newer:
            if rv not in (None, ""):
                out[f] = rv
            elif lv not in (None, ""):
                out[f] = lv
        else:
            if lv in (None, "") and rv not in (None, ""):
                out[f] = rv
    out["updated_at"] = max(_ts(local), _ts(remote))
    return out


def merge_list_entry(local: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
    """Merge user_anime / user_manga style rows."""
    out = lww(
        local,
        remote,
        "list_status",
        "notes",
        "local_folder",
        "title",
        "title_english",
        "cover_url",
        "synopsis",
        "media_type",
        "airing_status",
        "status",
        "year",
        "episodes",
        "mean_score",
    )
    # Progress: never go backwards
    lp = int(local.get("progress") or 0)
    rp = int(remote.get("progress") or 0)
    out["progress"] = max(lp, rp)

    ls = int(local.get("score") or 0)
    rs = int(remote.get("score") or 0)
    if ls == 0 and rs > 0:
        out["score"] = rs
    elif rs == 0 and ls > 0:
        out["score"] = ls
    elif _ts(remote) >= _ts(local):
        out["score"] = rs
    else:
        out["score"] = ls

    out["updated_at"] = max(_ts(local), _ts(remote))
    return out


def merge_read_position(local: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
    lp = int(local.get("page_index") or 0)
    rp = int(remote.get("page_index") or 0)
    return {
        "page_index": max(lp, rp),
        "updated_at": max(_ts(local), _ts(remote)),
        "mangadex_id": local.get("mangadex_id") or remote.get("mangadex_id"),
        "chapter_id": local.get("chapter_id") or remote.get("chapter_id"),
    }


def merge_catalog(local: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "title",
        "title_english",
        "media_type",
        "airing_status",
        "status",
        "year",
        "episodes",
        "mean_score",
        "cover_url",
        "synopsis",
    )
    return lww(local, remote, *fields)


def tombstone_wins(tombstone: dict[str, Any], entity_updated_at: str) -> bool:
    return _ts(tombstone, "deleted_at") >= (entity_updated_at or "")
