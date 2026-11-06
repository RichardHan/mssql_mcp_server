import asyncio
import contextlib
import logging
import os
import re

import pymssql
from mcp.server import Server
from mcp.types import Resource, TextContent, Tool, ToolAnnotations
from pydantic import AnyUrl
from sqlglot import exp, parse
from sqlglot.errors import ErrorLevel

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("mssql_mcp_server")

def sanitize_error_message(message: str) -> str:
    """Redact obvious credential material from error strings returned to clients."""
    # Matches: password='x', password "x", password: x, password 'x', with password 'x'
    message = re.sub(
        r"(?i)(password\s*[=:]?\s*)(['\"])([^'\"]+)\2",
        r"\1\2***\2",
        message,
    )
    message = re.sub(
        r"(?i)(password\s*[=:]\s*)(\S+)",
        r"\1***",
        message,
    )
    message = re.sub(
        r"(?i)(pwd\s*[=:]?\s*)(['\"])([^'\"]+)\2",
        r"\1\2***\2",
        message,
    )
    return message


def validate_table_name(table_name: str) -> str:
    """Validate and escape table name to prevent SQL injection."""
    # Allow only alphanumeric, underscore, and dot (for schema.table)
    if not re.match(r'^[a-zA-Z0-9_]+(\.[a-zA-Z0-9_]+)?$', table_name):
        raise ValueError(f"Invalid table name: {table_name}")
    
    # Split schema and table if present
    parts = table_name.split('.')
    if len(parts) == 2:
        # Escape both schema and table name
        return f"[{parts[0]}].[{parts[1]}]"
    else:
        # Just table name
        return f"[{table_name}]"

def allow_writes() -> bool:
    """Whether non-SELECT statements are permitted (default: read-only)."""
    return os.getenv("MSSQL_ALLOW_WRITES", "false").lower() in ("1", "true", "yes")

def get_db_config():
    """Get database configuration from environment variables."""
    # Basic configuration
    server = os.getenv("MSSQL_SERVER", "localhost")
    logger.info(f"MSSQL_SERVER environment variable: {os.getenv('MSSQL_SERVER', 'NOT SET')}")
    logger.info(f"Using server: {server}")
    
    # Handle LocalDB connections (Issue #6)
    # LocalDB format: (localdb)\instancename
    if server.startswith("(localdb)\\"):
        # For LocalDB, pymssql needs special formatting
        # Convert (localdb)\MSSQLLocalDB to localhost\MSSQLLocalDB with dynamic port
        instance_name = server.replace("(localdb)\\", "")
        server = f".\\{instance_name}"
        logger.info(f"Detected LocalDB connection, converted to: {server}")
    
    config = {
        "server": server,
        "user": os.getenv("MSSQL_USER"),
        "password": os.getenv("MSSQL_PASSWORD"),
        "database": os.getenv("MSSQL_DATABASE"),
    }
    # Port support (Issue #8) — only set when explicitly configured
    port = os.getenv("MSSQL_PORT")
    if port:
        try:
            config["port"] = int(port)
        except ValueError:
            logger.warning(f"Invalid MSSQL_PORT value: {port}. Using default port.")
    
    # Encryption settings (Issues #11, #33, #34)
    # pymssql.connect() accepts `encryption` (not `encrypt`); values:
    # 'default' | 'off' | 'request' | 'require'
    if config["server"] and ".database.windows.net" in config["server"]:
        config["tds_version"] = "7.4"  # Required for Azure SQL
        # Azure SQL requires encryption
        if os.getenv("MSSQL_ENCRYPT", "true").lower() == "true":
            config["encryption"] = "require"
    else:
        # For non-Azure connections, respect the MSSQL_ENCRYPT setting
        encrypt_str = os.getenv("MSSQL_ENCRYPT", "false")
        if encrypt_str.lower() == "true":
            config["encryption"] = "require"
        else:
            config["encryption"] = "off"

    # Trust server certificate (Issue #39)
    # FreeTDS/pymssql do not expose a first-class kwarg; when requested we append
    # the ODBC-style option that many FreeTDS builds honor on the server string.
    trust_env = os.getenv("MSSQL_TRUST_SERVER_CERTIFICATE")
    if trust_env is None and config.get("encryption") == "require":
        # Sensible defaults: Azure validates certs; on-prem encrypt often needs trust
        if config["server"] and ".database.windows.net" in str(config["server"]).split(";")[0]:
            trust_env = "false"
        else:
            trust_env = "true"
    if trust_env is not None:
        flag = "yes" if trust_env.lower() in ("1", "true", "yes") else "no"
        base_server = str(config["server"]).split(";")[0]
        config["server"] = f"{base_server};TrustServerCertificate={flag}"
            
    # Windows Authentication support (Issue #7)
    use_windows_auth = os.getenv("MSSQL_WINDOWS_AUTH", "false").lower() == "true"
    
    if use_windows_auth:
        # For Windows authentication, user and password are not required
        if not config["database"]:
            logger.error("MSSQL_DATABASE is required")
            raise ValueError("Missing required database configuration")
        # Remove user and password for Windows auth
        config.pop("user", None)
        config.pop("password", None)
        logger.info("Using Windows Authentication")
    else:
        # SQL Authentication - user and password are required
        if not all([config["user"], config["password"], config["database"]]):
            logger.error("Missing required database configuration. Please check environment variables:")
            logger.error("MSSQL_USER, MSSQL_PASSWORD, and MSSQL_DATABASE are required")
            raise ValueError("Missing required database configuration")

    # Query timeout support (Issue #40 / PR #41)
    try:
        config["timeout"] = int(os.getenv("MSSQL_QUERY_TIMEOUT", "300"))
    except ValueError:
        logger.warning("Invalid MSSQL_QUERY_TIMEOUT; using default 300 seconds")
        config["timeout"] = 300

    # Safety net: never pass unsupported `encrypt` kwarg to pymssql (PR #38 / issues #33/#34)
    config.pop("encrypt", None)
    
    return config

def get_command():
    """Get the MCP tool name (query for read-only, execute_sql for write mode)."""
    override = os.getenv("MSSQL_COMMAND")
    if override:
        return override
    return "execute_sql" if allow_writes() else "query"

# Statement roots permitted in read-only mode (fail-closed otherwise).
_READ_ONLY_ROOTS = (
    exp.Select,
    exp.Union,
    exp.Except,
    exp.Intersect,
)

# Function / procedure names that must not appear even inside an otherwise-SELECT AST.
# Defense in depth: parser gaps should not silently allow these.
_BLOCKED_NAME_FRAGMENTS = frozenset({
    "OPENROWSET",
    "OPENDATASOURCE",
    "OPENQUERY",
    "OPENXML",
    "XP_CMDSHELL",
    "SP_EXECUTESQL",
    "SP_OACREATE",
    "SP_OAMETHOD",
})


def _expression_name(node: exp.Expr) -> str | None:
    """Best-effort identifier/function name from a sqlglot node."""
    this = getattr(node, "this", None)
    if isinstance(this, str):
        return this
    if isinstance(this, exp.Identifier):
        return this.name
    if isinstance(this, exp.Expression) and hasattr(this, "name"):
        return this.name
    if hasattr(node, "name") and isinstance(node.name, str):
        return node.name
    return None


def _contains_blocked_name(tree: exp.Expression) -> bool:
    for node in tree.walk():
        name = _expression_name(node)
        if name and name.upper() in _BLOCKED_NAME_FRAGMENTS:
            return True
        # Also inspect raw SQL fragments for Command nodes
        if isinstance(node, (exp.Command, exp.Execute)):
            return True
    return False


def is_select_query(query: str) -> bool:
    """
    Fail-closed check: allow only a single read-only SELECT/WITH/set-operation.

    Uses sqlglot's T-SQL parser (not a prefix regex). Rejects stacked statements,
    SELECT INTO, DML/DDL/EXEC, and known dangerous table-valued helpers when the
    AST names them. Parse failures reject the query.

    Limitations (by design — prefer DB least-privilege as defense in depth):
    - Does not claim to prove arbitrary T-SQL is free of side effects.
    - Dialect/parser gaps fail closed (query denied) rather than allowed.
    - ``MSSQL_ALLOW_WRITES`` bypasses this gate entirely for opt-in write mode.
    """
    if not isinstance(query, str) or not query.strip():
        return False

    try:
        expressions = parse(query, read="tsql", error_level=ErrorLevel.RAISE)
    except Exception:  # noqa: BLE001 — fail closed on any parse/tokenize failure
        logger.info("Read-only gate: rejecting unparseable / unsupported T-SQL")
        return False

    # Drop empty placeholders and trailing semicolon markers.
    statements = [
        node
        for node in expressions
        if node is not None and not isinstance(node, exp.Semicolon)
    ]
    if len(statements) != 1:
        return False

    tree = statements[0]
    if not isinstance(tree, _READ_ONLY_ROOTS):
        return False

    # SELECT INTO writes a table — never read-only.
    if any(isinstance(node, exp.Into) for node in tree.find_all(exp.Into)):
        return False
    if isinstance(tree, exp.Select) and tree.args.get("into") is not None:
        return False

    return not _contains_blocked_name(tree)

# Initialize server
app = Server("mssql_mcp_server")

@app.list_resources()
async def list_resources() -> list[Resource]:
    """List SQL Server tables as resources."""
    config = get_db_config()
    conn = None
    cursor = None
    try:
        conn = pymssql.connect(**config)
        cursor = conn.cursor()
        # Query to get user tables from the current database
        cursor.execute("""
            SELECT TABLE_NAME 
            FROM INFORMATION_SCHEMA.TABLES 
            WHERE TABLE_TYPE = 'BASE TABLE'
        """)
        tables = cursor.fetchall()
        logger.info(f"Found tables: {tables}")
        
        resources = []
        for table in tables:
            resources.append(
                Resource(
                    uri=AnyUrl(f"mssql://{table[0]}/data"),
                    name=f"Table: {table[0]}",
                    mimeType="text/plain",
                    description=f"Data in table: {table[0]}"
                )
            )
        return resources
    except Exception as e:  # noqa: BLE001 — return empty list to client on any DB/listing failure
        logger.error(f"Failed to list resources: {e!s}")
        return []
    finally:
        if cursor is not None:
            with contextlib.suppress(Exception):
                cursor.close()
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()

@app.read_resource()
async def read_resource(uri: AnyUrl) -> str:
    """Read table contents."""
    config = get_db_config()
    uri_str = str(uri)
    logger.info(f"Reading resource: {uri_str}")
    
    if not uri_str.startswith("mssql://"):
        raise ValueError(f"Invalid URI scheme: {uri_str}")
        
    parts = uri_str[8:].split('/')
    table = parts[0]
    
    try:
        # Validate table name to prevent SQL injection
        safe_table = validate_table_name(table)
        
        conn = pymssql.connect(**config)
        cursor = conn.cursor()
        # Use TOP 100 for MSSQL (equivalent to LIMIT in MySQL)
        cursor.execute(f"SELECT TOP 100 * FROM {safe_table}")
        if cursor.description is None:
            raise RuntimeError("Query did not return a result set")
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        result = [",".join(map(str, row)) for row in rows]
        cursor.close()
        conn.close()
        return "\n".join([",".join(columns)] + result)
                
    except pymssql.OperationalError as e:
        if "timed out" in str(e).lower():
            logger.error(
                "Query timed out after %s seconds reading resource %s",
                os.getenv("MSSQL_QUERY_TIMEOUT", "300"),
                uri_str,
            )
            raise RuntimeError(
                "Query timed out and was terminated. Please narrow the query and try again."
            ) from e
        logger.error(f"Database error reading resource {uri}: {e!s}")
        raise RuntimeError(f"Database error: {e!s}") from e
    except Exception as e:
        logger.error(f"Database error reading resource {uri}: {e!s}")
        raise RuntimeError(f"Database error: {e!s}") from e

@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available SQL Server tools."""
    command = get_command()
    logger.info("Listing tools...")
    annotations = None
    if not allow_writes():
        annotations = ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        )
    return [
        Tool(
            name=command,
            description=(
                "Execute an SQL query on the SQL Server"
                if allow_writes()
                else "Run a read-only SQL query"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The SQL query to execute"
                    }
                },
                "required": ["query"]
            },
            annotations=annotations,
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute SQL commands."""
    command = get_command()
    logger.info(f"Calling tool: {name} with arguments: {arguments}")
    
    if name != command:
        raise ValueError(f"Unknown tool: {name}")
    
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Query is required")

    if not allow_writes() and not is_select_query(query):
        return [TextContent(
            type="text",
            text=(
                "Error: only SELECT queries are allowed in read-only mode. "
                "Set MSSQL_ALLOW_WRITES=1 to enable writes (Agent mode)."
            ),
        )]

    config = get_db_config()
    conn = None
    cursor = None
    try:
        conn = pymssql.connect(**config)
        cursor = conn.cursor()
        cursor.execute(query)
        
        # Special handling for table listing
        if is_select_query(query) and "INFORMATION_SCHEMA.TABLES" in query.upper():
            tables = cursor.fetchall()
            result = ["Tables_in_" + config["database"]]  # Header
            result.extend([table[0] for table in tables])
            return [TextContent(type="text", text="\n".join(result))]
        
        # Regular SELECT queries
        elif is_select_query(query):
            if cursor.description is None:
                raise RuntimeError("Query did not return a result set")
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            result = [",".join(map(str, row)) for row in rows]
            return [TextContent(type="text", text="\n".join([",".join(columns)] + result))]
        
        # Non-SELECT queries
        else:
            conn.commit()
            affected_rows = cursor.rowcount
            return [TextContent(type="text", text=f"Query executed successfully. Rows affected: {affected_rows}")]

    except pymssql.OperationalError as e:
        if "timed out" in str(e).lower():
            logger.error(
                "Query timed out after %s seconds: %s",
                os.getenv("MSSQL_QUERY_TIMEOUT", "300"),
                query[:200],
            )
            return [TextContent(
                type="text",
                text="Query timed out and was terminated. Please narrow the query and try again.",
            )]
        logger.error(f"Error executing SQL '{query}': {e}")
        return [TextContent(type="text", text=f"Error executing query: {sanitize_error_message(str(e))}")]
    except Exception as e:  # noqa: BLE001 — return sanitized error message to client
        logger.error(f"Error executing SQL '{query}': {e}")
        return [TextContent(type="text", text=f"Error executing query: {sanitize_error_message(str(e))}")]
    finally:
        if cursor is not None:
            with contextlib.suppress(Exception):
                cursor.close()
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()

async def main():
    """Main entry point to run the MCP server."""
    from mcp.server.stdio import stdio_server
    
    logger.info("Starting MSSQL MCP server...")
    config = get_db_config()
    # Log connection info without exposing sensitive data
    server_info = str(config['server']).split(';')[0]
    if 'port' in config:
        server_info += f":{config['port']}"
    user_info = config.get('user', 'Windows Auth')
    logger.info(f"Database config: {server_info}/{config['database']} as {user_info}")
    logger.info("Write mode: %s", "enabled" if allow_writes() else "read-only")
    
    async with stdio_server() as (read_stream, write_stream):
        try:
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options()
            )
        except Exception:
            logger.exception("Server error")
            raise

if __name__ == "__main__":
    asyncio.run(main())
