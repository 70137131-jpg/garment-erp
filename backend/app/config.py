from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Garment ERP"
    # SQLite by default for zero-friction local dev; use Postgres in production,
    # e.g. postgresql+psycopg://user:pass@host/garment_erp
    database_url: str = "sqlite:///./garment_erp.db"

    # CORS origins allowed to call the API (the SPA dev server by default).
    # Comma-separated in the env var, e.g. "http://localhost:5173,https://erp.acme.com".
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
