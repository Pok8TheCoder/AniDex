"""Download HLS (m3u8) streams to MP4 without a system ffmpeg install."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Callable
from pathlib import Path
from typing import Callable as CallableType
from urllib.parse import urljoin, urlparse

ProgressCb = CallableType[[str], None]
ProgressNumCb = CallableType[[int, int], None]

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_IMPERSONATE = "chrome131"


def stream_headers(referer: str) -> dict[str, str]:
    parsed = urlparse(referer)
    origin = (
        f"{parsed.scheme}://{parsed.netloc}"
        if parsed.scheme and parsed.netloc
        else referer
    )
    return {
        "User-Agent": _UA,
        "Referer": referer,
        "Origin": origin,
        "Accept": "*/*",
    }


def _abs_url(base: str, ref: str) -> str:
    return urljoin(base, ref)


def _pick_variant(text: str, base_url: str) -> str:
    lines = text.splitlines()
    best_bw = -1
    best_url = base_url
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXT-X-STREAM-INF"):
            m = re.search(r"BANDWIDTH=(\d+)", line, re.I)
            bw = int(m.group(1)) if m else 0
            if i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                if nxt and not nxt.startswith("#"):
                    if bw >= best_bw:
                        best_bw = bw
                        best_url = _abs_url(base_url, nxt)
                    i += 1
        i += 1
    return best_url


class _KeyInfo:
    __slots__ = ("method", "uri", "iv", "media_sequence")

    def __init__(
        self,
        method: str | None,
        uri: str | None,
        iv: bytes | None,
        media_sequence: int = 0,
    ) -> None:
        self.method = method
        self.uri = uri
        self.iv = iv
        self.media_sequence = media_sequence


def _parse_media_playlist(
    text: str, base_url: str
) -> tuple[list[str], _KeyInfo | None, int]:
    segments: list[str] = []
    key: _KeyInfo | None = None
    seq = 0
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            try:
                seq = int(line.split(":", 1)[1].strip())
            except ValueError:
                seq = 0
        elif line.startswith("#EXT-X-KEY"):
            method_m = re.search(r"METHOD=([^,\s]+)", line, re.I)
            uri_m = re.search(r'URI="([^"]+)"', line)
            iv_m = re.search(r"IV=0x([0-9a-fA-F]+)", line)
            iv = bytes.fromhex(iv_m.group(1)) if iv_m else None
            key = _KeyInfo(
                method=method_m.group(1).upper() if method_m else None,
                uri=uri_m.group(1) if uri_m else None,
                iv=iv,
                media_sequence=seq,
            )
        elif line and not line.startswith("#"):
            segments.append(_abs_url(base_url, line))
    if key is not None:
        key.media_sequence = seq
        if key.uri:
            key.uri = _abs_url(base_url, key.uri)
    return segments, key, seq


def _segment_iv(key: _KeyInfo, index: int) -> bytes:
    if key.iv is not None:
        return key.iv
    # HLS default IV is the media sequence number of the segment.
    return (key.media_sequence + index).to_bytes(16, byteorder="big")


def _decrypt_aes128_cbc(data: bytes, key: bytes, iv: bytes) -> bytes:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    plain = decryptor.update(data) + decryptor.finalize()
    pad = plain[-1]
    if pad < 1 or pad > 16:
        return plain
    return plain[:-pad]


def _remux_ts_to_mp4(ts_path: Path, mp4_path: Path) -> None:
    import av

    with av.open(str(ts_path)) as in_container, av.open(str(mp4_path), mode="w") as out:
        out_streams: dict[int, av.stream.Stream] = {}
        for in_stream in in_container.streams:
            if in_stream.type not in ("video", "audio"):
                continue
            if hasattr(out, "add_stream_from_template"):
                out_streams[in_stream.index] = out.add_stream_from_template(in_stream)
            else:
                out_streams[in_stream.index] = out.add_stream(template=in_stream)
        if not out_streams:
            raise RuntimeError("No audio/video streams found in downloaded data.")
        for packet in in_container.demux():
            if packet.stream is None or packet.dts is None:
                continue
            out_stream = out_streams.get(packet.stream.index)
            if out_stream is None:
                continue
            packet.stream = out_stream
            out.mux(packet)


def _curl_session(referer: str):
    from curl_cffi import requests as creq

    session = creq.Session(impersonate=_IMPERSONATE)
    session.headers.update(stream_headers(referer))
    return session


def download_hls_curl(
    m3u8_url: str,
    output: Path,
    *,
    referer: str,
    on_log: ProgressCb | None = None,
    on_progress: ProgressNumCb | None = None,
    workers: int = 6,
) -> Path:
    """Download HLS with Chrome TLS fingerprint (required by uwucdn)."""

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    def progress(done: int, total: int) -> None:
        if on_progress and total > 0:
            on_progress(done, total)

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    ts_path = output.with_name(output.stem + ".part.ts")

    session = _curl_session(referer)
    log("Fetching playlist...")
    progress(0, 100)
    resp = session.get(m3u8_url, timeout=60)
    if not resp.ok:
        raise RuntimeError(f"{resp.status_code} Forbidden for url: {m3u8_url}")
    playlist_url = m3u8_url
    playlist_text = resp.text

    if "#EXT-X-STREAM-INF" in playlist_text:
        playlist_url = _pick_variant(playlist_text, m3u8_url)
        resp = session.get(playlist_url, timeout=60)
        if not resp.ok:
            raise RuntimeError(f"{resp.status_code} Forbidden for url: {playlist_url}")
        playlist_text = resp.text

    segments, key_info, _seq = _parse_media_playlist(playlist_text, playlist_url)
    if not segments:
        raise RuntimeError("No video segments found in m3u8 playlist.")

    key_bytes: bytes | None = None
    if key_info and key_info.method not in (None, "NONE"):
        if key_info.method != "AES-128":
            raise RuntimeError(f"Unsupported HLS encryption: {key_info.method}")
        log("Fetching decryption key...")
        assert key_info.uri
        kr = session.get(key_info.uri, timeout=60)
        if not kr.ok:
            raise RuntimeError(f"Could not fetch stream key ({kr.status_code})")
        key_bytes = kr.content

    total = len(segments)
    log(f"Downloading {total} segments...")
    progress(0, total)
    parts: list[bytes | None] = [None] * total

    def fetch_one(idx: int, url: str) -> tuple[int, bytes]:
        r = session.get(url, timeout=120)
        if not r.ok:
            raise RuntimeError(f"{r.status_code} Forbidden for url: {url}")
        data = r.content
        if key_bytes and key_info and key_info.method == "AES-128":
            data = _decrypt_aes128_cbc(data, key_bytes, _segment_iv(key_info, idx))
        return idx, data

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(fetch_one, i, url) for i, url in enumerate(segments)]
        for fut in as_completed(futures):
            idx, data = fut.result()
            parts[idx] = data
            done += 1
            progress(done, total)
            if done == 1 or done == total or done % max(1, total // 10) == 0:
                log(f"Segments {done}/{total}")

    with ts_path.open("wb") as out:
        for chunk in parts:
            assert chunk is not None
            out.write(chunk)

    return finalize_ts_to_mp4(ts_path, output, on_log=on_log)


def write_segments_to_ts(
    segments: list[str],
    bodies: dict[str, bytes],
    ts_path: Path,
    *,
    key_info: _KeyInfo | None = None,
    key_bytes: bytes | None = None,
    on_log: ProgressCb | None = None,
    on_progress: ProgressNumCb | None = None,
) -> None:
    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    missing = [u for u in segments if u not in bodies]
    if missing:
        raise RuntimeError(
            f"Missing {len(missing)}/{len(segments)} video segments from browser."
        )

    ts_path = Path(ts_path)
    ts_path.parent.mkdir(parents=True, exist_ok=True)
    total = len(segments)
    log(f"Writing {total} segments...")
    with ts_path.open("wb") as out:
        for i, url in enumerate(segments):
            data = bodies[url]
            if key_bytes and key_info and key_info.method == "AES-128":
                data = _decrypt_aes128_cbc(data, key_bytes, _segment_iv(key_info, i))
            out.write(data)
            if on_progress:
                on_progress(i + 1, total)
            if i == 0 or i + 1 == total or (i + 1) % max(1, total // 10) == 0:
                log(f"Segments {i + 1}/{total}")


def download_hls(
    m3u8_url: str,
    output: Path,
    *,
    referer: str,
    on_log: ProgressCb | None = None,
    on_progress: ProgressNumCb | None = None,
) -> Path:
    return download_hls_curl(
        m3u8_url,
        Path(output),
        referer=referer,
        on_log=on_log,
        on_progress=on_progress,
    )


def finalize_ts_to_mp4(
    ts_path: Path, output: Path, *, on_log: ProgressCb | None = None
) -> Path:
    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    output = Path(output)
    log("Writing MP4...")
    try:
        _remux_ts_to_mp4(ts_path, output)
        ts_path.unlink(missing_ok=True)
    except ImportError:
        ts_out = output.with_suffix(".ts")
        ts_path.replace(ts_out)
        raise RuntimeError(
            "Video downloaded but remux package missing. Run: pip install av"
        ) from None
    except Exception as exc:
        ts_out = output.with_suffix(".ts")
        if not ts_out.exists():
            ts_path.replace(ts_out)
        raise RuntimeError(
            f"Downloaded segments but MP4 remux failed; saved {ts_out.name} instead."
        ) from exc
    log(f"Saved {output}")
    return output
