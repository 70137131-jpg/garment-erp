from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _read_version() -> str:
    """Release version from the repo-root VERSION file (baked into images)."""
    for candidate in (
        Path(__file__).resolve().parent.parent / "VERSION",
        Path(__file__).resolve().parent.parent.parent / "VERSION",
    ):
        if candidate.is_file():
            return candidate.read_text().strip()
    return "0.0.0-dev"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Garment ERP"
    app_version: str = _read_version()
    environment: Literal["development", "test", "production"] = "development"

    # Observability. JSON logs are what container platforms expect; console
    # is friendlier for local dev. Sentry stays off until a DSN is provided.
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "console"
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = 0.0
    # SQLite by default for zero-friction local dev; use Postgres in production,
    # e.g. postgresql+psycopg://user:pass@host/garment_erp
    database_url: str = "sqlite:///./garment_erp.db"
    auto_create_schema: bool = True
    enable_api_docs: bool = True
    max_request_body_bytes: int = 2 * 1024 * 1024
    # File attachments (larger than the JSON body cap; enforced on /attachments).
    max_upload_bytes: int = 10 * 1024 * 1024
    attachments_dir: str = "./attachments"

    # CORS origins allowed to call the API (the SPA dev server by default).
    # Comma-separated in the env var, e.g. "http://localhost:5173,https://erp.acme.com".
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Authentication/session policy. The bootstrap credentials are deliberately
    # unset: deployments must inject them once, then remove them after the first
    # administrator has been created.
    session_cookie_name: str = "garment_erp_session"
    csrf_cookie_name: str = "garment_erp_csrf"
    session_ttl_hours: int = 12
    session_idle_minutes: int = 30
    session_touch_minutes: int = 5
    session_cookie_secure: bool = False
    login_max_attempts: int = 5
    login_lockout_minutes: int = 15
    login_ip_max_attempts: int = 20
    login_ip_window_minutes: int = 15
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None
    bootstrap_admin_name: str = "System Administrator"

    # Comma-separated Host header allow-list. Keep localhost defaults usable in
    # development and set this explicitly to the public ERP hostname in prod.
    allowed_hosts: str = "localhost,127.0.0.1,testserver"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

    @model_validator(mode="after")
    def validate_security_configuration(self):
        positive = {
            "SESSION_TTL_HOURS": self.session_ttl_hours,
            "SESSION_IDLE_MINUTES": self.session_idle_minutes,
            "SESSION_TOUCH_MINUTES": self.session_touch_minutes,
            "LOGIN_MAX_ATTEMPTS": self.login_max_attempts,
            "LOGIN_IP_MAX_ATTEMPTS": self.login_ip_max_attempts,
            "MAX_REQUEST_BODY_BYTES": self.max_request_body_bytes,
        }
        invalid = [name for name, value in positive.items() if value <= 0]
        if invalid:
            raise ValueError(f"Security settings must be positive: {', '.join(invalid)}")
        if self.session_touch_minutes >= self.session_idle_minutes:
            raise ValueError("SESSION_TOUCH_MINUTES must be shorter than SESSION_IDLE_MINUTES")
        if self.environment == "production":
            errors = []
            if self.database_url.startswith("sqlite"):
                errors.append("DATABASE_URL must use PostgreSQL")
            if self.auto_create_schema:
                errors.append("AUTO_CREATE_SCHEMA must be false")
            if self.enable_api_docs:
                errors.append("ENABLE_API_DOCS must be false")
            if not self.session_cookie_secure:
                errors.append("SESSION_COOKIE_SECURE must be true")
            if any(not origin.startswith("https://") for origin in self.cors_origin_list):
                errors.append("every CORS_ORIGINS entry must use HTTPS")
            unsafe_hosts = {"*", "localhost", "127.0.0.1", "testserver"}
            if not self.allowed_host_list or unsafe_hosts.intersection(self.allowed_host_list):
                errors.append("ALLOWED_HOSTS must contain only production hostnames")
            if errors:
                raise ValueError("Unsafe production configuration: " + "; ".join(errors))
        return self


settings = Settings()
