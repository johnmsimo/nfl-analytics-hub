"""P2.3 regression coverage for strict script Content-Security-Policy."""

from __future__ import annotations

import re
from pathlib import Path

from csp_policy import inline_script_hashes, strict_csp_header

ROOT = Path(__file__).resolve().parents[1]
EVENT_HANDLER_RE = re.compile(r"\son[a-z][a-z0-9_-]*\s*=", re.IGNORECASE)


def _script_directives(header: str) -> dict[str, str]:
    directives: dict[str, str] = {}
    for raw in header.split(";"):
        raw = raw.strip()
        if not raw:
            continue
        name, _, value = raw.partition(" ")
        directives[name] = value.strip()
    return directives


def _browser_sources() -> list[Path]:
    sources = list(ROOT.glob("*.html"))
    static_dir = ROOT / "static"
    sources.extend(static_dir.rglob("*.html"))
    sources.extend(static_dir.rglob("*.js"))
    return sorted(set(sources))


def test_script_csp_has_no_unsafe_inline_exception():
    directives = _script_directives(strict_csp_header())
    script_src = directives["script-src"]

    assert "'self'" in script_src
    assert "'unsafe-inline'" not in script_src
    assert "'unsafe-eval'" not in script_src
    assert directives["script-src-attr"] == "'none'"
    assert directives["object-src"] == "'none'"


def test_all_committed_inline_scripts_are_explicitly_hashed():
    hashes = inline_script_hashes()
    assert hashes, "expected committed inline application scripts"
    header = strict_csp_header()
    assert all(source in header for source in hashes)
    assert len(hashes) == len(set(hashes))


def test_browser_sources_contain_no_inline_event_handler_attributes():
    offenders: list[str] = []
    for path in _browser_sources():
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if EVENT_HANDLER_RE.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{line_number}: {line.strip()[:180]}")

    assert not offenders, "inline event handlers violate script-src-attr 'none':\n" + "\n".join(offenders)


def test_login_response_serves_strict_csp(client):
    response = client.get("/login")
    assert response.status_code == 200
    header = response.headers["Content-Security-Policy"]
    directives = _script_directives(header)
    assert "'unsafe-inline'" not in directives["script-src"]
    assert directives["script-src-attr"] == "'none'"
    assert header == strict_csp_header()
