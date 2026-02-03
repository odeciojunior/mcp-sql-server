# Claude Code Agents

This directory contains specialized Claude Code agents for the sql-playground project.

## Agent Overview

| Agent | Model | Color | Tools | Primary Use Case |
|-------|-------|-------|-------|------------------|
| `lesson-retriever` | Haiku | Green | All (inherited) | Fast knowledge base search and retrieval |
| `tsql-specialist` | Opus | Orange | All (inherited) | T-SQL query development and best practices |
| `sql-server-performance-tuner` | Opus | Blue | All (inherited) | Execution plan analysis and query optimization |
| `sql-performance-monitor` | Opus | Red | All (inherited) | Deep database health checks and bottleneck identification |
| `sql-schema-discovery` | Opus | Cyan | All (inherited) | Schema exploration, relationships, and documentation |
| `session-lessons-documenter` | Opus | Pink | All (inherited) | Capture session learnings for future reference |
| `code-reviewer` | Opus | Pink | Read, Grep, Glob | Code quality, security, and correctness review |
| `report-analyzer` | Opus | Green | Read, Grep, Glob, WebSearch, WebFetch, Write | Strategic report analysis and roadmap creation |
| `deep-research-analyst` | Opus | Purple | WebSearch, WebFetch, Read, Write, Grep, Glob | Multi-source internet research and synthesis |
| `set-baseline-metrics` | Haiku | Yellow | execute_query, Read, Write, Glob, Grep, Bash | Capture baseline performance metrics before optimizations |

## Agent Interaction Model

```
                              +---------------------+
                              |    User Request     |
                              +----------+----------+
                                         |
                                         v
                        +--------------------------------+
                        |         Main Claude            |
                        |   (routes to specialized       |
                        |         agents)                |
                        +----------------+---------------+
                                         |
    +--------+--------+--------+---------+---------+--------+--------+---------+
    |        |        |        |         |         |        |        |         |
    v        v        v        v         v         v        v        v         v
+------+ +------+ +------+ +------+ +------+ +------+ +------+ +------+ +--------+
|lesson| | tsql | | perf | | perf | |schema| | code | |report| | deep | |baseline|
|retrvr| |spec. | |tuner | |monit.| |disc. | |review| |analzr| |resrch| |metrics |
|(Haiku| |(Opus)| |(Opus)| |(Opus)| |(Opus)| |(Opus)| |(Opus)| |(Opus)| |(Haiku) |
+--+---+ +--+---+ +--+---+ +--+---+ +--+---+ +--+---+ +--+---+ +--+---+ +--+-----+
   |        |        |        |        |        |        |        |        |
   |        v        v        v        v        |        |        |        |
   |  +---------------------------------------------+   |        |        |
   +->|       session-lessons-documenter (Opus)      |<--+        |        |
      |       Capture new learnings                  |<-----------+        |
      +----------------------------------------------+                     |
                                                                           |
                              (standalone - no escalation)--------+--------+
```

## When to Use Each Agent

### lesson-retriever
**Use first** when you encounter:
- A problem that might have been solved before
- Need for best practices or patterns
- Gotchas or known issues to avoid
- Performance optimization guidance

**Triggers:** "slow query", "unexpected NULL", "syntax error", "SQL Agent fails"

### tsql-specialist
**Use for** active development:
- Writing new queries, views, or stored procedures
- Fixing T-SQL syntax errors
- Implementing window functions, CTEs, or complex joins
- Designing proper error handling and transactions

**Triggers:** "write a query", "create stored procedure", "fix this SQL"

### sql-server-performance-tuner
**Use for** query-level optimization:
- Execution plan review and interpretation
- Index strategy recommendations
- Identifying query bottlenecks (scans, lookups, implicit conversions)
- Query rewrite suggestions with impact estimates

**Triggers:** "query is slow", "analyze execution plan", "index recommendations", "optimize this query"

### sql-performance-monitor
**Use for** server-wide health and diagnostics:
- Comprehensive database health checks
- Wait statistics analysis and bottleneck identification
- CPU, memory, and I/O pressure diagnosis
- TempDB contention analysis
- Query Store health and regression detection
- Blocking and deadlock investigation

**Triggers:** "database health check", "why is the server slow", "wait statistics", "CPU high", "memory pressure", "blocking issues", "TempDB contention"

### sql-schema-discovery
**Use for** schema exploration and documentation:
- Discover table structures, columns, and constraints
- Map foreign key relationships and dependencies
- Find what objects depend on a table/view
- Identify special table types (graph, temporal, memory-optimized)
- Extract schema documentation from extended properties
- Analyze index coverage and usage patterns
- Generate schema documentation reports

**Triggers:** "what tables exist", "show me the schema", "what references this table", "find dependencies", "document the database", "relationship diagram", "table structure"

### session-lessons-documenter
**Use at session end** or after significant discoveries:
- Document bugs that took time to diagnose
- Capture performance optimization discoveries
- Record non-obvious configuration requirements
- Preserve patterns that solved recurring problems

**Triggers:** "wrap up session", "document this", end of complex troubleshooting

### code-reviewer
**Use proactively** after code changes:
- Review new feature implementations for correctness
- Validate refactors against project conventions
- Check bug fixes for regressions
- Audit security-critical code changes

**Triggers:** After writing/modifying code, "review this code", "check for regressions", "validate changes"

### report-analyzer
**Use for** strategic analysis:
- Analyze reports and extract actionable insights
- Create roadmaps for goal achievement
- Synthesize data into strategic recommendations
- Identify patterns and priorities from report data

**Triggers:** "analyze this report", "create a roadmap", "what does this data tell us"

**Note:** Read-only agent with restricted tools (no Edit, Bash, or NotebookEdit).

### deep-research-analyst
**Use for** comprehensive internet research:
- Multi-source investigation across authoritative sources
- Academic research, market analysis, technical deep-dives
- Competitive intelligence and historical investigations
- Synthesis from diverse, credible sources

**Triggers:** "research this topic", "deep dive on", "investigate", "find best practices for"

**Note:** Read-only agent with restricted tools (no Edit, Bash, or NotebookEdit).

### set-baseline-metrics
**Use before** applying database optimizations:
- Capture procedure execution stats from DMVs
- Record index usage patterns for target tables
- Document table sizes and plan cache entries
- Create structured JSON + SQL file pairs following project conventions
- Generate summary markdown with key metrics highlighted

**Triggers:** "capture baseline", "set baseline metrics", "before we optimize", "measure current performance", "baseline for optimization"

## Escalation Paths

| From Agent | When to Escalate | To Agent |
|------------|------------------|----------|
| lesson-retriever | Need to write/modify code | tsql-specialist |
| lesson-retriever | Need execution plan analysis | sql-server-performance-tuner |
| lesson-retriever | Need server-wide diagnostics | sql-performance-monitor |
| lesson-retriever | Need schema exploration | sql-schema-discovery |
| tsql-specialist | Deep query optimization | sql-server-performance-tuner |
| tsql-specialist | Server-level bottlenecks | sql-performance-monitor |
| tsql-specialist | Understand table relationships | sql-schema-discovery |
| tsql-specialist | Check for known gotchas | lesson-retriever |
| sql-server-performance-tuner | Need to implement query rewrite | tsql-specialist |
| sql-server-performance-tuner | Server-wide wait analysis | sql-performance-monitor |
| sql-server-performance-tuner | Check table/index structure | sql-schema-discovery |
| sql-server-performance-tuner | Check for documented patterns | lesson-retriever |
| sql-performance-monitor | Need specific query optimization | sql-server-performance-tuner |
| sql-performance-monitor | Need to write monitoring procedure | tsql-specialist |
| sql-performance-monitor | Understand schema for context | sql-schema-discovery |
| sql-performance-monitor | Check for known patterns | lesson-retriever |
| sql-schema-discovery | Need to write schema changes | tsql-specialist |
| sql-schema-discovery | Analyze query performance on schema | sql-server-performance-tuner |
| sql-schema-discovery | Check for schema gotchas | lesson-retriever |
| code-reviewer | Need to implement fixes | tsql-specialist |
| code-reviewer | Performance concerns in code | sql-server-performance-tuner |
| set-baseline-metrics | Interpret baseline results | sql-performance-monitor |
| set-baseline-metrics | Optimize based on findings | sql-server-performance-tuner |
| set-baseline-metrics | Write optimization SQL | tsql-specialist |
| set-baseline-metrics | Understand table relationships | sql-schema-discovery |
| All agents | Worth documenting for future | session-lessons-documenter |

## Standardized Severity Scale

All agents use a consistent 4-level severity scale:

| Level | Description | Action Required |
|-------|-------------|-----------------|
| **CRITICAL** | Causes failures or major performance issues (>90% degradation) | Immediate action |
| **HIGH** | Significant impact (>50% degradation) | Address before production |
| **MEDIUM** | Moderate impact (10-50% degradation) | Plan to optimize |
| **LOW** | Minor impact (<10% degradation) | Consider for future |

## Knowledge Base Reference

**Location:** `docs/lessons/`

**Contents:** 20 lessons covering:
- Gotchas (reserved keywords, temp table compilation, Unicode strings)
- Patterns (temp tables, materialized views, incremental refresh)
- Optimizations (CTE pitfalls, FORMAT vs CONVERT, EXISTS vs IN)
- Bug-fixes (SQL Agent QUOTED_IDENTIFIER, @@ROWCOUNT timing)

**Index:** `docs/lessons/INDEX.md` - Start here for topic-based navigation

## Agent File Format

Each agent file is a Markdown file with YAML frontmatter.

### Frontmatter Reference

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `name` | Yes | string | Unique identifier (lowercase, hyphens only) |
| `description` | Yes | string | When Claude should delegate to this agent. Be specific with examples and triggers. |
| `model` | No | `haiku` \| `sonnet` \| `opus` | Model to use. Defaults to inheriting the parent model. |
| `color` | No | string | Status line color for the agent |
| `tools` | No | comma-separated list | Tools the agent can use. If omitted, inherits all tools from parent. |
| `disallowedTools` | No | comma-separated list | Tools to explicitly deny (removed from inherited set) |
| `permissionMode` | No | string | Controls permission prompts (see below) |

### Permission Modes

| Mode | Behavior | Use Case |
|------|----------|----------|
| `default` | Standard permission checking with prompts | Interactive agents needing user approval |
| `acceptEdits` | Auto-accept file edits | Trusted automation agents |
| `dontAsk` | Auto-deny prompts (allowed tools still work) | Strict sandboxing |
| `bypassPermissions` | Skip all permission checks | Automation workflows (use with caution) |
| `plan` | Read-only exploration mode | Research and analysis agents |

### File Structure

```yaml
---
name: agent-name
description: "Use this agent when... (with examples and trigger phrases)"
model: haiku | sonnet | opus
color: green | orange | blue | red | cyan | pink | purple
tools: Read, Grep, Glob, WebSearch        # optional - restrict tool access
disallowedTools: Edit, Bash               # optional - deny specific tools
permissionMode: default                   # optional - control permissions
---

# Agent Title

[System prompt content with:]
- Role definition and expertise areas
- Step-by-step methodology/workflow
- Project-specific context and conventions
- Output format specification
- Quality standards and constraints
- When to Escalate section
```

### Model Selection Guide

| Model | Best For | Trade-off |
|-------|----------|-----------|
| `haiku` | Fast search, triage, read-only analysis | Speed over depth |
| `sonnet` | Balanced tasks, code review, general workflows | Good default |
| `opus` | Complex reasoning, architecture, critical analysis | Depth over speed |

## Agent Design Best Practices

### 1. Write Detailed Descriptions

Claude uses the `description` field to decide when to delegate. Be specific:

```yaml
# Bad - too vague
description: "Code reviewer"

# Good - specific triggers and examples
description: "Expert code review specialist. Analyzes code for quality,
security, and correctness. Use after writing or modifying code, for
refactors, bug fixes, or new feature implementations."
```

### 2. Single-Purpose Agents

Each agent should excel at one specific domain. Avoid "do everything" agents.

### 3. Restrict Tool Access

Only grant tools the agent actually needs. Read-only agents should not have Edit/Write/Bash:

```yaml
# Research agent - restricted to read-only + web
tools: Read, Grep, Glob, WebSearch, WebFetch
disallowedTools: Edit, NotebookEdit, Bash
```

### 4. Structure Prompts with Clear Workflows

Include numbered steps so the agent follows a consistent methodology:

```markdown
When invoked:
1. **Research** - Analyze current state
2. **Plan** - Design the approach
3. **Execute** - Implement the solution
4. **Verify** - Validate the result
```

### 5. Include Guardrails

Define what the agent must and must not do:

```markdown
Constraints:
- Never modify production tables directly
- Always use WITH(NOLOCK) for read queries
- Limit result sets to 1000 rows for exploration
```

### 6. Use Consistent Output Formats

Define severity scales, report structures, or output templates so agents produce predictable results.

### 7. Add Escalation Paths

Every agent should know when to recommend delegating to another specialist:

```markdown
## When to Escalate
- Performance issues -> sql-server-performance-tuner
- Schema questions -> sql-schema-discovery
- Known gotchas -> lesson-retriever
```

## Adding New Agents

1. Create `agent-name.md` in this directory
2. Include all required frontmatter fields (`name`, `description`)
3. Choose the right model for the task complexity
4. Restrict tools to only what the agent needs
5. Include "When to Escalate" section with cross-references
6. Use standardized severity scale (CRITICAL/HIGH/MEDIUM/LOW)
7. Add entry to this README (overview table + when-to-use section)
8. Update CLAUDE.md Custom Agents section
