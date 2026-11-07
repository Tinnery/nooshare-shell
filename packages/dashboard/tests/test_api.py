"""Dashboard API tests."""
import pytest
from fastapi.testclient import TestClient

from dashboard.app import app


@pytest.fixture
def client():
    return TestClient(app)


def test_health(client):
    """Test health endpoint."""
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_knowledge_tree(client):
    """Test knowledge tree endpoint."""
    response = client.get("/api/knowledge/tree")
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "directory"
    assert data["name"] == "knowledge"


def test_knowledge_content_not_found(client):
    """Test content endpoint with non-existent file."""
    response = client.get("/api/knowledge/content/nonexistent.md")
    assert response.status_code == 404


def test_knowledge_meta_not_found(client):
    """Test meta endpoint with non-existent file."""
    response = client.get("/api/knowledge/meta/nonexistent.md")
    assert response.status_code == 404
