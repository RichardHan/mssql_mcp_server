# Setup

Requires `uv` (https://docs.astral.sh/uv/). Replace placeholder values; never commit real passwords.

## Claude Code
```bash
claude mcp add mssql \
  -e MSSQL_SERVER=localhost -e MSSQL_DATABASE=mydb \
  -e MSSQL_USER=readonly_user -e MSSQL_PASSWORD=change-me \
  -- uvx microsoft_sql_server_mcp
```

## Claude Desktop (`claude_desktop_config.json`) / Cursor (`.cursor/mcp.json` or `~/.cursor/mcp.json`)
```json
{
  "mcpServers": {
    "mssql": {
      "command": "uvx",
      "args": ["microsoft_sql_server_mcp"],
      "env": {
        "MSSQL_SERVER": "localhost",
        "MSSQL_DATABASE": "mydb",
        "MSSQL_USER": "readonly_user",
        "MSSQL_PASSWORD": "change-me"
      }
    }
  }
}
```

## VS Code Copilot (`.vscode/mcp.json`)
```json
{
  "inputs": [
    { "type": "promptString", "id": "mssql-pw", "description": "SQL password", "password": true }
  ],
  "servers": {
    "mssql": {
      "type": "stdio",
      "command": "uvx",
      "args": ["microsoft_sql_server_mcp"],
      "env": {
        "MSSQL_SERVER": "localhost",
        "MSSQL_DATABASE": "mydb",
        "MSSQL_USER": "readonly_user",
        "MSSQL_PASSWORD": "${input:mssql-pw}"
      }
    }
  }
}
```

## Auth modes
- **SQL auth**: `MSSQL_USER` + `MSSQL_PASSWORD` + `MSSQL_DATABASE`.
- **Windows auth**: `MSSQL_WINDOWS_AUTH=true` + `MSSQL_DATABASE` (no user/password). Run the client as the Windows account with DB access.
- **Azure SQL**: `MSSQL_SERVER=yourserver.database.windows.net`, SQL auth user/password (a contained DB user or server login). Encryption is on automatically. Azure AD / Entra ID token or interactive auth is **not** supported by this server (pymssql/FreeTDS); create a SQL-auth user for the agent instead.
- **LocalDB**: `MSSQL_SERVER=(localdb)\\MSSQLLocalDB` (escape backslash in JSON), Windows auth typical.

## Least-privilege login (example)
```sql
CREATE LOGIN mcp_reader WITH PASSWORD = '<strong password>';
USE mydb; CREATE USER mcp_reader FOR LOGIN mcp_reader;
ALTER ROLE db_datareader ADD MEMBER mcp_reader;
```
