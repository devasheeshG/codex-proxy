# Path: app/config.py
# Description: Application settings plus the fixed Codex, ChatGPT, OAuth, and proxy endpoints.

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

# The dashboard is served at "/" and this backend is mounted at "/api" on the
# same reverse-proxy origin. Codex clients therefore use /api/v1 as their model
# provider base URL and POST Responses API traffic to /api/v1/responses.
API_PREFIX = "/api"

# ChatGPT subscription inference uses the same Responses wire protocol as the
# OpenAI API, but through the Codex backend and a ChatGPT OAuth access token.
CHATGPT_BASE_URL = "https://chatgpt.com/backend-api"
UPSTREAM_CODEX_BASE_URL = f"{CHATGPT_BASE_URL}/codex"
CODEX_USER_AGENT = "codex-proxy/0.1"
# The Codex model catalog requires this query parameter. Keep it configurable
# so deployments can advance it without a code change when the CLI updates.
DEFAULT_CODEX_CLIENT_VERSION = "0.157.0"

# Codex's official device-code login flow. Device authorization avoids requiring
# the proxy host to receive a browser callback and works for remote deployments.
AUTH_BASE_URL = "https://auth.openai.com"
OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OAUTH_TOKEN_URL = f"{AUTH_BASE_URL}/oauth/token"
OAUTH_DEVICE_CODE_URL = f"{AUTH_BASE_URL}/api/accounts/deviceauth/usercode"
OAUTH_DEVICE_POLL_URL = f"{AUTH_BASE_URL}/api/accounts/deviceauth/token"
OAUTH_DEVICE_REDIRECT_URI = f"{AUTH_BASE_URL}/deviceauth/callback"
OAUTH_DEVICE_VERIFICATION_URL = f"{AUTH_BASE_URL}/codex/device"

# Account usage and banked rate-limit reset endpoints used by the official
# Codex client for ChatGPT-authenticated accounts.
OAUTH_USAGE_URL = f"{CHATGPT_BASE_URL}/wham/usage"
OAUTH_RESET_CREDITS_URL = f"{CHATGPT_BASE_URL}/wham/rate-limit-reset-credits"
OAUTH_RESET_CONSUME_URL = f"{OAUTH_RESET_CREDITS_URL}/consume"

TOKEN_REFRESH_LEEWAY_SECONDS = 300

# New accounts inherit these values; admins can tune each provider window per account.
DEFAULT_FIVE_HOUR_ROTATION_THRESHOLD = 1.0
DEFAULT_WEEKLY_ROTATION_THRESHOLD = 1.0
# Deprecated compatibility alias for older integrations.
DEFAULT_ROTATION_THRESHOLD = DEFAULT_FIVE_HOUR_ROTATION_THRESHOLD
DEFAULT_COOLDOWN_SECONDS = 60
DEFAULT_MAX_FAILOVER_ATTEMPTS = 3
# TODO(concurrency calibration): measure the provider ceiling per model and
# subscription tier with controlled parallel probes, then make this policy
# model/tier-aware instead of relying on this conservative default.
DEFAULT_MAX_CONCURRENT_REQUESTS_PER_ACCOUNT = 3
# A request that finds no immediately eligible account waits briefly for a
# cooldown/refresh worker to make one available.  This avoids converting a
# transient pool dip into a client-visible 503 while keeping the request
# bounded so ASGI workers are not held forever.
DEFAULT_POOL_WAIT_TIMEOUT_SECONDS = 150
DEFAULT_POOL_WAIT_POLL_INTERVAL_SECONDS = 2
# A pre-output provider error is retryable, but it is not proof that the
# account's quota is exhausted.  Keep the transient circuit short and distinct
# from the account's configured 429 cooldown.
DEFAULT_TRANSIENT_UPSTREAM_COOLDOWN_SECONDS = 5


class Settings(BaseSettings):
    ENV: str = "production"
    LOG_LEVEL: str = "INFO"

    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_HOST: str
    POSTGRES_PORT: str
    POSTGRES_DB: str

    def get_postgres_uri(self) -> str:
        return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    FERNET_KEY: str
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 12

    QUOTA_REFRESH_INTERVAL_SECONDS: int = 60
    MODEL_CATALOG_REFRESH_INTERVAL_SECONDS: int = 60 * 60 * 6
    # Warm-up is part of the normal pool-management loop. Deployments can
    # explicitly disable synthetic traffic with WARMUP_ENABLED=false.
    WARMUP_ENABLED: bool = True
    WARMUP_TRIGGER_POOL_USAGE_PCT: float = 0.10
    WARMUP_WEEKLY_RESERVE_PCT: float = 0.90
    WARMUP_MODEL: str = "gpt-5.6-luna"
    # Comma-separated local model allowlist. Blank keeps the built-in lineup.
    ALLOWED_MODELS: str = ""
    DEFAULT_KEY_RATE_LIMIT_PER_MINUTE: int = 0
    MAX_CONCURRENT_REQUESTS_PER_ACCOUNT: int = DEFAULT_MAX_CONCURRENT_REQUESTS_PER_ACCOUNT
    MODEL_CONCURRENCY_LIMITS_JSON: str = ""
    TIER_CONCURRENCY_LIMITS_JSON: str = ""
    POOL_WAIT_TIMEOUT_SECONDS: int = DEFAULT_POOL_WAIT_TIMEOUT_SECONDS
    POOL_WAIT_POLL_INTERVAL_SECONDS: float = DEFAULT_POOL_WAIT_POLL_INTERVAL_SECONDS
    TRANSIENT_UPSTREAM_COOLDOWN_SECONDS: int = DEFAULT_TRANSIENT_UPSTREAM_COOLDOWN_SECONDS
    # Admin fallback health checks perform a one-token generation canary after
    # the catalog request, proving the credential can actually infer.
    FALLBACK_GENERATION_CANARY_ENABLED: bool = True
    FALLBACK_CANARY_MAX_OUTPUT_TOKENS: int = 1
    CODEX_CLIENT_VERSION: str = DEFAULT_CODEX_CLIENT_VERSION
    # JSON array of approved outbound paths. An empty value preserves the
    # historical direct server route. See README for proxy/local target shapes.
    EGRESS_TARGETS_JSON: str = ""
    DEFAULT_EGRESS_MAX_CONCURRENCY: int = 32
    EGRESS_RELAY_USERNAME: str = "proxy"
    EGRESS_RELAY_TOKEN: str = ""

    # Immutable request/response capture. Hot-tier bodies are raw JSON; the
    # retention sidecar moves expired bodies into a persistent Borg repository.
    ARCHIVE_ENABLED: bool = False
    ARCHIVE_REQUIRED: bool = True
    ARCHIVE_S3_ENDPOINT_URL: Optional[str] = None
    ARCHIVE_S3_REGION: str = "us-east-1"
    ARCHIVE_S3_BUCKET: str = "codex-proxy"
    ARCHIVE_S3_PREFIX: str = ""
    ARCHIVE_S3_ACCESS_KEY_ID: Optional[str] = None
    ARCHIVE_S3_SECRET_ACCESS_KEY: Optional[str] = None
    # Separate read-only credentials used only by the admin request explorer.
    ARCHIVE_S3_READ_ACCESS_KEY_ID: Optional[str] = None
    ARCHIVE_S3_READ_SECRET_ACCESS_KEY: Optional[str] = None
    ARCHIVE_S3_FORCE_PATH_STYLE: bool = True
    ARCHIVE_S3_MAX_ATTEMPTS: int = 4
    ARCHIVE_HOT_RETENTION_DAYS: int = 4
    ARCHIVE_RETENTION_ENABLED: bool = False
    ARCHIVE_RETENTION_INTERVAL_SECONDS: int = 3600
    ARCHIVE_BORG_REPOSITORY: str = "/borg/repository"
    ARCHIVE_RETENTION_WORK_DIR: str = "/borg/work"
    ARCHIVE_RETENTION_BATCH_BYTES: int = 536870912
    ARCHIVE_RETENTION_MIN_FREE_GIB: int = 10
    ARCHIVE_RETENTION_DELETE_MINIO: bool = False

    DOMAIN: str

    @property
    def FRONTEND_ORIGIN(self) -> str:
        scheme = "http" if self.DOMAIN.startswith("localhost") else "https"
        return f"{scheme}://{self.DOMAIN}"

    @model_validator(mode="after")
    def _reject_unsafe_secrets(self) -> "Settings":
        """Refuse to start with credentials that make the admin API unsafe."""
        placeholders = {
            "",
            "change-me",
            "change-me-please",
            "change-me-too",
            "changeme",
            "devpassword",
            "password",
            "secret",
            "admin",
        }
        if self.JWT_SECRET.strip().lower() in placeholders or len(self.JWT_SECRET) < 32:
            raise ValueError("JWT_SECRET is unset, a placeholder, or shorter than 32 characters; set a strong random value (see .env.example).")
        if self.ADMIN_PASSWORD.strip().lower() in placeholders or len(self.ADMIN_PASSWORD) < 8:
            raise ValueError("ADMIN_PASSWORD is unset, a placeholder, or shorter than 8 characters; set a strong value (see .env.example).")
        if not self.FERNET_KEY.strip():
            raise ValueError(
                'FERNET_KEY is unset; generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )
        if self.ARCHIVE_ENABLED:
            if not self.ARCHIVE_S3_BUCKET.strip():
                raise ValueError("ARCHIVE_S3_BUCKET must be set when ARCHIVE_ENABLED=true.")
            credentials = (self.ARCHIVE_S3_ACCESS_KEY_ID, self.ARCHIVE_S3_SECRET_ACCESS_KEY)
            if any(credentials) and not all(credentials):
                raise ValueError("Set both ARCHIVE_S3_ACCESS_KEY_ID and ARCHIVE_S3_SECRET_ACCESS_KEY, or neither.")
            read_credentials = (self.ARCHIVE_S3_READ_ACCESS_KEY_ID, self.ARCHIVE_S3_READ_SECRET_ACCESS_KEY)
            if any(read_credentials) and not all(read_credentials):
                raise ValueError("Set both ARCHIVE_S3_READ_ACCESS_KEY_ID and ARCHIVE_S3_READ_SECRET_ACCESS_KEY, or neither.")
            if self.ARCHIVE_S3_MAX_ATTEMPTS < 1:
                raise ValueError("ARCHIVE_S3_MAX_ATTEMPTS must be at least 1.")
            self.ARCHIVE_S3_PREFIX = self.ARCHIVE_S3_PREFIX.strip("/")
        return self

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def concurrency_limit_for(self, model: str | None = None, tier: str | None = None) -> int:
        """Resolve the most specific configured model/tier concurrency cap."""
        for raw, key in ((self.MODEL_CONCURRENCY_LIMITS_JSON, model), (self.TIER_CONCURRENCY_LIMITS_JSON, tier)):
            if not raw.strip() or not key:
                continue
            try:
                values = json.loads(raw)
                value = values.get(key) if isinstance(values, dict) else None
                if value is not None:
                    return max(1, min(int(value), 32))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return max(1, min(int(self.MAX_CONCURRENT_REQUESTS_PER_ACCOUNT), 32))


@lru_cache
def get_settings() -> Settings:
    return Settings()
