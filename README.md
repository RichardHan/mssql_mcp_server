# Microsoft SQL Server MCP Server

[![PyPI](https://img.shields.io/pypi/v/microsoft_sql_server_mcp)](https://pypi.org/project/microsoft_sql_server_mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

<a href="https://glama.ai/mcp/servers/29cpe19k30">
  <img width="380" height="200" src="https://glama.ai/mcp/servers/29cpe19k30/badge" alt="Microsoft SQL Server MCP server" />
</a>

A Model Context Protocol (MCP) server for secure SQL Server database access through Claude Desktop.

## Features

- 🔍 List database tables
- 📊 Execute SQL queries (read-only by default; optional write mode)
- 🔐 Multiple authentication methods (SQL, Windows, Azure AD)
- 🏢 LocalDB and Azure SQL support
- 🔌 Custom port configuration

## Quick Start

### Install with Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mssql": {
      "command": "uvx",
      "args": ["--from", "microsoft-sql-server-mcp", "mssql_mcp_server"],
      "env": {
        "MSSQL_SERVER": "localhost",
        "MSSQL_DATABASE": "your_database",
        "MSSQL_USER": "your_username",
        "MSSQL_PASSWORD": "your_password"
      }
    }
  }
}
```

## Configuration

### Basic SQL Authentication
```bash
MSSQL_SERVER=localhost          # Required
MSSQL_DATABASE=your_database    # Required
MSSQL_USER=your_username        # Required for SQL auth
MSSQL_PASSWORD=your_password    # Required for SQL auth
```

### Windows Authentication
```bash
MSSQL_SERVER=localhost
MSSQL_DATABASE=your_database
MSSQL_WINDOWS_AUTH=true         # Use Windows credentials
```

### Azure SQL Database
```bash
MSSQL_SERVER=your-server.database.windows.net
MSSQL_DATABASE=your_database
MSSQL_USER=your_username
MSSQL_PASSWORD=your_password
# Encryption is automatic for Azure
```

### Optional Settings
```bash
MSSQL_PORT=1433                 # Custom port (default: 1433)
MSSQL_ENCRYPT=true              # Force encryption (uses pymssql `encryption=require`)
MSSQL_TRUST_SERVER_CERTIFICATE=true  # Trust SQL Server TLS cert (default: true for non-Azure encrypt, false for Azure)
MSSQL_QUERY_TIMEOUT=300         # Query timeout in seconds (default: 300). Queries exceeding this limit are automatically terminated.
MSSQL_ALLOW_WRITES=1            # Allow INSERT/UPDATE/DELETE; tool name becomes execute_sql (default: read-only `query` tool)
MSSQL_COMMAND=query             # Optional override for the MCP tool name
```

### Read-only vs write mode

By default the server exposes a **`query`** tool that only accepts a **single** read-only `SELECT` / `WITH` / set-operation statement (parsed as T-SQL) and sets `readOnlyHint` (so Cursor Ask mode can use it). Stacked batches (`SELECT …; INSERT …`), `SELECT INTO`, `EXEC`, and other non-SELECT statements are rejected **before** they reach the database driver.

Set `MSSQL_ALLOW_WRITES=1` to expose **`execute_sql`** and allow writes. Prefer a **least-privilege read-only SQL login** for the default mode — the parser is fail-closed defense in depth, not a substitute for database permissions, and does not claim to prove arbitrary T-SQL is free of side effects.


## Alternative Installation Methods

### Using pip
```bash
pip install microsoft_sql_server_mcp
```

Then in `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "mssql": {
      "command": "python",
      "args": ["-m", "mssql_mcp_server"],
      "env": { ... }
    }
  }
}
```

### Development
```bash
git clone https://github.com/RichardHan/mssql_mcp_server.git
cd mssql_mcp_server
pip install -e .
```

## Dependency notes

The `mcp` SDK is pinned to `>=1.0.0,<2.0.0` in both `pyproject.toml` and `requirements.txt`.

`mcp` 2.0.0 removed the legacy `Server` decorator API (`@app.list_resources()`,
`@app.list_tools()`, ...) that `src/mssql_mcp_server/server.py` is built on, so an
unconstrained install crashes at import time with:

```
AttributeError: 'Server' object has no attribute 'list_resources'
```

This matters most for `uvx --from git+...` launches, which resolve dependencies fresh on
every cold start and ignore `uv.lock`. Do not drop the upper bound until the server is
migrated to the 2.x API.

## Security

- Create a dedicated SQL user with minimal permissions (e.g. `db_datareader` for read-only deployments)
- Never use admin/sa accounts for the MCP connection
- Use Windows Authentication when possible
- Enable encryption for sensitive data
- Treat `MSSQL_ALLOW_WRITES` as an explicit opt-in; keep it off unless the user deliberately wants writes
- Read-only mode parses T-SQL and fail-closes on unsupported/stacked statements — still pair it with least-privilege DB grants

## License

MIT
