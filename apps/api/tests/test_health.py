import pytest
from fastapi.testclient import TestClient

from secondbrain import __version__


def test_health_reports_degraded_when_database_is_down(client_without_db: TestClient) -> None:
    response = client_without_db.get("/v1/health")
    assert response.status_code == 200  # the endpoint describes failure, it does not fail
    body = response.json()
    assert body["status"] == "degraded"
    assert body["version"] == __version__
    assert body["environment"] == "test"
    assert body["database"]["reachable"] is False
    assert body["database"]["error"]


def test_openapi_schema_is_served(client_without_db: TestClient) -> None:
    schema = client_without_db.get("/openapi.json").json()
    assert "/v1/health" in schema["paths"]
    assert "HealthReport" in schema["components"]["schemas"]


def test_cors_allows_the_frontend_origin(client_without_db: TestClient) -> None:
    response = client_without_db.options(
        "/v1/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


@pytest.mark.integration
def test_health_is_ok_against_real_database(db_client: TestClient) -> None:
    body = db_client.get("/v1/health").json()
    assert body["database"]["reachable"] is True
    assert body["database"]["server_version"].startswith("PostgreSQL")
    assert body["database"]["pgvector_version"], "run `alembic upgrade head` first"
    assert body["database"]["migration_revision"]
    assert body["status"] == "ok"
