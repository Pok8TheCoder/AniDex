from __future__ import annotations

import json
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import requests

from anidex.paths import tokens_path

API = "https://api.myanimelist.net/v2"
AUTH = "https://myanimelist.net/v1/oauth2/authorize"
TOKEN = "https://myanimelist.net/v1/oauth2/token"
REDIRECT_URI = "http://127.0.0.1:58432/callback"


@dataclass
class MalAnimeResult:
    mal_id: int
    title: str
    title_english: str | None
    media_type: str | None
    airing_status: str | None
    episodes: int | None
    mean_score: float | None
    cover_url: str | None
    synopsis: str | None
    raw: dict[str, Any]


class MalApiError(RuntimeError):
    pass


def _pkce_verifier() -> str:
    """MAL only supports PKCE method 'plain' (challenge == verifier)."""
    # 43–128 chars; token_urlsafe gives URL-safe alphabet MAL accepts
    verifier = secrets.token_urlsafe(96)
    return verifier[:128] if len(verifier) > 128 else verifier


class MalClient:
    def __init__(self, client_id: str) -> None:
        self.client_id = (client_id or "").strip()
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "AniDex/0.1 (desktop tracker; personal use)"}
        )
        self._tokens: dict[str, Any] = self._load_tokens()

    def _load_tokens(self) -> dict[str, Any]:
        path = tokens_path()
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
        return {}

    def _save_tokens(self) -> None:
        path = tokens_path()
        path.write_text(json.dumps(self._tokens, indent=2), encoding="utf-8")

    def clear_tokens(self) -> None:
        self._tokens = {}
        path = tokens_path()
        if path.exists():
            path.unlink(missing_ok=True)

    @property
    def is_authenticated(self) -> bool:
        return bool(self._tokens.get("access_token"))

    def _ensure_client_id(self) -> None:
        if not self.client_id:
            raise MalApiError(
                "Set your MAL Client ID in Settings "
                "(https://myanimelist.net/apiconfig)."
            )

    def _auth_headers(self, *, user: bool = False) -> dict[str, str]:
        self._ensure_client_id()
        if user:
            self._refresh_if_needed()
            token = self._tokens.get("access_token")
            if not token:
                raise MalApiError("Not connected to MAL. Connect in Settings.")
            return {"Authorization": f"Bearer {token}"}
        return {"X-MAL-CLIENT-ID": self.client_id}

    def _refresh_if_needed(self) -> None:
        expires_at = self._tokens.get("expires_at", 0)
        if time.time() < expires_at - 60:
            return
        refresh = self._tokens.get("refresh_token")
        if not refresh:
            return
        self._ensure_client_id()
        r = self.session.post(
            TOKEN,
            data={
                "client_id": self.client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh,
            },
            timeout=60,
        )
        if r.status_code >= 400:
            self.clear_tokens()
            raise MalApiError(f"MAL token refresh failed: {r.text}")
        data = r.json()
        self._tokens = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", refresh),
            "expires_at": time.time() + int(data.get("expires_in", 3600)),
        }
        self._save_tokens()

    def login_oauth_pkce(self, timeout: float = 180.0) -> str:
        """Open browser for OAuth; return MAL username after success."""
        self._ensure_client_id()
        # MAL docs: only code_challenge_method=plain is supported
        verifier = _pkce_verifier()
        if len(verifier) < 43:
            verifier = (verifier + secrets.token_urlsafe(43))[:128]
        state = secrets.token_urlsafe(16)
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "code_challenge": verifier,
            "code_challenge_method": "plain",
            "redirect_uri": REDIRECT_URI,
            "state": state,
        }

        result: dict[str, str] = {}
        error_box: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                qs = urllib.parse.parse_qs(parsed.query)
                if qs.get("state", [""])[0] != state:
                    error_box.append("Invalid OAuth state")
                elif "error" in qs:
                    error_box.append(qs.get("error_description", qs["error"])[0])
                else:
                    result["code"] = qs.get("code", [""])[0]
                body = b"<html><body><h2>AniDex</h2><p>You can close this window.</p></body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):  # noqa: D401
                return

        server = HTTPServer(("127.0.0.1", 58432), Handler)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        webbrowser.open(f"{AUTH}?{urllib.parse.urlencode(params)}")
        thread.join(timeout=timeout)
        server.server_close()

        if error_box:
            raise MalApiError(error_box[0])
        code = result.get("code")
        if not code:
            raise MalApiError("OAuth timed out or was cancelled.")

        r = self.session.post(
            TOKEN,
            data={
                "client_id": self.client_id,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": verifier,
            },
            timeout=60,
        )
        if r.status_code >= 400:
            raise MalApiError(f"Token exchange failed: {r.text}")
        data = r.json()
        self._tokens = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", ""),
            "expires_at": time.time() + int(data.get("expires_in", 3600)),
        }
        self._save_tokens()
        me = self.get_me()
        return me.get("name", "")

    def get_me(self) -> dict[str, Any]:
        r = self.session.get(
            f"{API}/users/@me",
            headers=self._auth_headers(user=True),
            timeout=60,
        )
        if r.status_code >= 400:
            raise MalApiError(r.text)
        return r.json()

    def search_anime(self, query: str, limit: int = 20) -> list[MalAnimeResult]:
        q = query.strip()
        if not q:
            return []
        fields = (
            "id,title,alternative_titles,main_picture,media_type,status,"
            "num_episodes,mean,synopsis"
        )
        r = self.session.get(
            f"{API}/anime",
            headers=self._auth_headers(user=False),
            params={"q": q, "limit": limit, "fields": fields},
            timeout=60,
        )
        if r.status_code >= 400:
            raise MalApiError(f"MAL search failed: {r.status_code} {r.text}")
        data = r.json()
        out: list[MalAnimeResult] = []
        for node in data.get("data", []):
            n = node.get("node", {})
            alts = n.get("alternative_titles") or {}
            pic = n.get("main_picture") or {}
            out.append(
                MalAnimeResult(
                    mal_id=int(n["id"]),
                    title=n.get("title") or "",
                    title_english=alts.get("en"),
                    media_type=n.get("media_type"),
                    airing_status=n.get("status"),
                    episodes=n.get("num_episodes"),
                    mean_score=n.get("mean"),
                    cover_url=pic.get("large") or pic.get("medium"),
                    synopsis=n.get("synopsis"),
                    raw=n,
                )
            )
        return out

    def get_seasonal_anime(
        self,
        year: int,
        season: str,
        *,
        limit: int = 100,
        offset: int = 0,
        sort: str = "anime_num_list_users",
    ) -> tuple[list[dict[str, Any]], int]:
        """Return (nodes, total) for a TV season. Nodes include genres/broadcast."""
        season = season.lower().strip()
        if season not in {"winter", "spring", "summer", "fall"}:
            raise MalApiError(f"Invalid season: {season}")
        fields = (
            "id,title,alternative_titles,main_picture,media_type,status,"
            "num_episodes,mean,synopsis,start_date,end_date,genres,"
            "start_season,broadcast,num_list_users,rank,popularity"
        )
        r = self.session.get(
            f"{API}/anime/season/{year}/{season}",
            headers=self._auth_headers(user=False),
            params={
                "limit": str(min(500, max(1, limit))),
                "offset": str(max(0, offset)),
                "fields": fields,
                "sort": sort,
                "nsfw": "true",
            },
            timeout=60,
        )
        if r.status_code >= 400:
            raise MalApiError(f"MAL season failed: {r.status_code} {r.text}")
        data = r.json()
        nodes = [item.get("node") or {} for item in data.get("data") or []]
        total = int(data.get("paging", {}).get("previous") and 0 or len(nodes))
        # MAL season paging doesn't always expose total; keep fetching hint via len
        return nodes, len(nodes)

    def get_anime_ranking(
        self,
        ranking_type: str = "bypopularity",
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """MAL ranking feed. Types: all, airing, upcoming, tv, movie, bypopularity, favorite."""
        ranking_type = (ranking_type or "bypopularity").strip().lower()
        allowed = {
            "all",
            "airing",
            "upcoming",
            "tv",
            "ova",
            "movie",
            "special",
            "bypopularity",
            "favorite",
        }
        if ranking_type not in allowed:
            raise MalApiError(f"Invalid ranking type: {ranking_type}")
        fields = (
            "id,title,alternative_titles,main_picture,media_type,status,"
            "num_episodes,mean,synopsis,start_date,genres,"
            "start_season,num_list_users,rank,popularity"
        )
        r = self.session.get(
            f"{API}/anime/ranking",
            headers=self._auth_headers(user=False),
            params={
                "ranking_type": ranking_type,
                "limit": str(min(500, max(1, limit))),
                "offset": str(max(0, offset)),
                "fields": fields,
                "nsfw": "true",
            },
            timeout=60,
        )
        if r.status_code >= 400:
            raise MalApiError(f"MAL ranking failed: {r.status_code} {r.text}")
        data = r.json()
        out: list[dict[str, Any]] = []
        for item in data.get("data") or []:
            node = item.get("node") or {}
            ranking = item.get("ranking") or {}
            if ranking.get("rank") is not None:
                node = dict(node)
                node["_rank"] = ranking.get("rank")
            out.append(node)
        return out

    def get_anime(
        self, mal_id: int, *, fields: str | None = None
    ) -> dict[str, Any]:
        fields = fields or (
            "id,title,alternative_titles,main_picture,media_type,status,"
            "num_episodes,mean,synopsis,start_date,genres,broadcast,start_season"
        )
        r = self.session.get(
            f"{API}/anime/{mal_id}",
            headers=self._auth_headers(user=False),
            params={"fields": fields},
            timeout=60,
        )
        if r.status_code >= 400:
            raise MalApiError(f"MAL get anime failed: {r.status_code} {r.text}")
        return r.json()

    def get_user_animelist(
        self, username: str = "@me", status: str | None = None
    ) -> list[dict[str, Any]]:
        fields = (
            "list_status{status,score,num_episodes_watched,updated_at},"
            "id,title,alternative_titles,main_picture,media_type,status,"
            "num_episodes,mean,synopsis"
        )
        items: list[dict[str, Any]] = []
        url: str | None = f"{API}/users/{username}/animelist"
        params: dict[str, Any] = {"fields": fields, "limit": 100, "nsfw": "true"}
        if status:
            params["status"] = status
        headers = self._auth_headers(user=True)
        while url:
            r = self.session.get(
                url,
                headers=headers,
                params=params if "offset" not in (url or "") else None,
                timeout=60,
            )
            if r.status_code >= 400:
                raise MalApiError(f"Fetch list failed: {r.text}")
            payload = r.json()
            items.extend(payload.get("data", []))
            next_url = (payload.get("paging") or {}).get("next")
            url = next_url
            params = {}
        return items

    def update_list_status(
        self,
        mal_id: int,
        *,
        status: str,
        score: int = 0,
        num_watched_episodes: int = 0,
    ) -> dict[str, Any]:
        r = self.session.put(
            f"{API}/anime/{mal_id}/my_list_status",
            headers=self._auth_headers(user=True),
            data={
                "status": status,
                "score": score,
                "num_watched_episodes": num_watched_episodes,
            },
            timeout=60,
        )
        if r.status_code >= 400:
            raise MalApiError(f"Update list failed: {r.text}")
        return r.json()

    def delete_list_status(self, mal_id: int) -> None:
        r = self.session.delete(
            f"{API}/anime/{mal_id}/my_list_status",
            headers=self._auth_headers(user=True),
            timeout=60,
        )
        if r.status_code >= 400 and r.status_code != 404:
            raise MalApiError(f"Delete list entry failed: {r.text}")
