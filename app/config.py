from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "CQL_"}

    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    database: str = ":memory:"
    use_demo: bool = True
    mcp_timeout_ms: int = 30000
    remote_timeout_ms: int = 30000
    register_mock_providers: bool = True
    register_deepsee_mock: bool = False
    catalog_db: str = "cql_catalog.db"
    refresh_scheduler_enabled: bool = True
    refresh_scheduler_poll_seconds: float = 5.0


settings = Settings()
