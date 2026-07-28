import asyncio
import contextlib
import logging
import os
import re

import pymssql
from mcp.server import Server
from mcp.types import Resource, TextContent, Tool, ToolAnnotations
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

def sanitize_error_message(message: str) -> str:
    """Remove credentials from database errors before reporting them."""
    message = re.sub(
        r"""(?i)(\b(?:password|pwd)\b\s*(?:=|:)?\s*)(?:'[^']*'|"[^"]*"|[^\s,;]+)""",
        r"\1<redacted>",
        message,
    )
    password = os.getenv("MSSQL_PASSWORD")
    if password:
        message = message.replace(password, "<redacted>")
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

def get_result_format(requested_format: str | None = None) -> ResultFormat:
    """Resolve a request override or the configured default result format."""
    configured_format = os.getenv("MSSQL_RESULT_FORMAT", ResultFormat.LEGACY.value)
    return ResultFormat.parse(requested_format, default=configured_format)

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
    Check if a query is a SELECT (or CTE/WITH) statement, accounting for comments.
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
    return first_word.upper() in ("SELECT", "WITH")


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
    except Exception as e:  # noqa: BLE001 — return empty list to client on any DB/listing failure
        logger.error("Failed to list resources: %s", sanitize_error_message(str(e)))
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
        if cursor.description is None:
            raise RuntimeError("Query did not return a result set")
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()

    except Exception as e:
        if isinstance(e, pymssql.OperationalError) and "timed out" in str(e).lower():
            logger.error(
                "Query timed out after %s seconds reading resource %s",
                os.getenv("MSSQL_QUERY_TIMEOUT", "300"),
                uri_str,
            )
            raise RuntimeError(
                "Query timed out and was terminated. Please narrow the query and try again."
            ) from e
        safe_message = sanitize_error_message(str(e))
        logger.error("Database error reading resource %s: %s", uri_str, safe_message)
        raise RuntimeError(f"Database error: {safe_message}") from e
    finally:
        if cursor is not None:
            with contextlib.suppress(Exception):
                cursor.close()
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()

    return format_tabular_result(
        columns,
        rows[:100],
        result_format,
        store=store,
        truncated=use_sentinel_row and len(rows) > 100,
    )

@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available SQL Server tools."""
    command = get_command()
    _validate_tool_names(command)
    logger.info("Listing tools...")
    writes_enabled = allow_writes()
    annotations = None
    if not writes_enabled:
        annotations = ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        )
    tools = [
        Tool(
            name=command,
            description=(
                "Execute an SQL query on the SQL Server"
                if writes_enabled
                else "Run a read-only SQL query"
            ),
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
            annotations=annotations,
        )
    ]
    store = get_result_store()
    if store.enabled:
        tools.extend(
            [
                Tool(
                    name=GET_RESULT_TOOL,
                    description="Read a stored SQL result using row pagination",
                    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
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
                    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
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
    logger.info("Calling tool: %s", name)

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
    writes_enabled = allow_writes()
    select_query = is_select_query(query)
    if not writes_enabled and not select_query:
        return [TextContent(
            type="text",
            text=(
                "Error: only SELECT queries are allowed in read-only mode. "
                "Set MSSQL_ALLOW_WRITES=1 to enable writes (Agent mode)."
            ),
        )]

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
        elif not writes_enabled:
            raise RuntimeError("Query did not return a result set")
        else:
            affected_rows = cursor.rowcount

        # Fetch before committing in write mode because commit can discard active results.
        if writes_enabled:
            conn.commit()

    except Exception as e:
        if isinstance(e, pymssql.OperationalError) and "timed out" in str(e).lower():
            logger.error(
                "Query timed out after %s seconds",
                os.getenv("MSSQL_QUERY_TIMEOUT", "300"),
            )
            raise RuntimeError(
                "Query timed out and was terminated. Please narrow the query and try again."
            ) from e
        safe_message = sanitize_error_message(str(e))
        logger.error("Error executing SQL query: %s", safe_message)
        raise RuntimeError(f"Error executing query: {safe_message}") from e
    finally:
        if cursor is not None:
            with contextlib.suppress(Exception):
                cursor.close()
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()

    # Keep result serialization and persistence outside the database exception
    # boundary so post-processing failures are not mislabeled as SQL errors.
    if columns is not None and rows is not None:
        if (
            result_format is ResultFormat.LEGACY
            and select_query
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
