"""Raw provenance capture must survive whatever types providers hand it."""

from datetime import UTC, date, datetime
from decimal import Decimal

from database import db
from db_models import RawIngestRecord
from source_registry import capture_raw, register_source


def test_captures_payloads_containing_dates(app_fixture):
    """nflverse roster rows carry date objects; the JSON column must take them."""
    with app_fixture.app_context():
        source = register_source("test-dates", "Date payload source")
        payload = {
            "player": "Test Player",
            "birth_date": date(1998, 4, 12),
            "observed": datetime(2026, 8, 17, tzinfo=UTC),
            "rating": Decimal("88.5"),
        }
        assert capture_raw(source, "roster", "2025:TST:1", payload, season=2025, week=1)
        db.session.commit()

        stored = db.session.scalar(
            db.select(RawIngestRecord).where(RawIngestRecord.external_id == "2025:TST:1")
        )
        assert stored is not None
        # Coerced to strings, matching what the hash was computed over.
        assert stored.payload["birth_date"] == "1998-04-12"
        assert stored.payload["rating"] == "88.5"


def test_identical_payloads_are_captured_once(app_fixture):
    with app_fixture.app_context():
        source = register_source("test-dedup", "Dedup source")
        payload = {"seen": date(2026, 1, 2)}
        assert capture_raw(source, "roster", "dedup-1", payload)
        db.session.commit()
        assert not capture_raw(source, "roster", "dedup-1", payload)


def test_changed_payload_is_captured_as_a_new_version(app_fixture):
    with app_fixture.app_context():
        source = register_source("test-version", "Versioning source")
        assert capture_raw(source, "roster", "ver-1", {"seen": date(2026, 1, 2)})
        db.session.commit()
        assert capture_raw(source, "roster", "ver-1", {"seen": date(2026, 1, 3)})
