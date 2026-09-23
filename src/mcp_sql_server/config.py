"""Configuration loading from .env file."""

from pathlib import Path
import os
import re
from functools import lru_cache
from typing import Any

from dotenv import dotenv_values
from pydantic import BaseModel, Field, ValidationError

# Valid database alias pattern: letters, digits, underscore; must start with letter; max 64 chars
_ALIAS_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")

# Repository-root .env (works for editable installs)
DEFAULT_ENV_PATH = Path(__file__).parent.parent.parent / ".env"


def _parse_int(raw: str, name: str) -> int:
    """Parse an integer env value; the error names the variable, never the value."""
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None


def _parse_float(raw: str, name: str) -> float:
    """Parse a numeric env value; the error names the variable, never the value."""
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number") from None


def _read_dotenv(env_path: Path | None) -> dict[str, str]:
    """Return the .env file's values without touching os.environ.

    Loading .env into os.environ would make file values indistinguishable
    from explicitly set ones and break the documented precedence.
    """
    values = dotenv_values(env_path or DEFAULT_ENV_PATH)
    return {k: v for k, v in values.items() if v is not None}


def _env(name: str, file_values: dict[str, str], default: str = "") -> str:
    """Process env first, then the .env file, then default (empty counts as unset)."""
    return os.environ.get(name) or file_values.get(name) or default


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
        file_values = _read_dotenv(env_path)

        return cls(
            min_size=_parse_int(_env("DB_POOL_MIN_SIZE", file_values, "1"), "DB_POOL_MIN_SIZE"),
            max_size=_parse_int(_env("DB_POOL_MAX_SIZE", file_values, "5"), "DB_POOL_MAX_SIZE"),
            idle_timeout=_parse_int(
                _env("DB_POOL_IDLE_TIMEOUT", file_values, "300"), "DB_POOL_IDLE_TIMEOUT"
            ),
            health_check_interval=_parse_int(
                _env("DB_POOL_HEALTH_CHECK_INTERVAL", file_values, "30"),
                "DB_POOL_HEALTH_CHECK_INTERVAL",
            ),
            acquire_timeout=_parse_float(
                _env("DB_POOL_ACQUIRE_TIMEOUT", file_values, "10.0"), "DB_POOL_ACQUIRE_TIMEOUT"
            ),
            max_lifetime=_parse_int(
                _env("DB_POOL_MAX_LIFETIME", file_values, "3600"), "DB_POOL_MAX_LIFETIME"
            ),
        )

    @classmethod
    def from_env_prefixed(cls, prefix: str, env_path: Path | None = None) -> "PoolConfig":
        """Load pool configuration from prefixed environment variables.

        Args:
            prefix: Uppercase prefix for env vars (e.g. "ANALYTICS" reads DB_ANALYTICS_POOL_*).
            env_path: Path to .env file.
        """
        file_values = _read_dotenv(env_path)

        p = prefix.upper()

        def var(suffix: str) -> str:
            return f"DB_{p}_POOL_{suffix}"

        return cls(
            min_size=_parse_int(_env(var("MIN_SIZE"), file_values, "1"), var("MIN_SIZE")),
            max_size=_parse_int(_env(var("MAX_SIZE"), file_values, "5"), var("MAX_SIZE")),
            idle_timeout=_parse_int(
                _env(var("IDLE_TIMEOUT"), file_values, "300"), var("IDLE_TIMEOUT")
            ),
            health_check_interval=_parse_int(
                _env(var("HEALTH_CHECK_INTERVAL"), file_values, "30"), var("HEALTH_CHECK_INTERVAL")
            ),
            acquire_timeout=_parse_float(
                _env(var("ACQUIRE_TIMEOUT"), file_values, "10.0"), var("ACQUIRE_TIMEOUT")
            ),
            max_lifetime=_parse_int(
                _env(var("MAX_LIFETIME"), file_values, "3600"), var("MAX_LIFETIME")
            ),
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
        """Load configuration from .env file.

        Precedence, highest first:

        1. DB_* set in the process environment (explicit configuration, e.g.
           an MCP client's env block).
        2. SQL_SERVER_* (process environment, then .env).
        3. DB_* from the .env file.

        The .env file is read with dotenv_values and never merged into
        os.environ, so file values can never masquerade as explicit ones.
        """
        file_values = _read_dotenv(env_path)

        def _get(sql_server_key: str, db_key: str, default: str = "") -> str:
            explicit = os.environ.get(db_key)
            if explicit:
                return explicit
            value = _env(sql_server_key, file_values) or file_values.get(db_key)
            return value if value else default

        return cls(
            host=_get("SQL_SERVER_HOST", "DB_HOST"),
            port=_parse_int(_get("SQL_SERVER_PORT", "DB_PORT", "1433"), "SQL_SERVER_PORT/DB_PORT"),
            user=_get("SQL_SERVER_USER", "DB_USER"),
            password=_get("SQL_SERVER_PASSWORD", "DB_PASSWORD"),
            database=_get("SQL_SERVER_DATABASE", "DB_NAME"),
            driver=_get("SQL_SERVER_DRIVER", "DB_DRIVER", "ODBC Driver 17 for SQL Server"),
            connection_timeout=_parse_int(_env("DB_TIMEOUT", file_values, "30"), "DB_TIMEOUT"),
            query_timeout=_parse_int(
                _env("DB_QUERY_TIMEOUT", file_values, "120"), "DB_QUERY_TIMEOUT"
            ),
            encrypt=_get("SQL_SERVER_ENCRYPT", "DB_ENCRYPT").lower() in ("true", "1", "yes"),
            trust_cert=_get("SQL_SERVER_TRUST_CERT", "DB_TRUST_CERT").lower() in ("true", "1", "yes"),
        )

    @classmethod
    def from_env_prefixed(cls, prefix: str, env_path: Path | None = None) -> "DatabaseConfig":
        """Load configuration from prefixed environment variables.

        Args:
            prefix: Uppercase prefix for env vars (e.g. "ANALYTICS" reads DB_ANALYTICS_*).
            env_path: Path to .env file.
        """
        file_values = _read_dotenv(env_path)

        p = prefix.upper()
        return cls(
            host=_env(f"DB_{p}_HOST", file_values),
            port=_parse_int(_env(f"DB_{p}_PORT", file_values, "1433"), f"DB_{p}_PORT"),
            user=_env(f"DB_{p}_USER", file_values),
            password=_env(f"DB_{p}_PASSWORD", file_values),
            database=_env(f"DB_{p}_NAME", file_values),
            driver=_env(f"DB_{p}_DRIVER", file_values, "ODBC Driver 17 for SQL Server"),
            connection_timeout=_parse_int(
                _env(f"DB_{p}_TIMEOUT", file_values, "30"), f"DB_{p}_TIMEOUT"
            ),
            query_timeout=_parse_int(
                _env(f"DB_{p}_QUERY_TIMEOUT", file_values, "120"), f"DB_{p}_QUERY_TIMEOUT"
            ),
            encrypt=_env(f"DB_{p}_ENCRYPT", file_values).lower() in ("true", "1", "yes"),
            trust_cert=_env(f"DB_{p}_TRUST_CERT", file_values).lower() in ("true", "1", "yes"),
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
    names = ["default"]
    db_databases = _env("DB_DATABASES", _read_dotenv(env_path)).strip()
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


def load_database_config(name: str, env_path: Path | None = None) -> DatabaseConfig:
    """Load the DatabaseConfig for one alias ("default" uses DB_*/SQL_SERVER_*)."""
    if name == "default":
        return DatabaseConfig.from_env(env_path)
    return DatabaseConfig.from_env_prefixed(name, env_path)


def load_pool_config(name: str, env_path: Path | None = None) -> PoolConfig:
    """Load the PoolConfig for one alias."""
    if name == "default":
        return PoolConfig.from_env(env_path)
    return PoolConfig.from_env_prefixed(name, env_path)


def load_all_database_configs(
    env_path: Path | None = None,
) -> dict[str, "DatabaseConfig"]:
    """Load DatabaseConfig for all configured databases.

    Returns:
        Mapping of alias -> DatabaseConfig. Always includes "default".
    """
    return {name: load_database_config(name, env_path) for name in get_database_names(env_path)}


def load_all_pool_configs(
    env_path: Path | None = None,
) -> dict[str, PoolConfig]:
    """Load PoolConfig for all configured databases.

    Returns:
        Mapping of alias -> PoolConfig. Always includes "default".
    """
    return {name: load_pool_config(name, env_path) for name in get_database_names(env_path)}


def describe_config_error(exc: ValueError) -> str:
    """Describe a config error without any input values.

    pydantic errors become "field: error_type" pairs (e.g.
    "password: string_too_short"). Other ValueErrors raised here name only
    variables or aliases, so their text is used as-is.
    """
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors(include_input=False, include_context=False, include_url=False):
            loc = ".".join(str(p) for p in err["loc"]) or "config"
            parts.append(f"{loc}: {err['type']}")
        return "; ".join(parts)
    return str(exc)


@lru_cache(maxsize=1)
def get_query_dir() -> Path:
    """Get the query directory path from environment or default.

    Returns:
        Path to the query directory.
    """
    query_dir_str = _env("QUERY_DIR", _read_dotenv(DEFAULT_ENV_PATH))
    if query_dir_str:
        return Path(query_dir_str).resolve()

    # Default: query/ directory at repository root (4 levels up from this file)
    default_dir = Path(__file__).parent.parent.parent / "query"
    return default_dir.resolve()
