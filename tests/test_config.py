"""Tests for configuration loading."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from mcp_sql_server.config import DatabaseConfig


class TestDatabaseConfigDefaults:
    """Tests for DatabaseConfig default values."""

    def test_default_port(self):
        config = DatabaseConfig(
            host="localhost",
            user="test",
            password="pass",
            database="db",
        )
        assert config.port == 1433

    def test_default_driver(self):
        config = DatabaseConfig(
            host="localhost",
            user="test",
            password="pass",
            database="db",
        )
        assert config.driver == "ODBC Driver 17 for SQL Server"

    def test_default_connection_timeout(self):
        config = DatabaseConfig(
            host="localhost",
            user="test",
            password="pass",
            database="db",
        )
        assert config.connection_timeout == 30

    def test_default_query_timeout(self):
        config = DatabaseConfig(
            host="localhost",
            user="test",
            password="pass",
            database="db",
        )
        assert config.query_timeout == 120

    def test_default_encrypt_false(self):
        config = DatabaseConfig(
            host="localhost",
            user="test",
            password="pass",
            database="db",
        )
        assert config.encrypt is False

    def test_default_trust_cert_false(self):
        config = DatabaseConfig(
            host="localhost",
            user="test",
            password="pass",
            database="db",
        )
        assert config.trust_cert is False


class TestDatabaseConfigFromEnv:
    """Tests for DatabaseConfig.from_env() method."""

    def test_from_env_with_complete_vars(self, env_with_vars):
        config = DatabaseConfig.from_env()
        assert config.host == "test-server.example.com"
        assert config.port == 1433
        assert config.user == "test_user"
        assert config.password == "test_password123"
        assert config.database == "test_database"

    def test_from_env_with_minimal_vars(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("DB_HOST=localhost\nDB_USER=sa\nDB_PASSWORD=password\nDB_NAME=master\n")
        with patch.dict(os.environ, {}, clear=True):
            config = DatabaseConfig.from_env(env_path=env_file)
            assert config.host == "localhost"
            assert config.user == "sa"
            assert config.password == "password"
            assert config.database == "master"
            # Defaults should apply
            assert config.port == 1433
            assert config.driver == "ODBC Driver 17 for SQL Server"

    def test_from_env_encrypt_true_variations(self):
        for value in ["true", "True", "TRUE", "1", "yes", "Yes", "YES"]:
            with patch.dict(os.environ, {
                "DB_HOST": "h", "DB_USER": "u", "DB_PASSWORD": "p",
                "DB_NAME": "d", "DB_ENCRYPT": value
            }, clear=True):
                config = DatabaseConfig.from_env()
                assert config.encrypt is True, f"Failed for value: {value}"

    def test_from_env_encrypt_false_variations(self):
        for value in ["false", "False", "FALSE", "0", "no", "No", ""]:
            with patch.dict(os.environ, {
                "DB_HOST": "h", "DB_USER": "u", "DB_PASSWORD": "p",
                "DB_NAME": "d", "DB_ENCRYPT": value
            }, clear=True):
                config = DatabaseConfig.from_env()
                assert config.encrypt is False, f"Failed for value: {value}"

    def test_from_env_trust_cert_true(self):
        with patch.dict(os.environ, {
            "DB_HOST": "h", "DB_USER": "u", "DB_PASSWORD": "p",
            "DB_NAME": "d", "DB_TRUST_CERT": "true"
        }, clear=True):
            config = DatabaseConfig.from_env()
            assert config.trust_cert is True

    def test_from_env_custom_port(self):
        with patch.dict(os.environ, {
            "DB_HOST": "h", "DB_USER": "u", "DB_PASSWORD": "p",
            "DB_NAME": "d", "DB_PORT": "5433"
        }, clear=True):
            config = DatabaseConfig.from_env()
            assert config.port == 5433

    def test_from_env_custom_driver(self):
        with patch.dict(os.environ, {
            "DB_HOST": "h", "DB_USER": "u", "DB_PASSWORD": "p",
            "DB_NAME": "d", "DB_DRIVER": "ODBC Driver 18 for SQL Server"
        }, clear=True):
            config = DatabaseConfig.from_env()
            assert config.driver == "ODBC Driver 18 for SQL Server"

    def test_from_env_missing_host_raises_validation_error(self, tmp_path):
        """Missing host env var raises ValidationError (required field)."""
        env_file = tmp_path / ".env"
        env_file.write_text("DB_USER=u\nDB_PASSWORD=p\nDB_NAME=d\n")
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValidationError) as exc_info:
                DatabaseConfig.from_env(env_path=env_file)
            assert "host" in str(exc_info.value)

    def test_from_env_missing_user_raises_validation_error(self, tmp_path):
        """Missing user env var raises ValidationError (required field)."""
        env_file = tmp_path / ".env"
        env_file.write_text("DB_HOST=h\nDB_PASSWORD=p\nDB_NAME=d\n")
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValidationError) as exc_info:
                DatabaseConfig.from_env(env_path=env_file)
            assert "user" in str(exc_info.value)

    def test_from_env_missing_password_raises_validation_error(self, tmp_path):
        """Missing password env var raises ValidationError (required field)."""
        env_file = tmp_path / ".env"
        env_file.write_text("DB_HOST=h\nDB_USER=u\nDB_NAME=d\n")
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValidationError) as exc_info:
                DatabaseConfig.from_env(env_path=env_file)
            assert "password" in str(exc_info.value)

    def test_from_env_missing_database_raises_validation_error(self, tmp_path):
        """Missing database env var raises ValidationError (required field)."""
        env_file = tmp_path / ".env"
        env_file.write_text("DB_HOST=h\nDB_USER=u\nDB_PASSWORD=p\n")
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValidationError) as exc_info:
                DatabaseConfig.from_env(env_path=env_file)
            assert "database" in str(exc_info.value)

    def test_from_env_with_custom_path(self, sample_env):
        saved = {k: v for k, v in os.environ.items() if k.startswith("SQL_SERVER_")}
        for key in saved:
            del os.environ[key]
        try:
            config = DatabaseConfig.from_env(env_path=sample_env)
        finally:
            os.environ.update(saved)
        assert config.host == "localhost"
        assert config.user == "testuser"
        assert config.password == "testpass"
        assert config.database == "testdb"


class TestEnvVarPrecedence:
    """Tests for SQL_SERVER_* vs DB_* precedence in DatabaseConfig.from_env().

    Precedence, highest first:
      1. DB_* set in the real process environment (explicit config, e.g. an MCP
         client injecting them via .mcp.json)
      2. SQL_SERVER_* (injected via .claude/settings.local.json)
      3. DB_* read from the .env file
    """

    def test_process_env_db_beats_sql_server(self, tmp_path):
        """DB_* injected into the process env wins over SQL_SERVER_*."""
        env_file = tmp_path / ".env"
        env_file.write_text("")
        with patch.dict(
            os.environ,
            {
                "DB_HOST": "from-mcp-json",
                "DB_USER": "mcp_user",
                "DB_PASSWORD": "mcp_pass",
                "DB_NAME": "McpDb",
                "DB_PORT": "1500",
                "SQL_SERVER_HOST": "from-settings",
                "SQL_SERVER_USER": "settings_user",
                "SQL_SERVER_PASSWORD": "settings_pass",
                "SQL_SERVER_DATABASE": "SettingsDb",
                "SQL_SERVER_PORT": "1600",
            },
            clear=True,
        ):
            config = DatabaseConfig.from_env(env_path=env_file)
        assert config.host == "from-mcp-json"
        assert config.user == "mcp_user"
        assert config.password == "mcp_pass"
        assert config.database == "McpDb"
        assert config.port == 1500

    def test_sql_server_beats_dotenv_file(self, tmp_path):
        """SQL_SERVER_* still wins over DB_* that come from the .env file."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "DB_HOST=from-dotenv\nDB_USER=dotenv_user\n"
            "DB_PASSWORD=dotenv_pass\nDB_NAME=DotenvDb\n"
        )
        with patch.dict(
            os.environ,
            {
                "SQL_SERVER_HOST": "from-settings",
                "SQL_SERVER_USER": "settings_user",
                "SQL_SERVER_PASSWORD": "settings_pass",
                "SQL_SERVER_DATABASE": "SettingsDb",
            },
            clear=True,
        ):
            config = DatabaseConfig.from_env(env_path=env_file)
        assert config.host == "from-settings"
        assert config.user == "settings_user"
        assert config.database == "SettingsDb"

    def test_sql_server_used_when_no_db_vars(self, tmp_path):
        """SQL_SERVER_* is used when no DB_* exist anywhere."""
        env_file = tmp_path / ".env"
        env_file.write_text("")
        with patch.dict(
            os.environ,
            {
                "SQL_SERVER_HOST": "only-settings",
                "SQL_SERVER_USER": "u",
                "SQL_SERVER_PASSWORD": "p",
                "SQL_SERVER_DATABASE": "D",
            },
            clear=True,
        ):
            config = DatabaseConfig.from_env(env_path=env_file)
        assert config.host == "only-settings"
        assert config.database == "D"

    def test_empty_process_db_var_falls_through(self, tmp_path):
        """An empty DB_* in the process env does not mask SQL_SERVER_*."""
        env_file = tmp_path / ".env"
        env_file.write_text("")
        with patch.dict(
            os.environ,
            {
                "DB_HOST": "",
                "SQL_SERVER_HOST": "from-settings",
                "SQL_SERVER_USER": "u",
                "SQL_SERVER_PASSWORD": "p",
                "SQL_SERVER_DATABASE": "D",
            },
            clear=True,
        ):
            config = DatabaseConfig.from_env(env_path=env_file)
        assert config.host == "from-settings"

    def test_missing_everywhere_raises(self, tmp_path):
        """No DB_* and no SQL_SERVER_* anywhere raises ValidationError."""
        env_file = tmp_path / ".env"
        env_file.write_text("")
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValidationError):
                DatabaseConfig.from_env(env_path=env_file)


class TestGetConnectionString:
    """Tests for DatabaseConfig.get_connection_string() method."""

    def test_basic_connection_string(self, sample_config):
        conn_str = sample_config.get_connection_string()
        assert "DRIVER={ODBC Driver 17 for SQL Server}" in conn_str
        assert "SERVER=test-host,1433" in conn_str
        assert "DATABASE=test-db" in conn_str
        assert "UID=test-user" in conn_str
        assert "PWD=test-pass" in conn_str
        assert "Connection Timeout=30" in conn_str

    def test_connection_string_no_encrypt(self, sample_config):
        conn_str = sample_config.get_connection_string()
        assert "Encrypt=" not in conn_str

    def test_connection_string_no_trust_cert(self, sample_config):
        conn_str = sample_config.get_connection_string()
        assert "TrustServerCertificate=" not in conn_str

    def test_connection_string_with_encrypt(self, sample_config_with_ssl):
        conn_str = sample_config_with_ssl.get_connection_string()
        assert "Encrypt=yes" in conn_str

    def test_connection_string_with_trust_cert(self, sample_config_with_ssl):
        conn_str = sample_config_with_ssl.get_connection_string()
        assert "TrustServerCertificate=yes" in conn_str

    def test_connection_string_custom_port(self):
        config = DatabaseConfig(
            host="myserver",
            port=5433,
            user="user",
            password="pass",
            database="db",
        )
        conn_str = config.get_connection_string()
        assert "SERVER=myserver,5433" in conn_str

    def test_connection_string_custom_timeout(self):
        config = DatabaseConfig(
            host="myserver",
            user="user",
            password="pass",
            database="db",
            connection_timeout=60,
        )
        conn_str = config.get_connection_string()
        assert "Connection Timeout=60" in conn_str


class TestDatabaseConfigValidation:
    """Tests for DatabaseConfig field validation."""

    def test_port_validation_zero_rejected(self):
        """Port value of 0 should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            DatabaseConfig(
                host="localhost",
                port=0,
                user="user",
                password="pass",
                database="db",
            )
        assert "port" in str(exc_info.value)

    def test_port_validation_negative_rejected(self):
        """Negative port value should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            DatabaseConfig(
                host="localhost",
                port=-1,
                user="user",
                password="pass",
                database="db",
            )
        assert "port" in str(exc_info.value)

    def test_port_validation_too_high_rejected(self):
        """Port value >= 65536 should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            DatabaseConfig(
                host="localhost",
                port=65536,
                user="user",
                password="pass",
                database="db",
            )
        assert "port" in str(exc_info.value)

    def test_port_validation_max_valid(self):
        """Port value of 65535 should be valid."""
        config = DatabaseConfig(
            host="localhost",
            port=65535,
            user="user",
            password="pass",
            database="db",
        )
        assert config.port == 65535

    def test_port_validation_min_valid(self):
        """Port value of 1 should be valid."""
        config = DatabaseConfig(
            host="localhost",
            port=1,
            user="user",
            password="pass",
            database="db",
        )
        assert config.port == 1

    def test_empty_host_rejected(self):
        """Empty host string should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            DatabaseConfig(
                host="",
                user="user",
                password="pass",
                database="db",
            )
        assert "host" in str(exc_info.value)

    def test_empty_user_rejected(self):
        """Empty user string should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            DatabaseConfig(
                host="localhost",
                user="",
                password="pass",
                database="db",
            )
        assert "user" in str(exc_info.value)

    def test_empty_password_rejected(self):
        """Empty password string should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            DatabaseConfig(
                host="localhost",
                user="user",
                password="",
                database="db",
            )
        assert "password" in str(exc_info.value)

    def test_empty_database_rejected(self):
        """Empty database string should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            DatabaseConfig(
                host="localhost",
                user="user",
                password="pass",
                database="",
            )
        assert "database" in str(exc_info.value)
