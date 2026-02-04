"""Configuration loading from .env file."""

from pathlib import Path
import os
import re
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Valid database alias pattern: letters, digits, underscore; must start with letter; max 64 chars
_ALIAS_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")


class PoolConfig(BaseModel):
    """Connection pool configuration from environment variables."""

    min_size: int = Field(default=1, ge=1, description="Minimum pool size")
    max_size: int = Field(default=5, ge=1, description="Maximum pool size")
    idle_timeout: int = Field(default=300, ge=0, description="Idle connection timeout in seconds")
    health_check_interval: int = Field(
        default=30, ge=0, description="Health check interval in seconds"
    )
    acquire_timeout: float = Field(
        default=10.0, ge=0, description="Timeout for acquiring a connection in seconds"
    )
    max_lifetime: int = Field(
        default=3600, ge=0, description="Maximum connection lifetime in seconds"
    )

    @classmethod
    def from_env(cls, env_path: Path | None = None) -> "PoolConfig":
        """Load pool configuration from environment variables."""
        if env_path is None:
            env_path = Path(__file__).parent.parent.parent.parent / ".env"

        load_dotenv(env_path)

        return cls(
            min_size=int(os.getenv("DB_POOL_MIN_SIZE", "1")),
            max_size=int(os.getenv("DB_POOL_MAX_SIZE", "5")),
            idle_timeout=int(os.getenv("DB_POOL_IDLE_TIMEOUT", "300")),
            health_check_interval=int(os.getenv("DB_POOL_HEALTH_CHECK_INTERVAL", "30")),
            acquire_timeout=float(os.getenv("DB_POOL_ACQUIRE_TIMEOUT", "10.0")),
            max_lifetime=int(os.getenv("DB_POOL_MAX_LIFETIME", "3600")),
        )

    @classmethod
    def from_env_prefixed(cls, prefix: str, env_path: Path | None = None) -> "PoolConfig":
        """Load pool configuration from prefixed environment variables.

        Args:
            prefix: Uppercase prefix for env vars (e.g. "ANALYTICS" reads DB_ANALYTICS_POOL_*).
            env_path: Path to .env file.
        """
        if env_path is None:
            env_path = Path(__file__).parent.parent.parent.parent / ".env"

        load_dotenv(env_path)

        p = prefix.upper()
        return cls(
            min_size=int(os.getenv(f"DB_{p}_POOL_MIN_SIZE", "1")),
            max_size=int(os.getenv(f"DB_{p}_POOL_MAX_SIZE", "5")),
            idle_timeout=int(os.getenv(f"DB_{p}_POOL_IDLE_TIMEOUT", "300")),
            health_check_interval=int(os.getenv(f"DB_{p}_POOL_HEALTH_CHECK_INTERVAL", "30")),
            acquire_timeout=float(os.getenv(f"DB_{p}_POOL_ACQUIRE_TIMEOUT", "10.0")),
            max_lifetime=int(os.getenv(f"DB_{p}_POOL_MAX_LIFETIME", "3600")),
        )

    def model_post_init(self, __context: Any) -> None:
        """Validate that min_size <= max_size."""
        if self.min_size > self.max_size:
            raise ValueError(
                f"min_size ({self.min_size}) cannot be greater than max_size ({self.max_size})"
            )


class DatabaseConfig(BaseModel):
    """Database configuration from environment variables."""

    host: str = Field(..., min_length=1, description="SQL Server host")
    port: int = Field(default=1433, gt=0, lt=65536, description="SQL Server port")
    user: str = Field(..., min_length=1, description="Database username")
    password: str = Field(..., min_length=1, description="Database password")
    database: str = Field(..., min_length=1, description="Database name")
    driver: str = Field(
        default="ODBC Driver 17 for SQL Server",
        description="ODBC driver name",
    )
    connection_timeout: int = Field(default=30, description="Connection timeout in seconds")
    query_timeout: int = Field(default=120, description="Query timeout in seconds")
    encrypt: bool = Field(default=False, description="Use encrypted connection")
    trust_cert: bool = Field(default=False, description="Trust server certificate")

    @classmethod
    def from_env(cls, env_path: Path | None = None) -> "DatabaseConfig":
        """Load configuration from .env file."""
        if env_path is None:
            # Look for .env in parent directory (repo root)
            env_path = Path(__file__).parent.parent.parent.parent / ".env"

        load_dotenv(env_path)

        return cls(
            host=os.getenv("DB_HOST", ""),
            port=int(os.getenv("DB_PORT", "1433")),
            user=os.getenv("DB_USER", ""),
            password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_NAME", ""),
            driver=os.getenv("DB_DRIVER", "ODBC Driver 17 for SQL Server"),
            connection_timeout=int(os.getenv("DB_TIMEOUT", "30")),
            query_timeout=int(os.getenv("DB_QUERY_TIMEOUT", "120")),
            encrypt=os.getenv("DB_ENCRYPT", "").lower() in ("true", "1", "yes"),
            trust_cert=os.getenv("DB_TRUST_CERT", "").lower() in ("true", "1", "yes"),
        )

    @classmethod
    def from_env_prefixed(cls, prefix: str, env_path: Path | None = None) -> "DatabaseConfig":
        """Load configuration from prefixed environment variables.

        Args:
            prefix: Uppercase prefix for env vars (e.g. "ANALYTICS" reads DB_ANALYTICS_*).
            env_path: Path to .env file.
        """
        if env_path is None:
            env_path = Path(__file__).parent.parent.parent.parent / ".env"

        load_dotenv(env_path)

        p = prefix.upper()
        return cls(
            host=os.getenv(f"DB_{p}_HOST", ""),
            port=int(os.getenv(f"DB_{p}_PORT", "1433")),
            user=os.getenv(f"DB_{p}_USER", ""),
            password=os.getenv(f"DB_{p}_PASSWORD", ""),
            database=os.getenv(f"DB_{p}_NAME", ""),
            driver=os.getenv(f"DB_{p}_DRIVER", "ODBC Driver 17 for SQL Server"),
            connection_timeout=int(os.getenv(f"DB_{p}_TIMEOUT", "30")),
            query_timeout=int(os.getenv(f"DB_{p}_QUERY_TIMEOUT", "120")),
            encrypt=os.getenv(f"DB_{p}_ENCRYPT", "").lower() in ("true", "1", "yes"),
            trust_cert=os.getenv(f"DB_{p}_TRUST_CERT", "").lower() in ("true", "1", "yes"),
        )

    def get_connection_string(self) -> str:
        """Generate pyodbc connection string."""
        conn_str = (
            f"DRIVER={{{self.driver}}};"
            f"SERVER={self.host},{self.port};"
            f"DATABASE={self.database};"
            f"UID={self.user};"
            f"PWD={self.password};"
            f"Connection Timeout={self.connection_timeout};"
        )
        if self.encrypt:
            conn_str += "Encrypt=yes;"
        if self.trust_cert:
            conn_str += "TrustServerCertificate=yes;"
        return conn_str


def get_database_names(env_path: Path | None = None) -> list[str]:
    """Get all configured database alias names.

    Returns:
        List starting with "default", followed by any aliases from DB_DATABASES.
    """
    if env_path is None:
        env_path = Path(__file__).parent.parent.parent.parent / ".env"
    load_dotenv(env_path)

    names = ["default"]
    db_databases = os.getenv("DB_DATABASES", "").strip()
    if db_databases:
        for alias in db_databases.split(","):
            alias = alias.strip()
            if not alias:
                continue
            if not _ALIAS_PATTERN.match(alias):
                raise ValueError(
                    f"Invalid database alias '{alias}': must match [a-zA-Z][a-zA-Z0-9_]*"
                )
            if alias.lower() == "default":
                continue  # skip if someone explicitly lists "default"
            names.append(alias)
    return names


def load_all_database_configs(
    env_path: Path | None = None,
) -> dict[str, "DatabaseConfig"]:
    """Load DatabaseConfig for all configured databases.

    Returns:
        Mapping of alias -> DatabaseConfig. Always includes "default".
    """
    names = get_database_names(env_path)
    configs: dict[str, DatabaseConfig] = {}
    for name in names:
        if name == "default":
            configs[name] = DatabaseConfig.from_env(env_path)
        else:
            configs[name] = DatabaseConfig.from_env_prefixed(name, env_path)
    return configs


def load_all_pool_configs(
    env_path: Path | None = None,
) -> dict[str, PoolConfig]:
    """Load PoolConfig for all configured databases.

    Returns:
        Mapping of alias -> PoolConfig. Always includes "default".
    """
    names = get_database_names(env_path)
    configs: dict[str, PoolConfig] = {}
    for name in names:
        if name == "default":
            configs[name] = PoolConfig.from_env(env_path)
        else:
            configs[name] = PoolConfig.from_env_prefixed(name, env_path)
    return configs


@lru_cache(maxsize=1)
def get_query_dir() -> Path:
    """Get the query directory path from environment or default.

    Returns:
        Path to the query directory.
    """
    # Load env if not already loaded
    env_path = Path(__file__).parent.parent.parent.parent / ".env"
    load_dotenv(env_path)

    query_dir_str = os.getenv("QUERY_DIR")
    if query_dir_str:
        return Path(query_dir_str).resolve()

    # Default: query/ directory at repository root (4 levels up from this file)
    default_dir = Path(__file__).parent.parent.parent.parent / "query"
    return default_dir.resolve()
