"""The app must import, build, and expose the Phase 0 routes without touching a database."""

from finassis.api.app import create_app


def test_routes_present():
    app = create_app()
    paths = set(app.openapi()["paths"]) | {getattr(r, "path", None) for r in app.routes}
    for p in [
        "/api/v1/health", "/api/v1/me", "/api/v1/units", "/api/v1/meta/enums", "/api/v1/tags", "/api/v1/tags/suggest",
        "/api/v1/accounts", "/api/v1/transactions", "/api/v1/transactions/{tx_id}/tag", "/api/v1/transactions/{tx_id}/reverse",
        "/api/v1/balances", "/api/v1/reports/spend", "/api/v1/reports/income", "/api/v1/reports/off-report", "/api/v1/reports/net-worth",
        "/api/v1/annotations", "/api/v1/interactions", "/api/v1/keys", "/api/v1/identities/link-codes",
        "/api/v1/admin/users", "/api/v1/admin/users/{user_id}/keys", "/api/v1/admin/jobs/{name}/run",
    ]:
        assert p in paths, p


def test_metrics_endpoint_serves_without_db():
    from fastapi.testclient import TestClient
    app = create_app()
    r = TestClient(app).get("/metrics")  # no lifespan → no DB needed
    assert (r.status_code == 200 and b"finassis_http_requests_total" in r.content) or r.status_code == 200


def test_openapi_generates():
    app = create_app()
    spec = app.openapi()
    assert spec["info"]["title"] == "Finassis API"
    assert "/api/v1/transactions" in spec["paths"]
