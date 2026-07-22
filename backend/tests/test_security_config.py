import pytest
from pydantic import ValidationError

from app.config import Settings


def test_production_configuration_fails_closed_with_development_defaults():
    with pytest.raises(ValidationError, match="Unsafe production configuration"):
        Settings(environment="production", _env_file=None)


def test_hardened_production_configuration_is_accepted():
    configured = Settings(
        environment="production",
        database_url="postgresql+psycopg://erp@db.internal/garment",
        auto_create_schema=False,
        enable_api_docs=False,
        session_cookie_secure=True,
        cors_origins="https://erp.example.com",
        allowed_hosts="erp.example.com",
        _env_file=None,
    )
    assert configured.environment == "production"


def test_session_touch_must_be_shorter_than_idle_window():
    with pytest.raises(ValidationError, match="SESSION_TOUCH_MINUTES"):
        Settings(session_idle_minutes=5, session_touch_minutes=5, _env_file=None)
