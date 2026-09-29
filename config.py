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

    # ------------------------------------------------------
    # PostgreSQL
    # ------------------------------------------------------

    postgres_host: str
    postgres_port: int = 5432
    postgres_db: str
    postgres_user: str
    postgres_password: SecretStr

    # ------------------------------------------------------
    # Neo4j
    # ------------------------------------------------------

    neo4j_uri: str
    neo4j_user: str
    neo4j_password: SecretStr
    neo4j_database: str = "neo4j"

    # ------------------------------------------------------
    # LLM
    #
    # Deliberately provider-neutral.
    # No OpenAI / Bedrock dependency belongs here.
    # ------------------------------------------------------

    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None

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