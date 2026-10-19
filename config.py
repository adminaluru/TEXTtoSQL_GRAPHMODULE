from functools import lru_cache
from typing import Literal, Optional

from psycopg.conninfo import make_conninfo
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

    # Browser origins allowed to call the API from another origin
    # (e.g. a UI on its own domain). Empty = none. Exact origins
    # only; "*" is refused. The development UI uses its own proxy
    # and needs nothing here.
    # Example: CORS_ALLOWED_ORIGINS='["https://flowone.example.com"]'
    cors_allowed_origins: list[str] = []

    # ------------------------------------------------------
    # Caller identity (Step 5)
    #
    # api_key : one shared key, no per-user identity or tenant.
    #           Development only; refused when APP_ENV=production.
    # jwt     : every request carries a signed token naming the
    #           user/service and their companies (tenants).
    #
    # Development (AUTH_MODE=jwt):
    #   IDENTITY_ISSUER=flowone-dev
    #   IDENTITY_AUDIENCE=flowone-api
    #   IDENTITY_SHARED_SECRET=<32+ random characters>
    #   TENANT_SCOPE_FILE=docs/examples/tenant_scope.dev.json
    #
    # Microsoft Entra ID later (no code change):
    #   IDENTITY_ENTRA_DIRECTORY_ID=<directory id>
    #   IDENTITY_AUDIENCE=api://<application id>
    #   IDENTITY_SUBJECT_CLAIM=oid  IDENTITY_KIND_CLAIM=idtyp
    # ------------------------------------------------------

    auth_mode: Literal["api_key", "jwt"] = "api_key"
    identity_issuer: Optional[str] = None
    identity_audience: Optional[str] = None
    identity_algorithms: list[str] = ["HS256"]
    identity_shared_secret: Optional[SecretStr] = None
    identity_jwks_url: Optional[str] = None
    identity_entra_directory_id: Optional[str] = None
    identity_policy_version: str = "dev-1"
    identity_subject_claim: str = "sub"
    identity_tenants_claim: str = "tenants"
    identity_roles_claim: str = "roles"
    identity_kind_claim: Optional[str] = "kind"

    # Logical tenant -> owning company + ownership columns
    # (decision D-002). Required when AUTH_MODE=jwt.
    tenant_scope_file: Optional[str] = None

    # ------------------------------------------------------
    # Documents (Step 8, decision D-010). AUTH_MODE=jwt only:
    # every document belongs to the uploader's workspace.
    #
    # DOCUMENT_STORAGE_DIR holds the uploaded files and the
    # local catalog/index (SQLite). Keep it outside Git
    # (data/ is ignored) and back it up like a database.
    # ------------------------------------------------------

    documents_enabled: bool = True

    # Step 11 (decision D-011): route each question to the database,
    # the documents or both. Rules decide clear questions; the AI
    # classifier is asked only when they are unsure (one small call).
    # ROUTING_ENABLED=false keeps the original database-only path.
    routing_enabled: bool = True
    route_classifier_enabled: bool = True
    document_storage_dir: str = "data/documents"
    document_max_file_mb: int = 25
    document_processing_timeout_seconds: int = 120

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

    # TLS to PostgreSQL (Step 7). "prefer" suits a local
    # database; production requires require / verify-ca /
    # verify-full (checked at start-up).
    postgres_sslmode: Literal[
        "disable", "allow", "prefer", "require", "verify-ca", "verify-full"
    ] = "prefer"

    # Fail fast instead of hanging on an unreachable server.
    postgres_connect_timeout_seconds: int = 5

    # ------------------------------------------------------
    # Neo4j
    # ------------------------------------------------------

    neo4j_uri: str
    neo4j_user: str
    neo4j_password: SecretStr
    neo4j_database: str = "neo4j"

    # Bound every Neo4j wait (Step 7). Query timeouts are also
    # capped by the time left for the whole question.
    neo4j_connection_timeout_seconds: float = 5.0
    neo4j_query_timeout_seconds: float = 10.0

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

    # Total time budget for one question (Step 7): every LLM
    # call, Neo4j query and PostgreSQL statement gets at most
    # the time that is left.
    request_timeout_seconds: float = 60.0

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
        # make_conninfo quotes every value, so a password containing
        # spaces, quotes or "key=value" text cannot break the string
        # or inject extra connection options.
        return make_conninfo(
            host=self.postgres_host,
            port=self.postgres_port,
            dbname=self.postgres_db,
            user=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            sslmode=self.postgres_sslmode,
            connect_timeout=self.postgres_connect_timeout_seconds,
            application_name="flowone-research-agent",
        )


@lru_cache
def get_settings() -> Settings:
    """
    Return one cached Settings instance for the application.
    """
    return Settings()


settings = get_settings()
