"""Deprecated shim — AniDex is the web app in app.py. Desktop UI lives in archive/desktop_ui."""

from app import main

if __name__ == "__main__":
    raise SystemExit(main())
