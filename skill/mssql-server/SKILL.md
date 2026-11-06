---
name: mssql-server
description: Explore and query Microsoft SQL Server, Azure SQL Database, or LocalDB through the microsoft_sql_server_mcp MCP server. Use when the user asks about data, tables, or schema in a SQL Server / Azure SQL / LocalDB database, wants to run T-SQL, or needs to set up or troubleshoot that MCP server.
---

# Microsoft SQL Server via MCP

Pairs with the open-source MCP server `microsoft_sql_server_mcp` (github.com/RichardHan/mssql_mcp_server, MIT). Launch: `uvx microsoft_sql_server_mcp`.

## When to use
- User asks a question answered by data in SQL Server, Azure SQL, or LocalDB.
- Schema exploration: "what tables are there", "what columns does X have".
- Writing/running T-SQL, or setting up/troubleshooting this MCP server.

## What the server exposes
- **Resources**: one per table, `mssql://<table>/data` (reads first 100 rows as CSV).
- **Tool**: `query` in read-only mode (default; a single parsed SELECT/WITH/set-operation only — stacked batches and SELECT INTO are rejected). When `MSSQL_ALLOW_WRITES=true` the tool is `execute_sql` and accepts any statement. `MSSQL_COMMAND` overrides the tool name.

If the server is not configured, follow `references/setup.md`. Env vars are in `references/env-vars.md`.

## Safe workflow
1. **List tables** (list resources, or `SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES`).
2. **Inspect schema** before querying: `SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 'X'`.
3. **Read with limits**: always `SELECT TOP (100) ...` (or `OFFSET/FETCH`), name columns instead of `*` on wide tables, filter on indexed columns, avoid unbounded joins on large tables.
4. **Writes**: never run INSERT/UPDATE/DELETE/DDL without showing the exact statement and getting explicit user confirmation. Prefer a `SELECT COUNT(*)` with the same WHERE first to show impact. Wrap in `BEGIN TRAN ... ` when possible. Leave read-only mode on unless the user deliberately enables writes.
5. **Parameter safety**: the tool takes raw SQL. Never paste untrusted user text directly into SQL; validate identifiers against INFORMATION_SCHEMA, quote with `QUOTENAME`, escape string literals (`'` -> `''`). Read-only mode rejects stacked statements / SELECT INTO before execute, but still use a least-privilege login — the gate is fail-closed defense in depth, not a full SQL security prover.

## Credentials
- Use a dedicated least-privilege login (e.g. `db_datareader`), **never `sa`**.
- Keep passwords in env/config, never echo them back or put them in queries or files you commit.

## Troubleshooting
See `references/troubleshooting.md` (encryption errors, certificate trust, timeouts, connection failures, LocalDB).
