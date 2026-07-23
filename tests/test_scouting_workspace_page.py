from html.parser import HTMLParser
from pathlib import Path


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
        if tag == "script" and values.get("src"):
            self.scripts.append(values["src"])


def test_scouting_workspace_has_required_controls():
    source = Path("scouting.html").read_text(encoding="utf-8")
    parser = _Parser()
    parser.feed(source)
    assert {
        "tabs",
        "workspace-form",
        "report-title",
        "payload",
        "save-report",
        "output",
    } <= parser.ids
    assert "/static/app.js" in parser.scripts


def test_scouting_workspace_covers_promised_flows():
    source = Path("scouting.html").read_text(encoding="utf-8")
    for capability in (
        "player-comparison",
        "team-style-map",
        "tendency-explorer",
        "matchup-card",
        "history-review",
        "saved",
    ):
        assert capability in source


def test_saved_reports_disclose_browser_local_storage():
    source = Path("scouting.html").read_text(encoding="utf-8")
    assert "localStorage" in source
    assert "No saved reports in this browser" in source
