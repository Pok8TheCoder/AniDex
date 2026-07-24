"""Localhost HLS proxy: curl_cffi fetch + AES decrypt for QMediaPlayer."""

from __future__ import annotations

import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from anidex.services.hls_download import (
    _curl_session,
    _decrypt_aes128_cbc,
    _parse_media_playlist,
    _pick_variant,
    _segment_iv,
)


class HlsProxy:
    """Short-lived proxy that serves a clear (decrypted) media playlist."""

    def __init__(self, m3u8_url: str, *, referer: str) -> None:
        self.m3u8_url = m3u8_url
        self.referer = referer
        self._session = _curl_session(referer)
        self._segments: list[str] = []
        self._key_info: Any = None
        self._key_bytes: bytes | None = None
        self._playlist_body: bytes = b""
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._prepare()

    def _prepare(self) -> None:
        resp = self._session.get(self.m3u8_url, timeout=60)
        if not resp.ok:
            raise RuntimeError(f"Playlist fetch failed ({resp.status_code})")
        playlist_url = self.m3u8_url
        text = resp.text
        if "#EXT-X-STREAM-INF" in text:
            playlist_url = _pick_variant(text, self.m3u8_url)
            resp = self._session.get(playlist_url, timeout=60)
            if not resp.ok:
                raise RuntimeError(f"Variant playlist failed ({resp.status_code})")
            text = resp.text

        segments, key_info, _seq = _parse_media_playlist(text, playlist_url)
        if not segments:
            raise RuntimeError("No segments in HLS playlist.")
        self._segments = segments
        self._key_info = key_info
        if key_info and key_info.method not in (None, "NONE"):
            if key_info.method != "AES-128":
                raise RuntimeError(f"Unsupported HLS encryption: {key_info.method}")
            assert key_info.uri
            kr = self._session.get(key_info.uri, timeout=60)
            if not kr.ok:
                raise RuntimeError(f"Could not fetch stream key ({kr.status_code})")
            self._key_bytes = kr.content

        # Rewrite to clear local playlist (no EXT-X-KEY; segments pre-decrypted).
        out_lines: list[str] = []
        seg_i = 0
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("#EXT-X-KEY"):
                continue
            if line and not line.startswith("#"):
                out_lines.append(f"/seg/{seg_i}.ts")
                seg_i += 1
            else:
                out_lines.append(raw)
        if seg_i != len(self._segments):
            # Fallback: rebuild minimal playlist
            out_lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:10"]
            for i in range(len(self._segments)):
                out_lines.append("#EXTINF:6.0,")
                out_lines.append(f"/seg/{i}.ts")
            out_lines.append("#EXT-X-ENDLIST")
        self._playlist_body = ("\n".join(out_lines) + "\n").encode("utf-8")

    def start(self) -> str:
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
                return

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path
                if path in ("/", "/index.m3u8"):
                    body = proxy._playlist_body
                    self.send_response(200)
                    self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                m = re.match(r"^/seg/(\d+)\.ts$", path)
                if m:
                    idx = int(m.group(1))
                    try:
                        data = proxy._fetch_segment(idx)
                    except Exception as exc:  # noqa: BLE001
                        msg = str(exc).encode("utf-8", errors="replace")
                        self.send_response(502)
                        self.send_header("Content-Type", "text/plain")
                        self.send_header("Content-Length", str(len(msg)))
                        self.end_headers()
                        self.wfile.write(msg)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "video/mp2t")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self.send_error(404)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="AniDex-HlsProxy",
            daemon=True,
        )
        self._thread.start()
        port = self._server.server_address[1]
        return f"http://127.0.0.1:{port}/index.m3u8"

    def _fetch_segment(self, idx: int) -> bytes:
        if idx < 0 or idx >= len(self._segments):
            raise IndexError("segment out of range")
        url = self._segments[idx]
        with self._lock:
            r = self._session.get(url, timeout=120)
        if not r.ok:
            raise RuntimeError(f"{r.status_code} for segment {idx}")
        data = r.content
        key_info = self._key_info
        if self._key_bytes and key_info and key_info.method == "AES-128":
            data = _decrypt_aes128_cbc(
                data, self._key_bytes, _segment_iv(key_info, idx)
            )
        return data

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
            try:
                self._server.server_close()
            except Exception:
                pass
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None


def start_hls_proxy(m3u8_url: str, *, referer: str) -> tuple[HlsProxy, str]:
    proxy = HlsProxy(m3u8_url, referer=referer)
    local_url = proxy.start()
    return proxy, local_url
