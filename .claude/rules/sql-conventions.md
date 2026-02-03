# SQL Conventions and Patterns

## Query Patterns

### WITH(NOLOCK) Hints
All read queries use `WITH(NOLOCK)` hints for better concurrency:
```sql
SELECT * FROM Req r WITH(NOLOCK)
```

### UNION ALL Structure
The custom views use UNION ALL to combine:
1. Open requests from `Req` + `ReqHistorico` tables
2. Concluded requests from `ReqConcluida` + `ReqHistConcluida` tables

This ensures all requests are captured regardless of their status.

### Form Filtering
Views filter by form code (`cdFrm`) and task (`idTar`):
```sql
WHERE f.cdFrm = '27'    -- Infrastructure form
  AND r.idTar = '529'   -- Specific task type
```

### Date Formatting
Use `CONVERT()` for better performance (avoid `FORMAT()` which is 10-50x slower):
```sql
-- Preferred
CONVERT(varchar(10), r.dtAbertura, 112)  -- YYYYMMDD
CONVERT(char(8), r.dtAbertura, 108)      -- HH:MM:SS

-- Avoid in high-volume queries
FORMAT(r.dtAbertura, 'dd/MM/yyyy HH:mm')
```

## Key Optimization Learnings

1. **SQL Server CTEs are NOT materialized** - Unlike PostgreSQL/Oracle, SQL Server re-executes CTEs on every reference. With UNION ALL sections, each section recomputes all CTEs.

2. **Temp tables with clustered indexes** - For complex queries, materialize intermediate results into indexed temp tables.

3. **Unicode string literals required** - Use `N'Nao'` not `'Nao'` for Portuguese accented characters. Silent mismatch causes NULL results.

4. **Indexed views limitations** - SQL Server indexed views prohibit LEFT JOIN, MAX(), UNION ALL, CTEs, and view references.

5. **Materialized table pattern** - For frequently-accessed complex queries, pre-compute results into a permanent table with incremental refresh.

6. **Lock timeout protection** - Use `SET LOCK_TIMEOUT 30000` in refresh procedures to prevent indefinite blocking.

7. **SQL Agent QUOTED_IDENTIFIER** - Job steps require `SET QUOTED_IDENTIFIER ON` to match database settings.

8. **FORMAT() vs CONVERT()** - `FORMAT()` is 10-50x slower than `CONVERT()`. Always prefer `CONVERT()` for date/number formatting in T-SQL.

## Handling Large Query Results

To avoid "result exceeds maximum allowed tokens" errors:

### 1. Always use LIMIT parameter with `execute_query`:
```python
# For exploration/sampling, use limit=100 or less
execute_query(sql="SELECT * FROM large_table", limit=100)
```

### 2. Use TOP N for sampling:
```sql
SELECT TOP 100 * FROM (
    -- original query here
) AS results
```

### 3. Best practices for large datasets:
- Start with `SELECT COUNT(*)` to understand data volume
- Use aggregations (`GROUP BY`, `COUNT`, `SUM`) instead of returning raw rows
- Select only needed columns, not `SELECT *`
- Apply filters to reduce result set before returning
