import json
import logging

from fastapi.testclient import TestClient

from app.main import app
from app.observability import JsonFormatter


def test_health_reports_database_and_version(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["version"]


def test_ready_endpoint_reports_ready(client):
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_responses_carry_request_id_header(client):
    response = client.get("/health")
    assert response.headers.get("X-Request-ID")


def test_unhandled_errors_return_clean_500_with_request_id(client):
    @app.get("/__boom__")
    def boom():
        raise RuntimeError("intentional test failure")

    try:
        test_client = TestClient(app, raise_server_exceptions=False)
        response = test_client.get("/__boom__")
        assert response.status_code == 500
        body = response.json()
        assert body["detail"] == "Internal server error"
        assert body["request_id"]
        # The traceback must never leak to the client.
        assert "intentional test failure" not in response.text
    finally:
        app.router.routes = [r for r in app.router.routes if getattr(r, "path", "") != "/__boom__"]


def test_json_formatter_emits_valid_json_with_context():
    record = logging.LogRecord(
        name="garment_erp.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="GET /health -> 200",
        args=(),
        exc_info=None,
    )
    record.request_id = "abc-123"
    record.status_code = 200
    record.duration_ms = 12.5
    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "GET /health -> 200"
    assert payload["request_id"] == "abc-123"
    assert payload["status_code"] == 200
    assert payload["duration_ms"] == 12.5
    assert payload["level"] == "INFO"
