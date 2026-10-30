import os
import asyncio
import pytest
from fastapi.testclient import TestClient
from backend.main import (
    app,
    _reset_limits_for_testing,
    _acquire_concurrency_slot,
    _release_concurrency_slot,
)


@pytest.fixture(autouse=True)
def setup_test_env():
    # Set known test environment variables
    orig_api_key = os.environ.get("DRIFTZERO_API_KEY")
    os.environ["DRIFTZERO_API_KEY"] = "test-secret-api-key"
    _reset_limits_for_testing()
    yield
    _reset_limits_for_testing()
    if orig_api_key is not None:
        os.environ["DRIFTZERO_API_KEY"] = orig_api_key
    else:
        os.environ.pop("DRIFTZERO_API_KEY", None)


def test_missing_api_key_post():
    client = TestClient(app)
    response = client.post("/api/migrate/stream", json={"repo_url": ""})
    assert response.status_code == 401
    assert "X-API-Key" in response.json()["detail"]


def test_invalid_api_key_post():
    client = TestClient(app)
    response = client.post(
        "/api/migrate/stream",
        json={"repo_url": ""},
        headers={"X-API-Key": "wrong-key"},
    )
    assert response.status_code == 401
    assert "Invalid or missing" in response.json()["detail"]


def test_valid_api_key_post():
    client = TestClient(app)
    response = client.post(
        "/api/migrate/stream",
        json={"repo_url": ""},
        headers={"X-API-Key": "test-secret-api-key"},
    )
    assert response.status_code == 200


def test_missing_api_key_get():
    client = TestClient(app)
    response = client.get("/api/migrate/stream?repo_url=")
    assert response.status_code == 401


def test_valid_api_key_header_get():
    client = TestClient(app)
    response = client.get(
        "/api/migrate/stream?repo_url=",
        headers={"X-API-Key": "test-secret-api-key"},
    )
    assert response.status_code == 200


def test_valid_api_key_query_get():
    client = TestClient(app)
    response = client.get("/api/migrate/stream?repo_url=&api_key=test-secret-api-key")
    assert response.status_code == 200


def test_rate_limiting_post():
    client = TestClient(app)
    headers = {"X-API-Key": "test-secret-api-key"}

    # Exhaust the 10 requests per minute
    for _ in range(10):
        resp = client.post("/api/migrate/stream", json={"repo_url": ""}, headers=headers)
        assert resp.status_code == 200

    # 11th request should exceed rate limit
    exceeded_resp = client.post("/api/migrate/stream", json={"repo_url": ""}, headers=headers)
    assert exceeded_resp.status_code == 429
    assert "Rate limit exceeded" in exceeded_resp.json()["detail"]


def test_concurrency_cap_post():
    client = TestClient(app)
    headers = {"X-API-Key": "test-secret-api-key"}

    # Acquire all concurrency slots (default 2)
    async def acquire_slots():
        await _acquire_concurrency_slot()
        await _acquire_concurrency_slot()

    asyncio.run(acquire_slots())

    # Next request should be blocked by concurrency cap
    blocked_resp = client.post("/api/migrate/stream", json={"repo_url": ""}, headers=headers)
    assert blocked_resp.status_code == 429
    assert "Concurrency cap reached" in blocked_resp.json()["detail"]

    # Releasing a slot should unblock subsequent requests
    _release_concurrency_slot()
    success_resp = client.post("/api/migrate/stream", json={"repo_url": ""}, headers=headers)
    assert success_resp.status_code == 200


def test_allow_local_repos_env_default(monkeypatch):
    monkeypatch.delenv("ALLOW_LOCAL_REPOS", raising=False)
    from backend.core.repo_manager import prepare_repo_workspace

    # Ensure ALLOW_LOCAL_REPOS defaults to 0 and local directories are rejected
    with pytest.raises(ValueError, match="disallowed"):
        prepare_repo_workspace("/tmp/fake_local_repo")


def test_startup_refuses_without_api_key(monkeypatch):
    monkeypatch.delenv("DRIFTZERO_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DRIFTZERO_API_KEY is required at startup"):
        with TestClient(app):
            pass


def test_rate_limiting_keyed_by_ip_only(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY", "1")
    client = TestClient(app)
    headers_key1 = {"X-API-Key": "test-secret-api-key", "X-Forwarded-For": "203.0.113.195"}
    headers_key2 = {"X-API-Key": "test-secret-api-key", "X-Forwarded-For": "203.0.113.195"}

    # 10 requests from this IP
    for _ in range(5):
        resp = client.post("/api/migrate/stream", json={"repo_url": ""}, headers=headers_key1)
        assert resp.status_code == 200
    for _ in range(5):
        resp = client.post("/api/migrate/stream", json={"repo_url": ""}, headers=headers_key2)
        assert resp.status_code == 200

    # 11th request from same IP should fail
    resp_blocked = client.post("/api/migrate/stream", json={"repo_url": ""}, headers=headers_key1)
    assert resp_blocked.status_code == 429

    # But a different IP should succeed
    headers_diff_ip = {"X-API-Key": "test-secret-api-key", "X-Forwarded-For": "198.51.100.42"}
    resp_other_ip = client.post("/api/migrate/stream", json={"repo_url": ""}, headers=headers_diff_ip)
    assert resp_other_ip.status_code == 200


def test_rate_limiting_evicts_old_buckets(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY", "1")
    import time
    from unittest.mock import MagicMock
    from backend.main import _rate_limit_records, _check_rate_limit

    # Seed an old bucket
    old_ip = "192.0.2.1"
    _rate_limit_records[old_ip] = [time.time() - 100.0]

    # New request triggers eviction
    req = MagicMock()
    req.headers = {"x-forwarded-for": "192.0.2.2"}
    req.client = None

    asyncio.run(_check_rate_limit(req))

    # Old bucket should be evicted
    assert old_ip not in _rate_limit_records
    # New bucket is present
    assert "192.0.2.2" in _rate_limit_records


def test_manifest_needs_manual_creates_draft_pr():
    import tempfile
    from pathlib import Path
    from unittest.mock import patch, MagicMock
    from backend.schemas.migration_models import (
        MigrationDelta,
        Stage4VerificationReport,
    )
    from backend.exporters.github_pr_client import create_migration_pr

    s4 = Stage4VerificationReport(
        test_file_path="tests/test_migration_parity.py",
        total_attempts=1,
        final_status="NEEDS_MANUAL",
        self_healing_triggered=False,
        attempts=[],
    )
    delta = MigrationDelta(
        library="pydantic",
        from_version="1.10.0",
        to_version="2.9.2",
        tavily_queries_executed=[],
        citations=[],
        rules=[],
        synthesized_by_model="test",
        generation_latency_ms=10.0,
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        ws_dir = Path(tmp_dir)
        (ws_dir / ".git").mkdir()

        mock_post_res = MagicMock()
        mock_post_res.status_code = 201
        mock_post_res.json.return_value = {"html_url": "https://github.com/org/repo/pull/42"}

        mock_git_res = MagicMock(returncode=0, stdout="", stderr="")
        with patch("backend.exporters.github_pr_client._run_git", return_value=mock_git_res), \
             patch("backend.exporters.github_pr_client._detect_default_branch", return_value="main"), \
             patch("httpx.get") as mock_get, \
             patch("httpx.post", return_value=mock_post_res) as mock_post:

            mock_user_res = MagicMock(status_code=200)
            mock_user_res.json.return_value = {"login": "org", "permissions": {"push": True}}
            mock_get.return_value = mock_user_res

            # Even with skip_on_fail=True, NEEDS_MANUAL creates a Draft PR
            pr_url = create_migration_pr(
                ws_dir, delta, s4, "https://github.com/org/repo", "token123", skip_on_fail=True
            )
            assert pr_url == "https://github.com/org/repo/pull/42"
            payload = mock_post.call_args[1]["json"]
            assert payload["draft"] is True
            assert "[DRAFT]" in payload["title"]



def test_rotating_xff_does_not_bypass_rate_limit(monkeypatch):
    from fastapi.testclient import TestClient
    import backend.main as m

    monkeypatch.delenv("TRUSTED_PROXY", raising=False)
    monkeypatch.setenv("DRIFTZERO_API_KEY", "k" * 32)
    monkeypatch.setattr(m, "RATE_LIMIT_PER_MINUTE", 2)
    m._rate_limit_records.clear()
    body = {"repo_url": "", "library": "pydantic", "target_version": "2.9.2"}
    with TestClient(m.app) as c:
        codes = [
            c.post("/api/migrate/stream", json=body,
                   headers={"X-API-Key": "k" * 32, "X-Forwarded-For": f"10.0.0.{i}"}).status_code
            for i in range(5)
        ]
    assert 429 in codes


def test_placeholder_api_key_rejected_at_startup(monkeypatch):
    import pytest
    from fastapi.testclient import TestClient
    import backend.main as m

    monkeypatch.setenv("DRIFTZERO_API_KEY", "your_driftzero_api_key_here")
    with pytest.raises(RuntimeError):
        with TestClient(m.app):
            pass
