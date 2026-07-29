import asyncio
import logging
import os
import re

import pymssql
from mcp.server import Server
from mcp.types import Resource, TextContent, Tool
from pydantic import AnyUrl

from .result_store import ResultStore
from .serialization import (
    ResultFormat,
    build_result,
    serialize_csv,
    serialize_envelope,
    serialize_json,
    serialize_legacy,
)

GET_RESULT_TOOL = "get_sql_result"
READ_RESULT_CHUNK_TOOL = "read_sql_result_chunk"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("mssql_mcp_server")

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
        "port": os.getenv("MSSQL_PORT", "1433"),  # Default MSSQL port
    }    
    # Port support (Issue #8)
    port = os.getenv("MSSQL_PORT")
    if port:
        try:
            config["port"] = int(port)
        except ValueError:
            logger.warning(f"Invalid MSSQL_PORT value: {port}. Using default port.")
            config["port"] = "1433"
    
    # Encryption settings for Azure SQL (Issue #11)
    # Check if we're connecting to Azure SQL
    if config["server"] and ".database.windows.net" in config["server"]:
        config["tds_version"] = "7.4"  # Required for Azure SQL
        # Azure SQL requires encryption - use connection string format for pymssql 2.3+
        # This improves upon TDS-only approach by being more explicit
        if os.getenv("MSSQL_ENCRYPT", "true").lower() == "true":
            config["server"] += ";Encrypt=yes;TrustServerCertificate=no"
    else:
        # For non-Azure connections, respect the MSSQL_ENCRYPT setting
        # Use connection string format in addition to TDS version for better compatibility
        encrypt_str = os.getenv("MSSQL_ENCRYPT", "false")
        if encrypt_str.lower() == "true":
            config["tds_version"] = "7.4"  # Keep existing TDS approach
            config["server"] += ";Encrypt=yes;TrustServerCertificate=yes"  # Add explicit setting
            
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
    
    return config

def get_command():
    """Get the command to execute SQL queries."""
    return os.getenv("MSSQL_COMMAND", "execute_sql")

def get_result_format(requested_format: str | None = None) -> ResultFormat:
    """Resolve a request override or the configured default result format."""
    configured_format = os.getenv("MSSQL_RESULT_FORMAT", ResultFormat.LEGACY.value)
    return ResultFormat.parse(requested_format, default=configured_format)

def sanitize_error_message(error: Exception) -> str:
    """Remove credentials from database errors before returning them to clients."""
    message = str(error)
    password = os.getenv("MSSQL_PASSWORD")
    if password:
        message = message.replace(password, "<redacted>")
    return re.sub(
        r"(?i)(password\s*(?:=|:)?\s*)(?:'[^']*'|\"[^\"]*\"|[^\s,;]+)",
        r"\1<redacted>",
        message,
    )

def get_result_store() -> ResultStore:
    """Create a result store from the current environment configuration."""
    return ResultStore.from_env()

def format_tabular_result(
    columns,
    rows,
    result_format: ResultFormat,
    *,
    store: ResultStore,
    force_store: bool = False,
    truncated: bool = False,
) -> str:
    """Return an inline result or a summary for a persisted oversized result."""
    materialized_rows = list(rows)

    # Without file-backed storage there is no threshold to evaluate. Preserve
    # the requested wire format directly and avoid building an unused canonical
    # JSON copy for legacy and CSV callers.
    if not store.enabled and not force_store:
        if result_format is ResultFormat.LEGACY:
            return serialize_legacy(columns, materialized_rows)
        if result_format is ResultFormat.CSV:
            return serialize_csv(columns, materialized_rows)
        envelope = build_result(columns, materialized_rows, truncated=truncated)
        return serialize_json(envelope)

    envelope = build_result(columns, materialized_rows, truncated=truncated)
    payload_bytes = serialize_json(envelope).encode("utf-8")
    if store.should_store(payload_bytes, envelope["row_count"], force=force_store):
        return serialize_json(store.store(envelope, payload_bytes))
    if result_format is ResultFormat.LEGACY:
        return serialize_legacy(columns, materialized_rows)
    return serialize_envelope(envelope, result_format)

def _validate_tool_names(command: str) -> None:
    if command in {GET_RESULT_TOOL, READ_RESULT_CHUNK_TOOL}:
        raise ValueError(f"MSSQL_COMMAND conflicts with reserved tool name '{command}'")

def is_select_query(query: str) -> bool:
    """
    Check if a query is a SELECT statement, accounting for comments.
    Handles both single-line (--) and multi-line (/* */) SQL comments.
    """
    # Remove multi-line comments /* ... */
    query_cleaned = re.sub(r'/\*.*?\*/', '', query, flags=re.DOTALL)
    
    # Remove single-line comments -- ...
    lines = query_cleaned.split('\n')
    cleaned_lines = []
    for line in lines:
        # Find -- comment marker and remove everything after it
        comment_pos = line.find('--')
        if comment_pos != -1:
            line = line[:comment_pos]
        cleaned_lines.append(line)
    
    query_cleaned = '\n'.join(cleaned_lines)
    
    # Get the first non-empty word after stripping whitespace
    first_word = query_cleaned.strip().split()[0] if query_cleaned.strip() else ""
    return first_word.upper() == "SELECT"


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
                    # MCP SDK 1.2 wraps string resources as text/plain. Keep the
                    # catalog consistent until the SDK can carry a dynamic MIME.
                    mimeType="text/plain",
                    description=f"Data in table: {table[0]}",
                )
            )
        return resources
    except Exception as e:  # noqa: BLE001 - MCP resource-list boundary
        logger.error(f"Failed to list resources: {e!s}")
        return []
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
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
    result_format = get_result_format()
    store = get_result_store()
    use_sentinel_row = result_format is ResultFormat.JSON or store.enabled
    row_limit = 101 if use_sentinel_row else 100
    
    conn = None
    cursor = None
    try:
        # Validate table name to prevent SQL injection
        safe_table = validate_table_name(table)
        
        conn = pymssql.connect(**config)
        cursor = conn.cursor()
        cursor.execute(f"SELECT TOP {row_limit} * FROM {safe_table}")
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        truncated = use_sentinel_row and len(rows) > 100
        return format_tabular_result(
            columns,
            rows[:100],
            result_format,
            store=store,
            truncated=truncated,
        )
                
    except Exception as e:  # noqa: BLE001 - MCP resource boundary
        logger.error(f"Database error reading resource {uri}: {e!s}")
        raise RuntimeError(f"Database error: {e!s}")
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()

@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available SQL Server tools."""
    command = get_command()
    _validate_tool_names(command)
    logger.info("Listing tools...")
    tools = [
        Tool(
            name=command,
            description="Execute an SQL query on the SQL Server",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The SQL query to execute",
                    },
                    "result_format": {
                        "type": "string",
                        "enum": [item.value for item in ResultFormat],
                        "description": (
                            "Optional query result format. Defaults to "
                            "MSSQL_RESULT_FORMAT or legacy."
                        ),
                    },
                    "store_result": {
                        "type": "boolean",
                        "description": (
                            "Persist the full JSON result even when it is below "
                            "the configured thresholds. Requires "
                            "MSSQL_RESULT_OUTPUT_DIR."
                        ),
                    },
                },
                "required": ["query"],
            },
        )
    ]
    store = get_result_store()
    if store.enabled:
        tools.extend(
            [
                Tool(
                    name=GET_RESULT_TOOL,
                    description="Read a stored SQL result using row pagination",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "result_id": {
                                "type": "string",
                                "pattern": "^[0-9a-f]{32}$",
                            },
                            "offset": {"type": "integer", "minimum": 0},
                            "limit": {"type": "integer", "minimum": 1},
                        },
                        "required": ["result_id"],
                    },
                ),
                Tool(
                    name=READ_RESULT_CHUNK_TOOL,
                    description=("Read bounded base64 chunks of a stored JSON result"),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "result_id": {
                                "type": "string",
                                "pattern": "^[0-9a-f]{32}$",
                            },
                            "offset_bytes": {
                                "type": "integer",
                                "minimum": 0,
                            },
                            "max_bytes": {"type": "integer", "minimum": 1},
                        },
                        "required": ["result_id"],
                    },
                ),
            ]
        )
    return tools

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute SQL commands."""
    command = get_command()
    _validate_tool_names(command)
    logger.info(f"Calling tool: {name} with arguments: {arguments}")

    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be an object")  # noqa: TRY004

    if name == GET_RESULT_TOOL:
        result_id = arguments.get("result_id")
        if not isinstance(result_id, str):
            raise ValueError("result_id is required")
        store = get_result_store()
        page = store.read_page(
            result_id,
            offset=arguments.get("offset", 0),
            limit=arguments.get("limit"),
        )
        return [TextContent(type="text", text=serialize_json(page))]

    if name == READ_RESULT_CHUNK_TOOL:
        result_id = arguments.get("result_id")
        if not isinstance(result_id, str):
            raise ValueError("result_id is required")
        store = get_result_store()
        chunk = store.read_chunk(
            result_id,
            offset_bytes=arguments.get("offset_bytes", 0),
            max_bytes=arguments.get("max_bytes"),
        )
        return [TextContent(type="text", text=serialize_json(chunk))]
    
    if name != command:
        raise ValueError(f"Unknown tool: {name}")
    
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Query is required")
    
    result_format = get_result_format(arguments.get("result_format"))
    force_store = arguments.get("store_result", False)
    if not isinstance(force_store, bool):
        raise ValueError("store_result must be a boolean")  # noqa: TRY004
    store = get_result_store()
    if force_store and not store.enabled:
        raise ValueError(
            "store_result requires MSSQL_RESULT_OUTPUT_DIR to be configured"
        )
    config = get_db_config()

    conn = None
    cursor = None
    columns = None
    rows = None
    affected_rows = None
    try:
        conn = pymssql.connect(**config)
        cursor = conn.cursor()
        cursor.execute(query)
        
        # A cursor description is the driver-level signal that the statement
        # returned rows. This also covers CTEs and stored procedures.
        if cursor.description is not None:
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
        else:
            affected_rows = cursor.rowcount
        
        # Commit every successfully executed statement, including statements
        # that return rows (for example UPDATE ... OUTPUT and stored procedures).
        # Result rows must be fetched before commit because some drivers discard
        # an active result set when the transaction is completed.
        conn.commit()
                
    except Exception as e:
        safe_message = sanitize_error_message(e)
        logger.error(f"Error executing SQL query: {safe_message}")
        raise RuntimeError(f"Error executing query: {safe_message}") from e
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()

    # Keep result serialization and persistence outside the database exception
    # boundary so post-processing failures are not mislabeled as SQL errors.
    if columns is not None and rows is not None:
        if (
            result_format is ResultFormat.LEGACY
            and is_select_query(query)
            and "INFORMATION_SCHEMA.TABLES" in query.upper()
        ):
            columns = ["Tables_in_" + config["database"]]
            rows = [[row[0]] for row in rows]
        text = format_tabular_result(
            columns,
            rows,
            result_format,
            store=store,
            force_store=force_store,
        )
        return [TextContent(type="text", text=text)]

    return [
        TextContent(
            type="text",
            text=f"Query executed successfully. Rows affected: {affected_rows}",
        )
    ]


async def main():
    """Main entry point to run the MCP server."""
    from mcp.server.stdio import stdio_server
    
    logger.info("Starting MSSQL MCP server...")
    config = get_db_config()
    # Log connection info without exposing sensitive data
    server_info = config['server']
    if 'port' in config:
        server_info += f":{config['port']}"
    user_info = config.get('user', 'Windows Auth')
    logger.info(f"Database config: {server_info}/{config['database']} as {user_info}")
    
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
