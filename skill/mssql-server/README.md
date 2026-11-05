# mssql-server agent skill

An Agent Skill (SKILL.md format) that teaches AI agents (Claude Code, Cursor, Claude Desktop, VS Code Copilot, Codex, etc.) when and how to use the open-source [microsoft_sql_server_mcp](https://github.com/RichardHan/mssql_mcp_server) MCP server to safely explore and query SQL Server, Azure SQL, and LocalDB.

## Contents
- `SKILL.md` – when to use, safe query workflow, credential rules
- `references/setup.md` – config snippets for each client, auth modes
- `references/env-vars.md` – every supported environment variable
- `references/troubleshooting.md` – common errors and fixes

## Install
Copy this folder into your agent's skills directory (e.g. `~/.claude/skills/mssql-server/`), then configure the MCP server per `references/setup.md`.

Contains no credentials. License: MIT.
