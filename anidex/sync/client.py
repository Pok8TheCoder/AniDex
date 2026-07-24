"""Outbound peer sync client."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urljoin

import httpx

from anidex.sync.manifest import apply_batches, build_manifest, diff_manifest, export_entities
from anidex.sync.media import (
    download_url,
    find_media_id_for_key,
    pack_offline_chapter,
    receive_anime_file,
    receive_offline_zip,
    resolve_anime_media_path,
)
from anidex.db.schema import open_repo
from anidex.sync.token import add_sync_log, ensure_sync_identity


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-AniDex-Sync-Token": token,
        "Accept": "application/json",
        "User-Agent": "AniDex-Sync/1.0",
    }


def _upload_media_to_peer(
    client: httpx.Client,
    peer: str,
    headers: dict[str, str],
    push_batches: dict[str, Any],
) -> dict[str, int]:
    """Push local media bytes for keys the peer is missing."""
    uploaded = {"anime": 0, "offline": 0}
    for meta in push_batches.get("media_meta") or []:
        key = meta.get("key") or ""
        mid = find_media_id_for_key(key)
        path = resolve_anime_media_path(mid) if mid else None
        if not path:
            continue
        data = path.read_bytes()
        put_headers = {
            **headers,
            "Content-Type": "application/octet-stream",
            "X-AniDex-Mal-Id": str(meta["mal_id"]),
            "X-AniDex-Episode": "" if meta.get("episode") is None else str(meta["episode"]),
            "X-AniDex-Pahe-Session": meta.get("pahe_episode_session") or "",
            "X-AniDex-Label": meta.get("label") or "",
            "X-AniDex-Filename": meta.get("filename") or path.name,
            "X-AniDex-Title": meta.get("title") or "Anime",
        }
        r = client.put(
            urljoin(peer + "/", "api/sync/media/anime"),
            headers=put_headers,
            content=data,
        )
        r.raise_for_status()
        uploaded["anime"] += 1

    for meta in push_batches.get("offline_meta") or []:
        mid = meta["mangadex_id"]
        cid = meta["chapter_id"]
        conn, repo = open_repo()
        try:
            entry = None
            for e in repo.list_user_manga():
                if e.mangadex_id == mid:
                    entry = e
                    break
            if not entry:
                continue
            data = pack_offline_chapter(entry.user_manga_id, cid)
        finally:
            conn.close()
        if not data:
            continue
        put_headers = {
            **headers,
            "Content-Type": "application/zip",
            "X-AniDex-Chapter-No": meta.get("chapter_no") or "",
            "X-AniDex-Title": meta.get("title") or "",
            "X-AniDex-Quality": meta.get("quality") or "data-saver",
        }
        r = client.put(
            urljoin(peer + "/", f"api/sync/media/offline/{quote(mid)}/{quote(cid)}"),
            headers=put_headers,
            content=data,
        )
        r.raise_for_status()
        uploaded["offline"] += 1
    return uploaded


def run_sync(peer_url: str | None = None) -> dict[str, Any]:
    """Full mesh sync against peer: pull → merge → push → media."""
    ident = ensure_sync_identity()
    peer = (peer_url or ident.get("peer_url") or "").rstrip("/")
    token = ident["sync_token"]
    if not peer:
        raise ValueError("No peer URL configured")
    if not token:
        raise ValueError("No sync token")

    headers = _headers(token)
    result: dict[str, Any] = {
        "peer": peer,
        "pulled": {},
        "pushed": {},
        "media_downloaded": 0,
        "offline_downloaded": 0,
        "media_uploaded": 0,
        "offline_uploaded": 0,
        "ok": True,
        "error": "",
    }

    try:
        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            hello = client.get(urljoin(peer + "/", "api/sync/hello"), headers=headers)
            hello.raise_for_status()
            remote_manifest = client.get(
                urljoin(peer + "/", "api/sync/manifest"), headers=headers
            )
            remote_manifest.raise_for_status()
            remote = remote_manifest.json()
            local = build_manifest()

            need = diff_manifest(local, remote)
            pulled_counts: dict[str, int] = {}
            if need:
                pull = client.post(
                    urljoin(peer + "/", "api/sync/pull"),
                    headers=headers,
                    json={"keys": need},
                )
                pull.raise_for_status()
                batches = pull.json().get("batches") or {}
                pulled_counts = apply_batches(batches)
                result["pulled"] = pulled_counts

                # Media files from peer
                for meta in batches.get("media_meta") or []:
                    key = meta.get("key") or ""
                    local_id = find_media_id_for_key(key)
                    if local_id and resolve_anime_media_path(local_id):
                        continue
                    # key may already include "media:" prefix
                    path_key = key if key.startswith("media:") else f"media:{key}"
                    url = urljoin(
                        peer + "/",
                        f"api/sync/media/anime/{quote(path_key, safe='/:')}",
                    )
                    data = download_url(url, headers=headers)
                    receive_anime_file(
                        mal_id=int(meta["mal_id"]),
                        episode=meta.get("episode"),
                        pahe_episode_session=meta.get("pahe_episode_session") or "",
                        label=meta.get("label") or "",
                        filename=meta.get("filename") or f"{meta['mal_id']}.mp4",
                        data=data,
                        title=meta.get("title") or "Anime",
                    )
                    result["media_downloaded"] += 1

                for meta in batches.get("offline_meta") or []:
                    mid = meta["mangadex_id"]
                    cid = meta["chapter_id"]
                    url = urljoin(
                        peer + "/",
                        f"api/sync/media/offline/{quote(mid)}/{quote(cid)}",
                    )
                    try:
                        data = download_url(url, headers=headers)
                    except Exception:
                        continue
                    receive_offline_zip(
                        mangadex_id=mid,
                        chapter_id=cid,
                        chapter_no=meta.get("chapter_no") or "",
                        title=meta.get("title") or "",
                        quality=meta.get("quality") or "data-saver",
                        data=data,
                    )
                    result["offline_downloaded"] += 1

            # Push our side (what peer is missing / behind)
            local2 = build_manifest()
            peer_need = diff_manifest(remote, local2)
            if peer_need:
                push_batches = export_entities(peer_need)
                push = client.post(
                    urljoin(peer + "/", "api/sync/push"),
                    headers=headers,
                    json={"batches": push_batches},
                )
                push.raise_for_status()
                result["pushed"] = push.json().get("counts") or {}
                uploaded = _upload_media_to_peer(client, peer, headers, push_batches)
                result["media_uploaded"] = uploaded["anime"]
                result["offline_uploaded"] = uploaded["offline"]

        add_sync_log(
            peer=peer,
            direction="bidirectional",
            message="Sync completed",
            counts={
                "pulled": result["pulled"],
                "pushed": result["pushed"],
                "media_downloaded": result["media_downloaded"],
                "offline_downloaded": result["offline_downloaded"],
                "media_uploaded": result["media_uploaded"],
                "offline_uploaded": result["offline_uploaded"],
            },
            ok=True,
        )
    except Exception as exc:  # noqa: BLE001
        result["ok"] = False
        result["error"] = str(exc)
        add_sync_log(
            peer=peer,
            direction="bidirectional",
            message=str(exc),
            counts=result,
            ok=False,
        )
        raise
    return result
