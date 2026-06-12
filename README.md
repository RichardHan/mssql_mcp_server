# Microsoft SQL Server MCP Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Fork of [RichardHan/mssql_mcp_server](https://github.com/RichardHan/mssql_mcp_server) with pymssql 2.3 compatibility, Cursor **Ask mode** support via `readOnlyHint`, and optional write mode.

A Model Context Protocol (MCP) server for secure SQL Server database access through AI assistants (Cursor, Claude Desktop, etc.).

## Features

- List database tables as MCP resources
- **`query`** tool for read-only SQL (Cursor Ask mode); **`execute_sql`** when writes are enabled
- Multiple authentication methods (SQL, Windows, Azure AD)
- LocalDB and Azure SQL support
- Custom port configuration

## Environment setup

Install with [uv](https://docs.astral.sh/uv/) from GitHub (recommended over PyPI — the published package lacks these fixes).

**Python interpreter (example):**

- `C:\Python\Python313\python.exe` (Windows)
- `/usr/bin/python3.13` (Linux/macOS)

**Git Bash / Linux / macOS:**

```bash
mkdir -p ~/tools/mcp && cd ~/tools/mcp
uv venv --python python3.13
source .venv/bin/activate   # Windows Git Bash: source .venv/Scripts/activate
uv pip install "git+https://github.com/binitt/mssql_mcp_server.git"
python -m mssql_mcp_server --help
```

**Windows CMD:**

```cmd
mkdir C:\tools\mcp
cd C:\tools\mcp
uv venv --python C:\Python\Python313\python.exe
.venv\Scripts\activate
uv pip install "git+https://github.com/binitt/mssql_mcp_server.git"
.venv\Scripts\python.exe -m mssql_mcp_server --help
```

**Upgrade after changes on GitHub:**

```bash
uv pip install --upgrade "git+https://github.com/binitt/mssql_mcp_server.git"
```

## Read-only vs write mode

By default the MCP server exposes a **`query`** tool (read-only): only `SELECT` / `WITH` (CTE) queries are accepted, and the tool is annotated with `readOnlyHint` so Cursor **Ask mode** can use it. With `MSSQL_ALLOW_WRITES=1`, the tool is named **`execute_sql`** and accepts all SQL.

| Variable | Default | Tool name | Effect |
|----------|---------|-----------|--------|
| `MSSQL_ALLOW_WRITES` | unset / `false` | `query` | SELECT-only; `readOnlyHint: true` (Ask mode) |
| `MSSQL_ALLOW_WRITES=1` | — | `execute_sql` | All SQL allowed; no read-only hint (Agent mode) |

Use a read-only SQL login for the default entry and a write-capable login when enabling writes. Restart the MCP server in Cursor after changing env vars.

**Cursor `mcp.json` — two entries (recommended):**

```json
{
  "mcpServers": {
    "mssql_readonly": {
      "command": "/path/to/.venv/Scripts/python.exe",
      "args": ["-m", "mssql_mcp_server"],
      "env": {
        "MSSQL_SERVER": "your-sql-server-host",
        "MSSQL_DATABASE": "your_database",
        "MSSQL_USER": "your_readonly_user",
        "MSSQL_PASSWORD": "your-password"
      },
      "alwaysAllow": ["query"]
    },
    "mssql_write": {
      "command": "/path/to/.venv/Scripts/python.exe",
      "args": ["-m", "mssql_mcp_server"],
      "env": {
        "MSSQL_SERVER": "your-sql-server-host",
        "MSSQL_DATABASE": "your_database",
        "MSSQL_USER": "your_write_user",
        "MSSQL_PASSWORD": "your-password",
        "MSSQL_ALLOW_WRITES": "1"
      },
      "alwaysAllow": ["execute_sql"]
    }
  }
}
```

- **`mssql_readonly`** — Ask mode and Agent read queries; tool name **`query`** (`your_readonly_user`).
- **`mssql_write`** — Agent write queries only; tool name **`execute_sql`**; disable when not needed.

On Windows, use forward slashes in `command` (e.g. `C:/tools/mcp/.venv/Scripts/python.exe`).

## Creating a read-only SQL user for MCP

Use a dedicated login with `db_datareader` only on the target database. Run in SQL Server Management Studio or `sqlcmd` as a user who can create logins and users.

```sql
CREATE LOGIN your_readonly_user WITH PASSWORD = 'your-strong-password';
GO

USE your_database;
GO

CREATE USER your_readonly_user FOR LOGIN your_readonly_user;
GO

ALTER ROLE db_datareader ADD MEMBER your_readonly_user;
GO
```

Use strong, unique passwords and never commit real credentials to version control.

## Configuration reference

### Basic SQL authentication

```bash
MSSQL_SERVER=your-sql-server-host   # Required
MSSQL_DATABASE=your_database        # Required
MSSQL_USER=your_username            # Required for SQL auth
MSSQL_PASSWORD=your-password        # Required for SQL auth
```

### Windows authentication

```bash
MSSQL_SERVER=your-sql-server-host
MSSQL_DATABASE=your_database
MSSQL_WINDOWS_AUTH=true
```

### Azure SQL Database

```bash
MSSQL_SERVER=your-server.database.windows.net
MSSQL_DATABASE=your_database
MSSQL_USER=your_username
MSSQL_PASSWORD=your-password
# Encryption is enabled automatically for Azure hosts
```

### Optional settings

```bash
MSSQL_PORT=1433                 # Custom port (default: 1433)
MSSQL_ENCRYPT=true              # Force encryption (non-Azure)
MSSQL_ALLOW_WRITES=1            # Allow INSERT/UPDATE/DELETE; exposes execute_sql tool
MSSQL_COMMAND=query             # Override default tool name (optional)
```

## Development

```bash
git clone https://github.com/binitt/mssql_mcp_server.git
cd mssql_mcp_server
pip install -e ".[dev]"
pytest tests/test_read_only_mode.py tests/test_config.py -v
```

## Security

- Create a dedicated SQL user with minimal permissions
- Never use `sa` or other administrative accounts
- Prefer read-only logins for the default MCP entry
- Use Windows Authentication when possible
- Enable encryption for sensitive data (`MSSQL_ENCRYPT=true`)

See [SECURITY.md](SECURITY.md) for more detail.

## License

MIT
