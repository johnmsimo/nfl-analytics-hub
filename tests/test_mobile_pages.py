import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AUTHENTICATED_PAGES = (
    "/",
    "/games",
    "/players",
    "/teams",
    "/projections",
    "/live",
    "/analytics",
    "/scouting",
    "/model-operations",
    "/enterprise-operations",
    "/rankings",
    "/settings",
    "/admin/data",
    "/ask",
    "/props",
    "/game/401873298",
    "/tracker",
    "/player/mobile-audit",
)


@pytest.mark.parametrize("url", AUTHENTICATED_PAGES)
def test_authenticated_page_has_mobile_viewport_and_shared_shell(client, url):
    response = client.get(url)

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    viewport = re.search(
        r'<meta\s+name="viewport"\s+content="([^"]+)"', html, re.IGNORECASE
    )
    assert viewport, f"{url} is missing a viewport meta tag"
    directives = viewport.group(1).replace(" ", "")
    assert "width=device-width" in directives
    assert "viewport-fit=cover" in directives
    assert 'href="/static/theme.css"' in html
    assert 'src="/static/app.js"' in html


def test_shared_theme_contains_iphone_layout_contracts():
    css = (ROOT / "static" / "theme.css").read_text()

    assert "P1.5 iPhone viewport hardening" in css
    assert "@media(max-width:820px)" in css
    assert "@media(max-width:430px)" in css
    assert "@media(max-width:360px)" in css
    assert "font-size:16px" in css
    assert "min-height:44px" in css
    assert "env(safe-area-inset-bottom" in css
    assert ".ai-sidebar{" in css and "overflow-y:auto" in css
    assert ".directory-table-wrap,.scroll{max-width:100%" in css
    assert ".page .panel{" in css
    assert re.search(r"(?<!\.page )\.panel\{[^}]*padding:20px", css) is None


def test_mobile_navigation_is_keyboard_and_screen_reader_operable():
    javascript = (ROOT / "static" / "app.js").read_text()

    assert "aria-controls=\"app-menu\"" in javascript
    assert "aria-expanded=\"false\"" in javascript
    assert "Application menu" in javascript
    assert "Mobile navigation" in javascript
    assert "Open bet slip" in javascript
    assert "if(e.key==='Escape')" in javascript
