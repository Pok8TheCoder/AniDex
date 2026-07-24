#!/usr/bin/env python3
"""CLI: download one AnimePahe episode to MP4."""

from __future__ import annotations

import argparse
import sys

from anidex.services.anime_download import download_episode


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="AnimePahe episode -> MP4")
    p.add_argument("url", help="AnimePahe play URL or Kwik embed URL")
    p.add_argument("-o", "--output", type=str, help="Output MP4 path")
    p.add_argument("-r", "--resolution", type=int, default=1080, choices=(360, 480, 720, 1080))
    p.add_argument("-a", "--audio", default="jpn", choices=("jpn", "eng"))
    args = p.parse_args(argv)

    try:
        result = download_episode(
            args.url,
            output=args.output,
            resolution=args.resolution,
            audio=args.audio,
            on_log=print,
        )
        if result.meta:
            print(result.meta.display)
        print(f"Done -> {result.path}")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
