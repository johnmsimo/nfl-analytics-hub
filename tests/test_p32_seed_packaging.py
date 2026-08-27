"""Regression coverage for the P3.2 production seed-data bundle."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_required_player_baseline_is_included_in_docker_context():
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "data/*.csv" in dockerignore
    assert "!data/player_week_2025.csv" in dockerignore
    assert dockerignore.index("!data/player_week_2025.csv") > dockerignore.index("data/*.csv")
    assert (ROOT / "data" / "player_week_2025.csv").is_file()


def test_docker_image_build_fails_if_required_seed_is_missing():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "test -s /app/data/player_week_2025.csv" in dockerfile
    assert "cp -a /app/data/. /app/seed-data/" in dockerfile
    assert "test -s /app/seed-data/player_week_2025.csv" in dockerfile
