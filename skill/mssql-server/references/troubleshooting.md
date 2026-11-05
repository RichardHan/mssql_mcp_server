# Troubleshooting

- **`unexpected keyword argument 'encrypt'` / encryption errors**: fixed in 0.1.1 (server passes pymssql `encryption`). Upgrade: `uvx microsoft_sql_server_mcp@latest` or `uv cache clean`.
- **Certificate / SSL handshake fails on-prem**: set `MSSQL_ENCRYPT=true` and `MSSQL_TRUST_SERVER_CERTIFICATE=true` (self-signed), or `MSSQL_ENCRYPT=false` on trusted networks.
- **Azure SQL connection refused**: add client IP to the Azure SQL firewall; use full `*.database.windows.net` host; use SQL auth (Entra ID not supported).
- **Timeouts**: raise `MSSQL_QUERY_TIMEOUT` (seconds, default 300) — but first add `TOP`, WHERE filters, or indexes.
- **"Missing required database configuration"**: `MSSQL_DATABASE` is always required; `MSSQL_USER`/`MSSQL_PASSWORD` unless `MSSQL_WINDOWS_AUTH=true`.
- **Cannot connect at all**: check TCP/IP is enabled in SQL Server Configuration Manager, port 1433 (or `MSSQL_PORT`) open, named instance needs SQL Browser running; test with `sqlcmd -S host -U user`.
- **Write rejected**: read-only mode is the default; only set `MSSQL_ALLOW_WRITES=true` if the user explicitly wants writes.
- **`uvx` not found**: install uv and restart the client so PATH is picked up.
