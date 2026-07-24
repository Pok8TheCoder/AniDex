"""Resolve Kwik embed pages to m3u8 URLs."""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path


def unpack_packer(script: str) -> str:
    m = re.search(
        r"}\('(.+)',(\d+),(\d+),'(.+?)'\.split\('\|'\)",
        script,
        re.DOTALL,
    )
    if not m:
        raise ValueError("Not a p,a,c,k,e,d packer script")
    p, a, c, k = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4).split("|")

    def encode(n: int) -> str:
        return (encode(n // a) if n >= a else "") + (
            chr(n % a + 29)
            if (n % a) > 35
            else "0123456789abcdefghijklmnopqrstuvwxyz"[n % a]
        )

    d: dict[str, str] = {}
    for i in range(c):
        d[encode(i)] = k[i] if k[i] else encode(i)

    def repl(match: re.Match[str]) -> str:
        return d.get(match.group(0), match.group(0))

    return re.sub(r"\b\w+\b", repl, p)


def _largest_packed_script(html: str) -> str:
    best = ""
    for script in re.findall(r"<script[^>]*>([\s\S]*?)</script>", html, re.I):
        if "eval(function(p,a,c,k,e,d)" in script and len(script) > len(best):
            best = script
    return best


def extract_m3u8(html: str) -> str | None:
    m3u8_re = re.compile(r"https?://[^\s'\"\\<>]+?\.m3u8(?:\?[^\s'\"\\<>]*)?", re.I)
    direct = m3u8_re.search(html)
    if direct:
        return direct.group(0)
    packed = _largest_packed_script(html)
    if not packed:
        return None
    for fn in (_unpack_python, _unpack_node):
        try:
            text = fn(packed)
            m = m3u8_re.search(text)
            if m:
                return m.group(0)
        except Exception:
            continue
    return None


def _unpack_python(packed: str) -> str:
    return unpack_packer(packed)


def _unpack_node(packed: str) -> str:
    js = packed.strip()
    if js.startswith("eval("):
        js = js.replace("eval(", "console.log(", 1)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".js", delete=False, encoding="utf-8"
    ) as f:
        f.write(js)
        path = f.name
    try:
        out = subprocess.check_output(
            ["node", path],
            text=True,
            timeout=45,
            stderr=subprocess.DEVNULL,
        )
        return out
    finally:
        Path(path).unlink(missing_ok=True)
