"""Tests for query directory configuration."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from sql_playground_mcp.config import get_query_dir


class TestGetQueryDir:
    """Tests for get_query_dir function."""

    def test_default_query_dir(self):
        # Clear the cache
        get_query_dir.cache_clear()

        with patch.dict(os.environ, {}, clear=False):
            # Remove QUERY_DIR if present
            os.environ.pop("QUERY_DIR", None)
            query_dir = get_query_dir()

            # Should be query/ relative to the project root
            # The function returns an absolute path
            assert query_dir.name == "query"
            assert query_dir.is_absolute()

        # Clear cache for other tests
        get_query_dir.cache_clear()

    def test_custom_query_dir_from_env(self, tmp_path):
        # Clear the cache
        get_query_dir.cache_clear()

        custom_dir = tmp_path / "custom_queries"
        custom_dir.mkdir()

        with patch.dict(os.environ, {"QUERY_DIR": str(custom_dir)}):
            query_dir = get_query_dir()
            assert query_dir == custom_dir.resolve()

        # Clear cache for other tests
        get_query_dir.cache_clear()

    def test_query_dir_is_absolute(self):
        get_query_dir.cache_clear()

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("QUERY_DIR", None)
            query_dir = get_query_dir()
            assert query_dir.is_absolute()

        get_query_dir.cache_clear()

    def test_query_dir_is_cached(self, tmp_path):
        get_query_dir.cache_clear()

        custom_dir1 = tmp_path / "queries1"
        custom_dir1.mkdir()

        with patch.dict(os.environ, {"QUERY_DIR": str(custom_dir1)}):
            result1 = get_query_dir()
            result2 = get_query_dir()

            # Same object returned (cached)
            assert result1 is result2

        get_query_dir.cache_clear()

    def test_query_dir_with_relative_path(self, tmp_path):
        get_query_dir.cache_clear()

        # Create a relative path scenario
        with patch.dict(os.environ, {"QUERY_DIR": "./relative/path"}):
            query_dir = get_query_dir()
            # Should resolve to absolute path
            assert query_dir.is_absolute()

        get_query_dir.cache_clear()
