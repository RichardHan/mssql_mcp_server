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
- 🌐 **Multi-host support** — connect to multiple SQL Servers from a single MCP instance

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

### Multi-Host Configuration

Connect to **multiple SQL Servers** from a single MCP server instance. Each
connection has its own name, credentials, and settings.

When multi-host mode is active the `execute_sql` tool gains a required
`connection` parameter, and a new `list_connections` tool becomes available.

#### Option 1 — Indexed environment variables (recommended)

Define hosts with `MSSQL_HOST_<N>_*` env vars. Each index is a separate
connection:

```json
{
  "mcpServers": {
    "mssql": {
      "command": "uvx",
      "args": ["microsoft_sql_server_mcp"],
      "env": {
        "MSSQL_HOST_1_NAME": "prod",
        "MSSQL_HOST_1_SERVER": "prod.example.com",
        "MSSQL_HOST_1_DATABASE": "proddb",
        "MSSQL_HOST_1_USER": "admin",
        "MSSQL_HOST_1_PASSWORD": "secret",

        "MSSQL_HOST_2_NAME": "dev",
        "MSSQL_HOST_2_SERVER": "dev.example.com",
        "MSSQL_HOST_2_DATABASE": "devdb",
        "MSSQL_HOST_2_USER": "dev",
        "MSSQL_HOST_2_PASSWORD": "devpass",
        "MSSQL_HOST_2_PORT": "1434"
      }
    }
  }
}
```

Indexes don't have to be contiguous — `1, 2, 5, 10` works fine.

#### Option 2 — JSON config file

Point `MSSQL_CONNECTIONS_FILE` to a JSON file on disk:

```json
{
  "mcpServers": {
    "mssql": {
      "command": "uvx",
      "args": ["microsoft_sql_server_mcp"],
      "env": {
        "MSSQL_CONNECTIONS_FILE": "/path/to/connections.json"
      }
    }
  }
}
```

Where `connections.json` looks like:

```json
[
  {
    "name": "prod",
    "server": "prod.example.com",
    "database": "proddb",
    "user": "admin",
    "password": "secret"
  },
  {
    "name": "dev",
    "server": "dev.example.com",
    "database": "devdb",
    "user": "dev",
    "password": "devpass",
    "port": "1434"
  },
  {
    "name": "local",
    "server": "localhost",
    "database": "localdb",
    "windows_auth": "true"
  }
]
```

#### Connection fields

Each host supports these fields (as env var suffixes or JSON keys):

| Field / Suffix   | Required | Default     | Description                              |
|------------------|----------|-------------|------------------------------------------|
| `NAME`           | Yes      | —           | Unique connection identifier             |
| `SERVER`         | No       | `localhost` | SQL Server hostname                      |
| `DATABASE`       | Yes      | —           | Database name                            |
| `USER`           | Cond.    | —           | Username (required for SQL auth)         |
| `PASSWORD`       | Cond.    | —           | Password (required for SQL auth)         |
| `PORT`           | No       | `1433`      | TCP port                                 |
| `ENCRYPT`        | No       | `false`     | Enable encryption (`true`/`false`)       |
| `WINDOWS_AUTH`   | No       | `false`     | Use Windows authentication               |

#### Resolution order

1. `MSSQL_HOST_<N>_*` env vars (highest priority)
2. `MSSQL_CONNECTIONS_FILE` JSON file
3. Legacy single-host env vars (`MSSQL_SERVER`, `MSSQL_USER`, etc.)

#### Backward compatibility

If no `MSSQL_HOST_*` env vars or `MSSQL_CONNECTIONS_FILE` are set, the server
falls back to the original single-host environment variables — **no changes
needed for existing setups**.

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

## Security

- Create a dedicated SQL user with minimal permissions
- Never use admin/sa accounts
- Use Windows Authentication when possible
- Enable encryption for sensitive data

## License

MIT