from __future__ import annotations
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch
from app.main import app

client = TestClient(app)


@pytest.fixture(scope="session", autouse=True)
def schema():
    """Health check tests test liveness and degraded DB states without requiring live DB schema."""
    yield


def test_get_health():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert data["status"] in ["healthy", "degraded"]
    assert "version" in data
    assert "database" in data
    assert data["service"] == "newtonite-api"


def test_get_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_get_health_degraded():
    with patch("app.main.engine.connect", side_effect=Exception("DB Down")):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["database"] == "disconnected"
