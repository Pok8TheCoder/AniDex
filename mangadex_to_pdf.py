#!/usr/bin/env python3
"""Download MangaDex chapters at original quality and build PDFs."""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path

import requests

API = "https://api.mangadex.org"
UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)
CHAPTER_URL_RE = re.compile(
    r"mangadex\.org/chapter/([0-9a-f-]{36})",
    re.I,
)

ProgressCb = Callable[[int, int, str], None]

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "AniDex/1.0 (personal archival; +local)",
        "Accept": "application/json,image/*",
    }
)


def parse_chapter_id(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Empty chapter URL/ID")
    m = CHAPTER_URL_RE.search(value)
    if m:
        return m.group(1).lower()
    m = UUID_RE.fullmatch(value)
    if m:
        return m.group(0).lower()
    raise ValueError(
        "Expected a MangaDex chapter URL or UUID, e.g. "
        "https://mangadex.org/chapter/<uuid>/1"
    )


def api_get(path: str, **params) -> dict:
    r = SESSION.get(f"{API}{path}", params=params or None, timeout=60)
    r.raise_for_status()
    data = r.json()
    if data.get("result") == "error":
        raise RuntimeError(data)
    return data


def chapter_meta(chapter_id: str) -> tuple[str, str, str, int | None]:
    data = api_get(
        f"/chapter/{chapter_id}",
        **{"includes[]": ["manga", "scanlation_group"]},
    )["data"]
    attrs = data["attributes"]
    chapter_no = attrs.get("chapter") or "?"
    chapter_title = attrs.get("title") or ""
    pages = attrs.get("pages")
    manga_title = "Unknown"
    for rel in data.get("relationships", []):
        if rel.get("type") == "manga":
            titles = (rel.get("attributes") or {}).get("title") or {}
            manga_title = (
                titles.get("en")
                or titles.get("ja-ro")
                or next(iter(titles.values()), "Unknown")
            )
            break
    return manga_title, str(chapter_no), chapter_title, pages


def chapter_label(manga: str, chapter_no: str, chapter_title: str = "") -> str:
    label = f"{manga} - Ch.{chapter_no}"
    if chapter_title:
        label = f"{label} - {chapter_title}"
    return label


def safe_name(text: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .")
    return text or "chapter"


def at_home(chapter_id: str) -> dict:
    return api_get(f"/at-home/server/{chapter_id}")


def page_urls(chapter_id: str, *, quality: str = "data") -> tuple[str, list[str]]:
    info = at_home(chapter_id)
    base = info["baseUrl"].rstrip("/")
    chapter = info["chapter"]
    hash_ = chapter["hash"]
    files = chapter["data"] if quality == "data" else chapter["dataSaver"]
    mode = "data" if quality == "data" else "data-saver"
    urls = [f"{base}/{mode}/{hash_}/{name}" for name in files]
    return hash_, urls


def fetch_bytes(url: str, *, chapter_id: str | None = None, quality: str = "data") -> bytes:
    last_err: Exception | None = None
    current = url
    for attempt in range(3):
        try:
            r = SESSION.get(current, timeout=120)
            if r.status_code == 403 and chapter_id and attempt < 2:
                _, urls = page_urls(chapter_id, quality=quality)
                # Best-effort: match by filename suffix.
                name = url.rsplit("/", 1)[-1]
                match = next((u for u in urls if u.endswith(name)), None)
                if match:
                    current = match
                time.sleep(0.5)
                continue
            r.raise_for_status()
            return r.content
        except requests.RequestException as e:
            last_err = e
            time.sleep(1.0)
    raise RuntimeError(f"Failed to download {url}: {last_err}")


def preview_image_bytes(chapter_id: str, *, quality: str = "data") -> bytes:
    _, urls = page_urls(chapter_id, quality=quality)
    if not urls:
        raise RuntimeError("Chapter has no pages")
    return fetch_bytes(urls[0], chapter_id=chapter_id, quality=quality)


def download_pages(
    chapter_id: str,
    out_dir: Path,
    *,
    quality: str = "data",
    delay: float = 0.25,
    on_progress: ProgressCb | None = None,
) -> list[Path]:
    info = at_home(chapter_id)
    base = info["baseUrl"].rstrip("/")
    chapter = info["chapter"]
    hash_ = chapter["hash"]
    files = chapter["data"] if quality == "data" else chapter["dataSaver"]
    mode = "data" if quality == "data" else "data-saver"

    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    total = len(files)
    for i, filename in enumerate(files, 1):
        url = f"{base}/{mode}/{hash_}/{filename}"
        ext = Path(filename).suffix or (".png" if mode == "data" else ".jpg")
        dest = out_dir / f"{i:03d}{ext}"
        for attempt in range(3):
            try:
                r = SESSION.get(url, timeout=120)
                if r.status_code == 403 and attempt < 2:
                    info = at_home(chapter_id)
                    base = info["baseUrl"].rstrip("/")
                    chapter = info["chapter"]
                    hash_ = chapter["hash"]
                    url = f"{base}/{mode}/{hash_}/{filename}"
                    time.sleep(0.5)
                    continue
                r.raise_for_status()
                dest.write_bytes(r.content)
                paths.append(dest)
                break
            except requests.RequestException as e:
                if attempt == 2:
                    raise
                time.sleep(1.0)
                _ = e
        if on_progress:
            on_progress(i, total, filename)
        if delay:
            time.sleep(delay)
    return paths


def images_to_pdf(images: list[Path], pdf_path: Path) -> None:
    try:
        import img2pdf
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "img2pdf is required for PDF export. Install with:\n"
            "  pip install -r requirements-pdf.txt\n"
            "(Offline chapter downloads save page images and do not need img2pdf.)"
        ) from e
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    with pdf_path.open("wb") as f:
        f.write(img2pdf.convert([str(p) for p in images]))


def export_chapter(
    chapter: str,
    *,
    output: Path | None = None,
    out_root: Path | None = None,
    keep_images: bool = False,
    quality: str = "data",
    delay: float = 0.25,
    on_progress: ProgressCb | None = None,
) -> Path:
    chapter_id = parse_chapter_id(chapter)
    manga, chapter_no, chapter_title, _pages = chapter_meta(chapter_id)
    label = chapter_label(manga, chapter_no, chapter_title)

    root = out_root or (Path(__file__).resolve().parent / "output")
    img_dir = root / "images" / safe_name(f"{manga}_ch{chapter_no}_{chapter_id[:8]}")
    pdf_path = output or (root / f"{safe_name(label)}.pdf")

    images = download_pages(
        chapter_id,
        img_dir,
        quality=quality,
        delay=delay,
        on_progress=on_progress,
    )
    if not images:
        raise RuntimeError("No pages downloaded.")

    images_to_pdf(images, pdf_path)

    if not keep_images:
        for img in images:
            img.unlink(missing_ok=True)
        try:
            img_dir.rmdir()
            img_dir.parent.rmdir()
        except OSError:
            pass

    return pdf_path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="MangaDex chapter -> original-quality PDF"
    )
    p.add_argument("chapter", help="Chapter URL or UUID")
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output PDF path (default: output/<manga> - Ch.<n>.pdf)",
    )
    p.add_argument(
        "--keep-images",
        action="store_true",
        help="Keep downloaded page images under output/images/",
    )
    p.add_argument(
        "--data-saver",
        action="store_true",
        help="Use compressed images instead of original quality",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=0.25,
        help="Seconds between page downloads (default: 0.25)",
    )
    args = p.parse_args(argv)

    quality = "data-saver" if args.data_saver else "data"
    chapter_id = parse_chapter_id(args.chapter)
    manga, chapter_no, chapter_title, _ = chapter_meta(chapter_id)
    label = chapter_label(manga, chapter_no, chapter_title)
    print(f"Chapter ID: {chapter_id}")
    print(f"Title: {label}")
    print(f"Quality: {'original (data)' if quality == 'data' else 'data-saver'}")
    print("Downloading pages...")

    def _progress(i: int, total: int, name: str) -> None:
        print(f"  [{i}/{total}] {name}")

    pdf_path = export_chapter(
        args.chapter,
        output=args.output,
        keep_images=args.keep_images,
        quality=quality,
        delay=args.delay,
        on_progress=_progress,
    )
    print(f"Writing PDF -> {pdf_path}")
    print("Done.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, requests.RequestException) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
