"""CLI: python -m anidex.sync.cli sync --peer http://192.168.x.x:8787"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="anidex.sync", description="AniDex peer sync")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sync = sub.add_parser("sync", help="Sync with a peer")
    p_sync.add_argument("--peer", default=None, help="Peer base URL")

    p_id = sub.add_parser("identity", help="Show device id / sync token")
    p_id.add_argument("--rotate", action="store_true", help="Rotate sync token")

    p_peer = sub.add_parser("set-peer", help="Save peer URL")
    p_peer.add_argument("url")

    p_tok = sub.add_parser("set-token", help="Set shared sync token (must match peer)")
    p_tok.add_argument("token")

    args = parser.parse_args(argv)

    from anidex.db import init_db

    init_db()

    if args.cmd == "identity":
        from anidex.sync.token import ensure_sync_identity, rotate_sync_token

        if args.rotate:
            rotate_sync_token()
        print(json.dumps(ensure_sync_identity(), indent=2))
        return 0

    if args.cmd == "set-peer":
        from anidex.sync.token import set_peer_url, ensure_sync_identity

        set_peer_url(args.url)
        print(json.dumps(ensure_sync_identity(), indent=2))
        return 0

    if args.cmd == "set-token":
        from anidex.sync.token import set_sync_token, ensure_sync_identity

        set_sync_token(args.token)
        print(json.dumps(ensure_sync_identity(), indent=2))
        return 0

    if args.cmd == "sync":
        from anidex.sync.client import run_sync

        try:
            result = run_sync(args.peer)
        except Exception as e:  # noqa: BLE001
            print(f"Sync failed: {e}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
