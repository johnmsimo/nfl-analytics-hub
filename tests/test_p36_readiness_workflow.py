from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_p36_readiness_gate_preserves_http_503_diagnostics_and_falls_back_to_internal_check():
    workflow = (
        ROOT / ".github" / "workflows" / "p36-market-pricing-verification.yml"
    ).read_text(encoding="utf-8")

    assert "--fail-with-body" in workflow
    assert '--output "$BODY" --write-out "%{http_code}"' in workflow
    assert "Sanitized public /ready response" in workflow
    assert "Public readiness failed; running internal Fly readiness diagnostic" in workflow
    assert "internal_ready_status=" in workflow
    assert 'c.get(\\"/ready\\")' in workflow
    assert "curl --fail --silent" not in workflow
