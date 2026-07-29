# Microsoft SQL Server MCP Server

[![PyPI](https://img.shields.io/pypi/v/microsoft_sql_server_mcp)](https://pypi.org/project/microsoft_sql_server_mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

<a href="https://glama.ai/mcp/servers/29cpe19k30">
  <img width="380" height="200" src="https://glama.ai/mcp/servers/29cpe19k30/badge" alt="Microsoft SQL Server MCP server" />
</a>

A Model Context Protocol (MCP) server for secure SQL Server database access through Claude Desktop.

## Features

- 🔍 List database tables
- 📊 Execute SQL queries (SELECT, INSERT, UPDATE, DELETE)
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
      "args": ["microsoft_sql_server_mcp"],
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
MSSQL_ENCRYPT=true              # Force encryption
```

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

- Create a dedicated SQL user with minimal permissions
- Never use admin/sa accounts
- Use Windows Authentication when possible
- Enable encryption for sensitive data

## License

MIT