# Microsoft SQL Server MCP Server (Ishaan's Version)

[![PyPI](https://img.shields.io/pypi/v/mssql_mcp_server_ishaan)](https://pypi.org/project/mssql_mcp_server_ishaan/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/pypi/pyversions/mssql_mcp_server_ishaan)](https://pypi.org/project/mssql_mcp_server_ishaan/)

A Model Context Protocol (MCP) server for secure SQL Server database access through Claude Desktop.

## 🚀 Quick Start

### Prerequisites
- **Python 3.11 or higher** (Python 3.12 recommended)
- **FreeTDS** (for SQL Server connectivity)

### Install FreeTDS
```bash
# macOS
brew install freetds

# Ubuntu/Debian
sudo apt-get install freetds-dev

# RHEL/CentOS
sudo yum install freetds-devel
```

### Install the Package
```bash
pip install mssql-mcp-server-ishaan
```

### Configure Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mssql": {
      "command": "python",
      "args": ["-m", "mssql_mcp_server_ishaan"],
      "env": {
        "MSSQL_CONNECTION_STRING": "server=your-server;database=your-db;uid=your-user;pwd=your-password;encrypt=true;trustServerCertificate=true"
      }
    }
  }
}
```

## 📋 Features

- **Secure SQL Server connectivity** with encrypted connections
- **Read-only operations** for safety (SELECT queries only)
- **Table schema inspection** and metadata retrieval
- **SQL injection protection** with parameterized queries
- **Connection validation** and error handling
- **Comprehensive logging** for debugging

## 🔧 Configuration Options

### Environment Variables

```bash
# Basic connection (choose one method)
MSSQL_CONNECTION_STRING="server=localhost;database=mydb;uid=user;pwd=pass"
# OR individual components:
MSSQL_SERVER=localhost
MSSQL_DATABASE=mydb
MSSQL_USERNAME=your_user
MSSQL_PASSWORD=your_password

# Optional settings
MSSQL_PORT=1433                 # Custom port (default: 1433)
MSSQL_ENCRYPT=true              # Enable encryption (true/false)
```

## 🛠️ Available Tools

### 1. `execute_query`
Execute SELECT queries on the database.

**Parameters:**
- `query` (string): SQL SELECT statement
- `parameters` (array, optional): Query parameters for safe parameterization

**Example:**
```sql
SELECT TOP 10 * FROM Users WHERE Department = ?
```

### 2. `list_tables`
List all tables in the database with their schemas.

### 3. `describe_table`
Get detailed schema information for a specific table.

**Parameters:**
- `table_name` (string): Name of the table to describe

### 4. `get_table_sample`
Retrieve a sample of data from a table.

**Parameters:**
- `table_name` (string): Name of the table
- `limit` (integer, optional): Number of rows to return (default: 10)

## 🔒 Security Features

- **Read-only access**: Only SELECT operations are allowed
- **SQL injection protection**: All queries use parameterized statements
- **Input validation**: Table names and queries are validated
- **Connection encryption**: Supports encrypted connections
- **Error sanitization**: Database errors are sanitized before returning

## 📦 Installation Methods

### Method 1: PyPI (Recommended)
```bash
pip install mssql-mcp-server-ishaan
```

### Method 2: From Source
```bash
git clone https://github.com/ishaan119/mssql_mcp_server.git
cd mssql_mcp_server
pip install -e .
```

## 🧪 Testing the Installation

```bash
# Test import
python -c "import mssql_mcp_server_ishaan; print('✅ Package imported successfully!')"

# Run the server (requires connection string)
python -m mssql_mcp_server_ishaan

# Or use the command-line script
mssql_mcp_server_ishaan
```

## 🐛 Troubleshooting

### Common Issues

1. **Python Version Error**
   ```
   ERROR: Requires-Python >=3.11
   ```
   **Solution**: Upgrade to Python 3.11 or higher
   ```bash
   brew install python@3.12  # macOS
   ```

2. **FreeTDS Not Found**
   ```
   error: Microsoft SQL Server Native Client
   ```
   **Solution**: Install FreeTDS
   ```bash
   brew install freetds  # macOS
   ```

3. **Connection Issues**
   - Verify your connection string format
   - Check firewall settings
   - Ensure SQL Server allows remote connections
   - Try adding `trustServerCertificate=true` for self-signed certificates

### Debug Mode

Enable detailed logging by setting the log level:
```bash
export PYTHONPATH=.
python -c "
import logging
logging.basicConfig(level=logging.DEBUG)
import mssql_mcp_server_ishaan
"
```

## 📚 Example Usage

### Basic Query
```json
{
  "tool": "execute_query",
  "arguments": {
    "query": "SELECT TOP 5 CustomerID, CompanyName FROM Customers"
  }
}
```

### Parameterized Query
```json
{
  "tool": "execute_query",
  "arguments": {
    "query": "SELECT * FROM Orders WHERE CustomerID = ? AND OrderDate > ?",
    "parameters": ["ALFKI", "2023-01-01"]
  }
}
```

### Table Information
```json
{
  "tool": "describe_table",
  "arguments": {
    "table_name": "Customers"
  }
}
```

## 🤝 Contributing

This is a fork of the original [mssql_mcp_server](https://github.com/RichardHan/mssql_mcp_server) by Richard Han. 

### Development Setup
```bash
git clone https://github.com/ishaan119/mssql_mcp_server.git
cd mssql_mcp_server
pip install -e ".[dev]"
```

### Running Tests
```bash
pytest
```

## 📄 License

MIT License - see [LICENSE](LICENSE) file for details.

## 🔗 Links

- **PyPI Package**: https://pypi.org/project/mssql-mcp-server-ishaan/
- **Original Repository**: https://github.com/RichardHan/mssql_mcp_server
- **Model Context Protocol**: https://modelcontextprotocol.io/

## 📈 Version History

- **v0.1.3**: Current stable version with Python 3.11+ support
- **v0.1.2**: Attempted Python 3.10+ support  
- **v0.1.1**: Attempted Python 3.9+ support
- **v0.1.0**: Initial release with Python 3.11+ requirement

---

**Note**: This package requires Python 3.11+ due to the Model Context Protocol (MCP) library dependency. For the best experience, use Python 3.12.
