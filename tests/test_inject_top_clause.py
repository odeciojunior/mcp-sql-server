"""Tests for _inject_top_clause() function in query_execution module."""

import pytest

from sql_playground_mcp.tools.query_execution import _inject_top_clause


class TestInjectTopClause:
    """Tests for _inject_top_clause() function."""

    def test_basic_select(self):
        """Test basic SELECT statement."""
        sql = "SELECT * FROM users"
        result = _inject_top_clause(sql, 100)

        assert "SELECT TOP 101 *" in result
        assert "FROM (SELECT * FROM users) AS _limited_query" in result

    def test_select_with_where(self):
        """Test SELECT with WHERE clause."""
        sql = "SELECT * FROM users WHERE active = 1"
        result = _inject_top_clause(sql, 50)

        assert "SELECT TOP 51 *" in result
        assert "FROM (SELECT * FROM users WHERE active = 1) AS _limited_query" in result

    def test_select_with_order_by(self):
        """Test SELECT with ORDER BY clause."""
        sql = "SELECT * FROM users ORDER BY name"
        result = _inject_top_clause(sql, 100)

        assert "SELECT TOP 101 *" in result
        assert "FROM (SELECT * FROM users ORDER BY name) AS _limited_query" in result

    def test_select_with_join(self):
        """Test SELECT with JOIN clause."""
        sql = "SELECT u.*, o.* FROM users u JOIN orders o ON u.id = o.user_id"
        result = _inject_top_clause(sql, 200)

        assert "SELECT TOP 201 *" in result
        assert "FROM (SELECT u.*, o.* FROM users u JOIN orders o ON u.id = o.user_id) AS _limited_query" in result

    def test_with_cte_single(self):
        """Test WITH CTE query (single CTE)."""
        sql = "WITH cte AS (SELECT * FROM users) SELECT * FROM cte"
        result = _inject_top_clause(sql, 100)

        assert "SELECT TOP 101 *" in result
        assert "FROM (WITH cte AS (SELECT * FROM users) SELECT * FROM cte) AS _limited_query" in result

    def test_with_cte_multiple(self):
        """Test WITH multiple CTEs."""
        sql = "WITH cte1 AS (SELECT id FROM users), cte2 AS (SELECT id FROM orders) SELECT * FROM cte1 JOIN cte2 ON cte1.id = cte2.id"
        result = _inject_top_clause(sql, 100)

        assert "SELECT TOP 101 *" in result
        assert sql in result
        assert "AS _limited_query" in result

    def test_subquery(self):
        """Test query with subquery."""
        sql = "SELECT * FROM (SELECT * FROM users) AS sub"
        result = _inject_top_clause(sql, 100)

        assert "SELECT TOP 101 *" in result
        assert "FROM (SELECT * FROM (SELECT * FROM users) AS sub) AS _limited_query" in result

    def test_union_query(self):
        """Test UNION ALL query."""
        sql = "SELECT * FROM users UNION ALL SELECT * FROM admins"
        result = _inject_top_clause(sql, 100)

        assert "SELECT TOP 101 *" in result
        assert "FROM (SELECT * FROM users UNION ALL SELECT * FROM admins) AS _limited_query" in result

    def test_query_with_existing_top(self):
        """Test query that already has TOP clause - verifies wrapping behavior."""
        sql = "SELECT TOP 10 * FROM users"
        result = _inject_top_clause(sql, 100)

        # The function wraps the query, so original TOP is preserved inside subquery
        assert "SELECT TOP 101 *" in result
        assert "FROM (SELECT TOP 10 * FROM users) AS _limited_query" in result

    def test_limit_value_1(self):
        """Test with limit=1 (minimum value)."""
        sql = "SELECT * FROM users"
        result = _inject_top_clause(sql, 1)

        # Should fetch limit + 1 = 2 rows to detect truncation
        assert "SELECT TOP 2 *" in result

    def test_limit_value_100(self):
        """Test with limit=100."""
        sql = "SELECT * FROM users"
        result = _inject_top_clause(sql, 100)

        assert "SELECT TOP 101 *" in result

    def test_limit_value_10000(self):
        """Test with limit=10000 (maximum value)."""
        sql = "SELECT * FROM users"
        result = _inject_top_clause(sql, 10000)

        assert "SELECT TOP 10001 *" in result

    def test_complex_query_with_all_clauses(self):
        """Test complex query with multiple clauses."""
        sql = """SELECT u.id, u.name, COUNT(o.id) as order_count
                 FROM users u
                 LEFT JOIN orders o ON u.id = o.user_id
                 WHERE u.active = 1
                 GROUP BY u.id, u.name
                 HAVING COUNT(o.id) > 0
                 ORDER BY order_count DESC"""
        result = _inject_top_clause(sql, 50)

        assert "SELECT TOP 51 *" in result
        assert sql in result
        assert "AS _limited_query" in result

    def test_preserves_original_sql_intact(self):
        """Test that original SQL is preserved intact inside the wrapper."""
        sql = "SELECT id, name FROM users WHERE status = 'active'"
        result = _inject_top_clause(sql, 100)

        # Original SQL should appear exactly as-is inside parentheses
        assert f"FROM ({sql}) AS _limited_query" in result

    def test_result_structure(self):
        """Test the overall structure of the result."""
        sql = "SELECT * FROM users"
        result = _inject_top_clause(sql, 100)

        # Should start with SELECT TOP
        assert result.startswith("SELECT TOP 101 * FROM (")
        # Should end with the alias
        assert result.endswith(") AS _limited_query")

    def test_select_with_distinct(self):
        """Test SELECT DISTINCT query."""
        sql = "SELECT DISTINCT name FROM users"
        result = _inject_top_clause(sql, 100)

        assert "SELECT TOP 101 *" in result
        assert "FROM (SELECT DISTINCT name FROM users) AS _limited_query" in result

    def test_select_with_aggregation(self):
        """Test SELECT with aggregate functions."""
        sql = "SELECT department, COUNT(*) as cnt FROM employees GROUP BY department"
        result = _inject_top_clause(sql, 100)

        assert "SELECT TOP 101 *" in result
        assert sql in result
