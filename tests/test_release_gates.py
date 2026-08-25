import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _workflow(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_pull_request_workflows_keep_stable_required_checks():
    ci = _workflow("ci.yml")
    quality = _workflow("quality.yml")

    assert "pull_request:" in ci
    assert "merge_group:" in ci
    assert "  test:" in ci
    assert "pull_request:" in quality
    assert "merge_group:" in quality
    assert "  quality:" in quality
    assert "  analytics-tests:" in quality


def test_production_deploy_requires_successful_tested_main_push():
    deploy = _workflow("fly.yml")

    assert "workflow_dispatch:" not in deploy
    assert "workflows: [CI]" in deploy
    assert "github.event.workflow_run.conclusion == 'success'" in deploy
    assert "github.event.workflow_run.event == 'push'" in deploy
    assert "github.event.workflow_run.head_branch == 'main'" in deploy
    assert "ref: ${{ github.event.workflow_run.head_sha }}" in deploy
    assert 'test "$(git rev-parse origin/main)" = "$DEPLOY_SHA"' in deploy


def test_main_protection_contract_requires_all_release_checks():
    protection = json.loads(
        (ROOT / ".github" / "main-protection.json").read_text(encoding="utf-8")
    )

    checks = protection["required_status_checks"]
    assert checks["strict"] is True
    assert set(checks["contexts"]) == {"test", "quality", "analytics-tests"}
    assert protection["required_pull_request_reviews"][
        "required_approving_review_count"
    ] == 0
    assert protection["enforce_admins"] is True
    assert protection["required_conversation_resolution"] is True
    assert protection["allow_force_pushes"] is False
    assert protection["allow_deletions"] is False
