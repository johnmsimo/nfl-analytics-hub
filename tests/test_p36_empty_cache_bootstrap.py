from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_one_event_workflow_bootstraps_catalog_only_on_explicit_refresh():
    workflow = (
        ROOT / ".github" / "workflows" / "p36-market-pricing-verification.yml"
    ).read_text(encoding="utf-8")
    assert "Bootstrap provider catalog when explicitly refreshing an empty cache" in workflow
    assert "inputs.confirmation == 'RUN_ONE_EVENT_PRICE_REFRESH_VERIFY'" in workflow
    assert "/app/scripts/p36_bootstrap_provider_catalog.py" in workflow
    assert "MODE=one-event" in workflow


def test_catalog_bootstrap_is_empty_cache_only_and_persists_through_odds_layer():
    script = (ROOT / "scripts" / "p36_bootstrap_provider_catalog.py").read_text(
        encoding="utf-8"
    )
    assert 'before_events = int(before.get("game_events") or 0)' in script
    assert "if before_events > 0:" in script
    assert "odds_api.get_game_odds(force=True)" in script
    assert '"catalogFetchPerformed": False' in script
    assert '"catalogFetchPerformed": True' in script
    assert 'after.get("cache_persistence")' in script


def test_cache_only_verification_does_not_run_catalog_bootstrap():
    workflow = (
        ROOT / ".github" / "workflows" / "p36-market-pricing-verification.yml"
    ).read_text(encoding="utf-8")
    bootstrap_index = workflow.index("Bootstrap provider catalog when explicitly refreshing an empty cache")
    bootstrap_block = workflow[bootstrap_index : workflow.index("Run sanitized P3.6", bootstrap_index)]
    assert "RUN_CACHE_ONLY_VERIFY" not in bootstrap_block
    assert "RUN_ONE_EVENT_PRICE_REFRESH_VERIFY" in bootstrap_block
