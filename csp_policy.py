"""Strict script Content-Security-Policy helpers for P2.3.

The UI is served from committed static HTML files.  Those pages still contain
inline <script> blocks, but they are immutable application code rather than
runtime-generated snippets.  Instead of granting the browser blanket
``'unsafe-inline'`` permission, hash every committed inline script and allow
only those exact bytes.

Event-handler attributes (onclick=, onload=, etc.) are deliberately unsupported
and are covered by regression tests.  JavaScript should attach listeners from
trusted script blocks/files instead.
"""

from __future__ import annotations

import base64
import hashlib
import re
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_SCRIPT_RE = re.compile(r"<script\b(?P<attrs>[^>]*)>(?P<body>.*?)</script\s*>", re.IGNORECASE | re.DOTALL)
_SRC_RE = re.compile(r"\bsrc\s*=", re.IGNORECASE)


def _html_files(root: Path = _ROOT) -> list[Path]:
    files = list(root.glob("*.html"))
    static_dir = root / "static"
    if static_dir.exists():
        files.extend(static_dir.rglob("*.html"))
    return sorted(files)


def inline_script_hashes(root: Path = _ROOT) -> tuple[str, ...]:
    """Return CSP-formatted SHA-256 sources for committed inline scripts."""
    hashes: set[str] = set()
    for path in _html_files(root):
        text = path.read_text(encoding="utf-8")
        for match in _SCRIPT_RE.finditer(text):
            if _SRC_RE.search(match.group("attrs")):
                continue
            body = match.group("body")
            if not body:
                continue
            digest = hashlib.sha256(body.encode("utf-8")).digest()
            encoded = base64.b64encode(digest).decode("ascii")
            hashes.add(f"'sha256-{encoded}'")
    return tuple(sorted(hashes))


@lru_cache(maxsize=1)
def strict_csp_header() -> str:
    script_sources = " ".join(("'self'", *inline_script_hashes()))
    return (
        "default-src 'self'; "
        f"script-src {script_sources}; "
        "script-src-attr 'none'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self'; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )
