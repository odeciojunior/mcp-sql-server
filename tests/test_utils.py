"""Tests for lazy server accessors in utils."""

from unittest.mock import MagicMock, patch

import pytest

from mcp_sql_server import utils


@pytest.fixture(autouse=True)
def reset_getters():
    utils._db_getter = None
    utils._registry_getter = None
    yield
    utils._db_getter = None
    utils._registry_getter = None


def test_get_db_delegates_and_caches():
    fake = MagicMock(return_value="manager")
    with patch("mcp_sql_server.server.get_db", fake):
        assert utils.get_db("analytics") == "manager"
        assert utils.get_db() == "manager"
    fake.assert_any_call("analytics")
    fake.assert_any_call("default")
    assert utils._db_getter is fake


def test_get_registry_delegates_and_caches():
    fake = MagicMock(return_value="registry")
    with patch("mcp_sql_server.server.get_registry", fake):
        assert utils.get_registry() == "registry"
        assert utils.get_registry() == "registry"
    assert fake.call_count == 2
    assert utils._registry_getter is fake
