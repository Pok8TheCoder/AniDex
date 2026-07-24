"""Progressive download-cache HLS sessions for AniDex streaming.

Downloads decrypted segments to disk (same path as offline download),
prefers a ±window around the playhead, keeps all cached segs until unload.
"""

from __future__ import annotations

import secrets
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from anidex.paths import app_data_dir
from anidex.services.hls_download import (
    _curl_session,
    _decrypt_aes128_cbc,
    _parse_media_playlist,
    _pick_variant,
    _segment_iv,
)

# Prefetch / seek window around playhead (seconds).
PLAYHEAD_WINDOW_SEC = 120.0
PREFETCH_WORKERS = 6


def stream_cache_root() -> Path:
    path = app_data_dir() / "stream_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _parse_durations(text: str) -> list[float]:
    """EXTINF durations in playlist order (one per media segment)."""
    durs: list[float] = []
    pending: float | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXTINF:"):
            try:
                pending = float(line.split(":", 1)[1].split(",", 1)[0].strip())
            except ValueError:
                pending = 6.0
        elif line and not line.startswith("#"):
            durs.append(pending if pending is not None else 6.0)
            pending = None
    return durs


@dataclass
class HlsStreamSession:
    id: str
    m3u8_url: str
    referer: str
    segments: list[str] = field(default_factory=list)
    durations: list[float] = field(default_factory=list)
    starts: list[float] = field(default_factory=list)  # media time at seg start
    key_info: Any = None
    key_bytes: bytes | None = None
    playlist_body: bytes = b""
    cache_dir: Path | None = None
    created_at: float = field(default_factory=time.time)
    last_access: float = field(default_factory=time.time)
    playhead: float = 0.0
    _http: Any = None
    _seg_locks: list[threading.Lock] = field(default_factory=list)
    _pool: ThreadPoolExecutor | None = None
    _prefetch_lock: threading.Lock = field(default_factory=threading.Lock)
    _closed: bool = False
    _stop: threading.Event = field(default_factory=threading.Event)

    def touch(self) -> None:
        self.last_access = time.time()

    @property
    def duration_total(self) -> float:
        return self.starts[-1] + self.durations[-1] if self.starts else 0.0

    def cached_count(self) -> int:
        if not self.cache_dir:
            return 0
        return sum(1 for i in range(len(self.segments)) if self._cache_path(i).is_file())

    def _cache_path(self, idx: int) -> Path:
        assert self.cache_dir is not None
        return self.cache_dir / f"seg_{idx:05d}.ts"

    def _index_at_time(self, t: float) -> int:
        if not self.starts:
            return 0
        t = max(0.0, t)
        for i, start in enumerate(self.starts):
            end = start + self.durations[i]
            if t < end:
                return i
        return len(self.starts) - 1

    def _window_indices(self, center_sec: float) -> list[int]:
        """Segment indices covering [center-window, center+window], center-first order."""
        if not self.segments:
            return []
        lo_t = max(0.0, center_sec - PLAYHEAD_WINDOW_SEC)
        hi_t = center_sec + PLAYHEAD_WINDOW_SEC
        lo = self._index_at_time(lo_t)
        hi = self._index_at_time(hi_t)
        center = self._index_at_time(center_sec)
        # Prefer near playhead first (ahead slightly weighted).
        order = sorted(range(lo, hi + 1), key=lambda i: (abs(i - center), i < center))
        return order

    def set_playhead(self, seconds: float) -> None:
        if self._closed:
            return
        self.touch()
        self.playhead = max(0.0, float(seconds))
        self.schedule_prefetch(self.playhead)

    def schedule_prefetch(self, center_sec: float) -> None:
        if self._closed or self._pool is None:
            return
        indices = self._window_indices(center_sec)
        for idx in indices:
            if self._cache_path(idx).is_file():
                continue
            self._pool.submit(self._prefetch_one, idx)

    def _prefetch_one(self, idx: int) -> None:
        if self._closed or self._stop.is_set():
            return
        try:
            self.fetch_segment(idx)
        except Exception:
            pass

    def fetch_segment(self, idx: int) -> bytes:
        """Return decrypted segment bytes; download+cache on miss. Keeps file until close."""
        self.touch()
        if idx < 0 or idx >= len(self.segments):
            raise IndexError("segment out of range")
        if self._closed:
            raise RuntimeError("stream session closed")

        path = self._cache_path(idx)
        if path.is_file() and path.stat().st_size > 0:
            return path.read_bytes()

        lock = self._seg_locks[idx]
        with lock:
            if path.is_file() and path.stat().st_size > 0:
                return path.read_bytes()
            url = self.segments[idx]
            r = self._http.get(url, timeout=120)
            if not r.ok:
                raise RuntimeError(f"{r.status_code} for segment {idx}")
            data = r.content
            if self.key_bytes and self.key_info and self.key_info.method == "AES-128":
                data = _decrypt_aes128_cbc(
                    data, self.key_bytes, _segment_iv(self.key_info, idx)
                )
            tmp = path.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(path)
            return data

    def warm_start(self, seconds: float = 2.0) -> None:
        """Block until the first ~seconds of media are cached (parallel)."""
        if not self.segments:
            return
        end_idx = self._index_at_time(seconds)
        idxs = list(range(0, end_idx + 1))
        if self._pool is None:
            for i in idxs:
                self.fetch_segment(i)
            return
        futs = [self._pool.submit(self.fetch_segment, i) for i in idxs]
        for f in futs:
            try:
                f.result(timeout=120)
            except Exception:
                pass

    def close(self) -> None:
        self._closed = True
        self._stop.set()
        if self._pool is not None:
            try:
                self._pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                self._pool.shutdown(wait=False)
            self._pool = None
        if self.cache_dir and self.cache_dir.exists():
            shutil.rmtree(self.cache_dir, ignore_errors=True)


class StreamSessionStore:
    def __init__(self, *, ttl_seconds: float = 3600) -> None:
        self._sessions: dict[str, HlsStreamSession] = {}
        self._lock = threading.Lock()
        self.ttl_seconds = ttl_seconds

    def create(
        self,
        m3u8_url: str,
        *,
        referer: str,
        warm_seconds: float = 2.0,
    ) -> HlsStreamSession:
        sid = secrets.token_urlsafe(12)
        http = _curl_session(referer)
        resp = http.get(m3u8_url, timeout=60)
        if not resp.ok:
            raise RuntimeError(f"Playlist fetch failed ({resp.status_code})")
        playlist_url = m3u8_url
        text = resp.text
        if "#EXT-X-STREAM-INF" in text:
            playlist_url = _pick_variant(text, m3u8_url)
            resp = http.get(playlist_url, timeout=60)
            if not resp.ok:
                raise RuntimeError(f"Variant playlist failed ({resp.status_code})")
            text = resp.text

        segments, key_info, _seq = _parse_media_playlist(text, playlist_url)
        if not segments:
            raise RuntimeError("No segments in HLS playlist.")
        durations = _parse_durations(text)
        if len(durations) != len(segments):
            durations = [6.0] * len(segments)
        starts: list[float] = []
        t = 0.0
        for d in durations:
            starts.append(t)
            t += d

        key_bytes: bytes | None = None
        if key_info and key_info.method not in (None, "NONE"):
            if key_info.method != "AES-128":
                raise RuntimeError(f"Unsupported HLS encryption: {key_info.method}")
            assert key_info.uri
            kr = http.get(key_info.uri, timeout=60)
            if not kr.ok:
                raise RuntimeError(f"Could not fetch stream key ({kr.status_code})")
            key_bytes = kr.content

        out_lines: list[str] = []
        seg_i = 0
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("#EXT-X-KEY"):
                continue
            if line and not line.startswith("#"):
                out_lines.append(f"seg/{seg_i}.ts")
                seg_i += 1
            else:
                out_lines.append(raw)
        if seg_i != len(segments):
            out_lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:10"]
            for i, dur in enumerate(durations):
                out_lines.append(f"#EXTINF:{dur:.3f},")
                out_lines.append(f"seg/{i}.ts")
            out_lines.append("#EXT-X-ENDLIST")

        cache_dir = stream_cache_root() / sid
        cache_dir.mkdir(parents=True, exist_ok=True)

        session = HlsStreamSession(
            id=sid,
            m3u8_url=m3u8_url,
            referer=referer,
            segments=segments,
            durations=durations,
            starts=starts,
            key_info=key_info,
            key_bytes=key_bytes,
            playlist_body=("\n".join(out_lines) + "\n").encode("utf-8"),
            cache_dir=cache_dir,
            _seg_locks=[threading.Lock() for _ in segments],
            _pool=ThreadPoolExecutor(
                max_workers=PREFETCH_WORKERS, thread_name_prefix=f"LFStream-{sid[:6]}"
            ),
        )
        session._http = http

        # Kick ±2min from start in background, then warm first seconds for quick play.
        session.schedule_prefetch(0.0)
        if warm_seconds > 0:
            session.warm_start(warm_seconds)

        with self._lock:
            self._purge_locked()
            self._sessions[sid] = session
        return session

    def get(self, sid: str) -> HlsStreamSession | None:
        with self._lock:
            self._purge_locked()
            session = self._sessions.get(sid)
            if session:
                session.touch()
            return session

    def drop(self, sid: str) -> None:
        with self._lock:
            session = self._sessions.pop(sid, None)
        if session:
            session.close()

    def clear(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for s in sessions:
            s.close()

    def _purge_locked(self) -> None:
        now = time.time()
        dead = [
            sid
            for sid, s in self._sessions.items()
            if now - s.last_access > self.ttl_seconds
        ]
        for sid in dead:
            session = self._sessions.pop(sid, None)
            if session:
                threading.Thread(target=session.close, daemon=True).start()


STREAM_STORE = StreamSessionStore()
