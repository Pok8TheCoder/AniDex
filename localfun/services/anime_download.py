"""Download AnimePahe streams to local MP4 (pure Python HLS downloader)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from localfun.paths import output_dir
from localfun.services.animepahe import DownloadResult, PahePlayMeta
from localfun.services.pahe_browser import browser_download_episode

ProgressCb = Callable[[str], None]
ProgressNumCb = Callable[[int, int], None]
MetaCb = Callable[[PahePlayMeta], None]


def safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name or "episode"


def download_episode(
    url: str,
    *,
    output: Path | None = None,
    out_dir: Path | None = None,
    resolution: int = 1080,
    audio: str = "jpn",
    on_log: ProgressCb | None = None,
    on_progress: ProgressNumCb | None = None,
    on_meta: MetaCb | None = None,
) -> DownloadResult:
    root = out_dir or (output_dir() / "anime")
    root.mkdir(parents=True, exist_ok=True)
    output = Path(output) if output is not None else None

    return browser_download_episode(
        url,
        output,
        out_dir=root,
        resolution=resolution,
        audio=audio,
        on_log=on_log,
        on_progress=on_progress,
        on_meta=on_meta,
    )
