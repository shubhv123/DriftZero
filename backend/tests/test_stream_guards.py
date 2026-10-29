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


def test_allow_local_repos_env_default():
    from backend.core.repo_manager import prepare_repo_workspace

    # Ensure ALLOW_LOCAL_REPOS defaults to 0 and local directories are rejected
    with pytest.raises(ValueError, match="disallowed"):
        prepare_repo_workspace("/tmp/fake_local_repo")
