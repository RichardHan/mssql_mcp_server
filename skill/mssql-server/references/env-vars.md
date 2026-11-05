# Environment variables (v0.1.1)

| Variable | Default | Meaning |
|---|---|---|
| `MSSQL_SERVER` | `localhost` | Host, `host\instance`, `(localdb)\MSSQLLocalDB`, or `name.database.windows.net` |
| `MSSQL_DATABASE` | — | **Required** always |
| `MSSQL_USER` / `MSSQL_PASSWORD` | — | Required unless `MSSQL_WINDOWS_AUTH=true` |
| `MSSQL_PORT` | (driver default 1433) | Custom port |
| `MSSQL_WINDOWS_AUTH` | `false` | `true` = Windows/integrated auth, user/password ignored |
| `MSSQL_ENCRYPT` | `false` on-prem, `true` for Azure | `true` = require TLS |
| `MSSQL_TRUST_SERVER_CERTIFICATE` | unset; when encrypting: `true` on-prem, `false` Azure | Trust self-signed certs |
| `MSSQL_QUERY_TIMEOUT` | `300` | Query timeout, seconds |
| `MSSQL_ALLOW_WRITES` | `false` | `true`/`1`/`yes` enables non-SELECT statements |
| `MSSQL_COMMAND` | `query` (read-only) / `execute_sql` (writes) | Override tool name |

Azure SQL hosts (`*.database.windows.net`) automatically use TDS 7.4 and required encryption.

Note: read-only default, `MSSQL_ALLOW_WRITES`, `MSSQL_QUERY_TIMEOUT`, and `MSSQL_TRUST_SERVER_CERTIFICATE` land in 0.1.1. On 0.1.0 all statements are allowed, so be extra careful.
