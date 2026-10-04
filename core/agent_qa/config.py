"""Validated configuration for the core service and MCP process."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

CORE_HOST = "127.0.0.1"
CORE_PORT = 8765
CORE_BASE_URL = f"http://{CORE_HOST}:{CORE_PORT}"

HTTP_TIMEOUT_SECONDS = 5.0
HTTP_CONNECTION_RETRIES = 1

DEFAULT_DATA_DIR = Path.home() / ".agent-qa"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "memory.db"
DEFAULT_LOG_DIR = DEFAULT_DATA_DIR / "logs"


class Settings(BaseSettings):
    """Load service settings from AGENT_QA-prefixed environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="AGENT_QA_",
        env_file=None,
        case_sensitive=False,
        extra="ignore",
        frozen=True,
        validate_default=True,
    )

    db_path: Path = DEFAULT_DB_PATH
    log_dir: Path = DEFAULT_LOG_DIR
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    log_backup_count: int = Field(default=5, ge=1, le=100)

    flaky_threshold: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    sqlite_busy_timeout_ms: int = Field(default=5000, ge=1, le=60000)

    mcp_transport: Literal["stdio", "sse"] = "stdio"
    mcp_port: int = Field(default=8766, ge=1024, le=65535)

    @field_validator("db_path", "log_dir", mode="before")
    @classmethod
    def validate_path(cls, value: object) -> Path:
        """Normalize a filesystem path without creating it."""
        if not isinstance(value, (str, Path)):
            raise ValueError("Path must be a string or pathlib.Path.")
        if isinstance(value, str) and not value.strip():
            raise ValueError("Path must not be empty.")
        if str(value).strip() == ":memory:":
            raise ValueError("Use a filesystem path for persistent SQLite storage.")
        return Path(value).expanduser().resolve()

    @field_validator("db_path")
    @classmethod
    def validate_db_path(cls, value: Path) -> Path:
        """Reject an existing directory as the database destination."""
        if value.is_dir():
            raise ValueError("Database path must refer to a file.")
        return value

    @field_validator("log_dir")
    @classmethod
    def validate_log_dir(cls, value: Path) -> Path:
        """Reject an existing file as the log directory."""
        if value.exists() and not value.is_dir():
            raise ValueError("Log directory must refer to a directory.")
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> str:
        """Accept case-insensitive logging level names."""
        if not isinstance(value, str):
            raise ValueError("Log level must be a string.")
        return value.strip().upper()

    @field_validator("mcp_port")
    @classmethod
    def validate_mcp_port(cls, value: int) -> int:
        """Keep the MCP listener separate from the core service listener."""
        if value == CORE_PORT:
            raise ValueError("MCP port must differ from the core service port.")
        return value

    @property
    def host(self) -> str:
        """Return the fixed loopback bind address."""
        return CORE_HOST

    @property
    def port(self) -> int:
        """Return the fixed core service port."""
        return CORE_PORT

    @property
    def base_url(self) -> str:
        """Return the core HTTP origin."""
        return CORE_BASE_URL

    @property
    def log_path(self) -> Path:
        """Return the rotating JSON log path."""
        return self.log_dir / "agent-qa.log"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached process-wide settings."""
    return Settings()