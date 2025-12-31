# Microsoft SQL Server MCP Server

[![PyPI](https://img.shields.io/pypi/v/microsoft_sql_server_mcp)](https://pypi.org/project/microsoft_sql_server_mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

<a href="https://glama.ai/mcp/servers/29cpe19k30">
  <img width="380" height="200" src="https://glama.ai/mcp/servers/29cpe19k30/badge" alt="Microsoft SQL Server MCP server" />
</a>

A Model Context Protocol (MCP) server for secure SQL Server database access through Claude Desktop.

## Features

- 🔍 List database tables and detailed schema information
- 📊 Execute SQL queries (SELECT, INSERT, UPDATE, DELETE)
- 🔐 Multiple authentication methods (SQL, Windows, Azure AD)
- 🏢 LocalDB and Azure SQL support
- 🔌 Custom port configuration
- ⚡ **Connection pooling for 50-70% performance improvement**
- 🛡️ **Rate limiting for DoS protection**
- 🔒 **Enhanced security with input validation**
- 📝 **Security audit logging for compliance**
- ⚙️ **YAML configuration file support**
- 📊 **Connection pool and rate limit monitoring**

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

### Advanced Configuration (YAML File)

For more complex configurations, create a `mssql_config.yml` file:

```yaml
# Database Connection
server: localhost
database: your_database
user: your_username
password: your_password

# Connection Pool (improves performance 50-70%)
connection_pool:
  min_size: 2
  max_size: 10
  timeout: 30

# Query Cache (optional)
cache:
  enabled: false
  ttl: 300
  max_size: 1000
```

Environment variables take precedence over the YAML file.

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

### Best Practices
- Create a dedicated SQL user with minimal permissions
- Never use admin/sa accounts in production
- Use Windows Authentication when possible
- Enable encryption for sensitive data: `MSSQL_ENCRYPT=true`

### Built-in Security Features

This server includes multiple layers of security:

1. **Input Validation** - All inputs validated with Pydantic
2. **SQL Injection Protection** - Table names validated with strict regex, dangerous patterns detected
3. **Rate Limiting** - Configurable request limits prevent DoS attacks
4. **Error Sanitization** - Passwords and sensitive data never exposed in errors
5. **Security Auditing** - All operations logged for compliance

### Example: Minimal Permissions

```sql
-- Create a restricted user
CREATE LOGIN mcp_user WITH PASSWORD = 'StrongPassword123!';
CREATE USER mcp_user FOR LOGIN mcp_user;

-- Grant only necessary permissions
GRANT SELECT ON Schema.TableName TO mcp_user;
GRANT INSERT, UPDATE ON Schema.AuditLog TO mcp_user;
```

## Performance

With connection pooling enabled (default):
- **50-70% faster** query response times
- **Reduced** database connection overhead
- **Better** resource utilization
- **Automatic** connection health checks and recovery

## Monitoring

Check connection pool stats:
```
Tool: get_pool_stats
```

``

## What's New in v0.2.0 🎉

### Major Features Added:

- ⚡ **Connection Pooling** - 50-70% performance improvement
- 💾 **Query Caching** - Near-instant results for repeated queries  
- 🔄 **Transactions** - ACID compliant multi-query transactions
- 🔧 **Stored Procedures** - Full SP execution with parameters
- 📄 **Advanced Pagination** - OFFSET/FETCH with configurable page size
- 🛡️ **Enhanced Security** - Pydantic validation, dangerous pattern detection
- 📝 **Security Auditing** - Comprehensive JSON-formatted audit logs
- 🎨 **Multiple Formats** - CSV, Table, and JSON output formats

See [CHANGELOG.md](CHANGELOG.md) for complete details.

## Quick Examples

### Execute Query with Caching
```python
# First execution: ~150ms
# Second execution: ~2ms (from cache!)
{
  "tool": "execute_sql",
  "arguments": {
    "query": "SELECT * FROM users WHERE active = 1",
    "use_cache": true,
    "format": "table"
  }
}
```

### Execute Transaction
```python
{
  "tool": "execute_transaction",
  "arguments": {
    "queries": [
      "UPDATE inventory SET quantity = quantity - 5 WHERE product_id = 123",
      "INSERT INTO orders (product_id, quantity) VALUES (123, 5)"
    ]
  }
}
# All succeed or all rollback!
```

### Execute Stored Procedure
```python
{
  "tool": "execute_stored_procedure",
  "arguments": {
    "procedure_name": "dbo.GetUsersByRole",
    "parameters": {
      "role": "admin",
      "active_only": true
    }
  }
}
```

## Documentation

- **[USAGE_GUIDE.md](USAGE_GUIDE.md)** - Complete user guide with examples
- **[CHANGELOG.md](CHANGELOG.md)** - Version history and changes
- **[SECURITY.md](SECURITY.md)** - Security best practices

## Contributing

Contributions are welcome! Please see our improvement plan in `.agent/workflows/plan-de-mejoras.md`.

## License

MIT
