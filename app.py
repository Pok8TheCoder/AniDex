#!/usr/bin/env python3
"""LocalFun — local web app (anime & manga tracker)."""

from __future__ import annotations

import argparse
import socket
import threading
import time
import webbrowser


HOST = "127.0.0.1"
PORT = 8787


def _lan_ips() -> list[str]:
    ips: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    # Fallback: UDP trick for primary outbound interface
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127.") and ip not in ips:
            ips.insert(0, ip)
    except OSError:
        pass
    return ips


def main() -> int:
    parser = argparse.ArgumentParser(description="LocalFun web server")
    parser.add_argument(
        "--host",
        default=HOST,
        help="Bind address (default 127.0.0.1 — localhost only)",
    )
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument(
        "--lan",
        action="store_true",
        help="Bind 0.0.0.0 and require an access token for phones on your Wi‑Fi",
    )
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    host = "0.0.0.0" if args.lan else args.host
    token = None
    if args.lan or host in ("0.0.0.0", "::"):
        from localfun.web.security import enable_lan_mode

        token = enable_lan_mode()
        host = "0.0.0.0" if host == "::" else host

    local_url = f"http://127.0.0.1:{args.port}/"
    open_url = local_url

    print(f"LocalFun listening on http://{host}:{args.port}/")
    print(f"  This PC: {local_url}")
    if token:
        print("  LAN mode ON — remote devices need the token link:")
        for ip in _lan_ips() or ["<your-lan-ip>"]:
            print(f"  Phone:  http://{ip}:{args.port}/?token={token}")
        print("  Keep this token private (same Wi‑Fi only; regenerates each --lan start).")
        # Prefer first LAN IP for console clarity; browser still opens localhost
    else:
        print("  Remote/LAN access is blocked (use --lan to allow phones).")

    if not args.no_browser:
        def _open() -> None:
            time.sleep(0.8)
            webbrowser.open(open_url)

        threading.Thread(target=_open, daemon=True).start()

    import uvicorn

    uvicorn.run(
        "localfun.web.app:app",
        host=host,
        port=args.port,
        log_level="info",
        reload=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
