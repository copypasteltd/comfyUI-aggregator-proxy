from __future__ import annotations

import pytest
from control_api.main import create_app
from fastapi.testclient import TestClient
from generation_infrastructure.models import AdminUser
from generation_infrastructure.security import password_hash
from generation_infrastructure.settings import Settings
from pydantic import SecretStr, ValidationError
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration
PASSWORD = "Synthetic-cors-test-password"


def test_production_http_requires_explicit_setting(test_settings: Settings) -> None:
    values = {**test_settings.model_dump(), "environment": "production", "public_origin": "http://127.0.0.1:39443", "storage_backend": "s3", "s3_bucket": "synthetic", "s3_access_key": SecretStr("synthetic"), "s3_secret_key": SecretStr("synthetic")}
    with pytest.raises(ValidationError, match="Production requires HTTPS"):
        Settings(_env_file=None, **values)
    enabled = Settings(_env_file=None, **{**values, "allow_http": True})
    assert not enabled.secure_cookies and enabled.cookie_same_site == "lax"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**values, "allow_http": True, "public_origin": "ftp://127.0.0.1"})


@pytest.mark.parametrize("origin", ["https://arbitrary.invalid", "http://different.invalid:8080", "null"])
def test_any_origin_preflight_and_authentication_errors(test_settings: Settings, origin: str) -> None:
    settings = test_settings.model_copy(update={"allow_any_origin": True})
    with TestClient(create_app(settings), base_url=settings.public_origin) as client:
        response = client.options("/v1/assets", headers={"Origin": origin, "Access-Control-Request-Method": "DELETE", "Access-Control-Request-Headers": "Authorization,X-Arbitrary-Header,Idempotency-Key"})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin
        assert response.headers["access-control-allow-credentials"] == "true"
        assert "X-Arbitrary-Header" in response.headers["access-control-allow-headers"]
        assert "DELETE" in response.headers["access-control-allow-methods"]
        response = client.get("/v1/models", headers={"Origin": origin})
        assert response.status_code == 401
        assert response.headers["access-control-allow-origin"] == origin
        assert response.headers["vary"] == "Origin"
        assert "ETag" in response.headers["access-control-expose-headers"]


def test_any_origin_covers_unhandled_errors(test_settings: Settings) -> None:
    settings = test_settings.model_copy(update={"allow_any_origin": True})
    app = create_app(settings)

    @app.get("/synthetic-cors-error")
    def failure() -> None:
        raise RuntimeError("Synthetic failure")

    with TestClient(app, base_url=settings.public_origin, raise_server_exceptions=False) as client:
        response = client.get("/synthetic-cors-error", headers={"Origin": "https://arbitrary.invalid"})
        assert response.status_code == 500
        assert response.headers["access-control-allow-origin"] == "https://arbitrary.invalid"
        assert "Synthetic failure" not in response.text


@pytest.mark.parametrize("origin", [None, "https://arbitrary.invalid"])
def test_http_login_and_cross_origin_admin_write_still_require_csrf(pg: sessionmaker[Session], test_settings: Settings, origin: str | None) -> None:
    settings = test_settings.model_copy(update={"public_origin": "http://127.0.0.1:39443", "allow_http": True, "allow_any_origin": True})
    with pg.begin() as session:
        session.add(AdminUser(username="cors-admin", password_hash=password_hash(PASSWORD), role="administrator"))
    with TestClient(create_app(settings), base_url=settings.public_origin) as client:
        headers = {"Origin": origin} if origin else {}
        response = client.post("/admin/api/v1/session", headers=headers, json={"username": "cors-admin", "password": PASSWORD})
        assert response.status_code == 200
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie and "secure" not in cookie
        assert client.get("/admin/api/v1/session").status_code == 200
        csrf = response.json()["csrf_token"]
        assert client.post("/admin/api/v1/session/activity", headers={"Origin": "https://another.invalid"}, json={"activity": "interactive"}).status_code == 403
        valid = {"Origin": "https://another.invalid", "X-CSRF-Token": csrf}
        assert client.post("/admin/api/v1/session/activity", headers=valid, json={"activity": "interactive"}).status_code == 200
        assert client.delete("/admin/api/v1/session", headers=valid).status_code == 204
        assert client.get("/admin/api/v1/session").status_code == 401


def test_https_cookie_supports_cross_site_credentials(test_settings: Settings) -> None:
    settings = test_settings.model_copy(update={"public_origin": "https://example.invalid", "allow_any_origin": True})
    assert settings.secure_cookies and settings.cookie_same_site == "none"
    assert settings.accepts_admin_origin("https://any.invalid")
    strict = settings.model_copy(update={"allow_any_origin": False})
    assert strict.secure_cookies and strict.cookie_same_site == "lax"
    assert not strict.accepts_admin_origin("https://any.invalid")
