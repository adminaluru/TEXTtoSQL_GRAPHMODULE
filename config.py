from functools import lru_cache
from typing import Optional

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central application configuration.

    All environment-specific values are loaded from .env
    or operating-system environment variables.

    Application code should depend on this Settings object
    rather than hard-coded configuration.
    """

    # ------------------------------------------------------
    # Application
    # ------------------------------------------------------

    app_name: str = "Research Agent"
    app_env: str = "dev"
    debug: bool = False
    api_key: Optional[SecretStr] = None
    rate_limit_requests_per_minute: int = 60

    # Proxy IPs / CIDR ranges whose X-Forwarded-For headers
    # are trusted for rate limiting. Empty = trust none.
    # Example: TRUSTED_PROXIES='["10.0.0.0/8"]'
    trusted_proxies: list[str] = []

    # ------------------------------------------------------
    # PostgreSQL
    # ------------------------------------------------------

    postgres_host: str
    postgres_port: int = 5432
    postgres_db: str
    postgres_user: str
    postgres_password: SecretStr

    # Connection pool. Size max_size to at least the number
    # of concurrent /ask workers; pool_timeout bounds how
    # long a request waits for a free connection.
    postgres_pool_min_size: int = 1
    postgres_pool_max_size: int = 10
    postgres_pool_timeout_seconds: float = 10.0

    # ------------------------------------------------------
    # Neo4j
    # ------------------------------------------------------

    neo4j_uri: str
    neo4j_user: str
    neo4j_password: SecretStr
    neo4j_database: str = "neo4j"

    # Ontology lookups are cached for this long. Lower it
    # while actively editing the ontology, or call
    # POST /admin/ontology-cache/clear.
    ontology_cache_ttl_seconds: float = 300.0

    # ------------------------------------------------------
    # LLM
    #
    # Deliberately provider-neutral.
    # No OpenAI / Bedrock dependency belongs here.
    # ------------------------------------------------------

    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None

    # Per-attempt request timeout and SDK-level retries.
    # Worst case ~= timeout * (retries + 1) plus backoff.
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 1

    # Repair loop: extra LLM attempts after an interpretation
    # failure, and the elapsed-time budget after which no new
    # attempt starts. 0 attempts disables repair.
    agent_repair_max_attempts: int = 2
    agent_repair_budget_seconds: float = 30.0

    # ------------------------------------------------------
    # Pydantic Settings Configuration
    # ------------------------------------------------------

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------
    # Derived PostgreSQL connection string
    # ------------------------------------------------------

    @property
    def postgres_dsn(self) -> str:
        password = self.postgres_password.get_secret_value()

        return (
            f"host={self.postgres_host} "
            f"port={self.postgres_port} "
            f"dbname={self.postgres_db} "
            f"user={self.postgres_user} "
            f"password={password}"
        )


@lru_cache
def get_settings() -> Settings:
    """
    Return one cached Settings instance for the application.
    """
    return Settings()


settings = get_settings()
