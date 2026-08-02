import asyncio
import logging
import os
import re
import pymssql
from mcp.server import Server
from mcp.types import (
    CallToolResult,
    ListResourcesResult,
    ListToolsResult,
    ReadResourceResult,
    Resource,
    TextContent,
    TextResourceContents,
    Tool,
)

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
     # Query timeout support
    config["timeout"] = int(os.getenv("MSSQL_QUERY_TIMEOUT", "300"))
    return config

def get_command():
    """Get the command to execute SQL queries."""
    return os.getenv("MSSQL_COMMAND", "execute_sql")

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

# MCP handlers
#
# In mcp>=2.0.0 the low-level Server no longer uses decorators. Handlers are
# passed to the Server constructor as `on_*` keyword arguments, each receiving
# (ctx, params) and returning a typed result object. Return values are no
# longer auto-wrapped, so we build the ListToolsResult/CallToolResult/etc.
# explicitly.

def _close_quietly(cursor, conn):
    """Close cursor and connection if they were opened, ignoring errors."""
    if cursor is not None:
        try:
            cursor.close()
        except Exception:
            pass
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass

async def list_resources(ctx, params) -> ListResourcesResult:
    """List SQL Server tables as resources."""
    config = get_db_config()
    resources = []
    conn = cursor = None
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

        for table in tables:
            resources.append(
                Resource(
                    uri=f"mssql://{table[0]}/data",
                    name=f"Table: {table[0]}",
                    mime_type="text/plain",
                    description=f"Data in table: {table[0]}"
                )
            )
    except Exception as e:
        logger.error(f"Failed to list resources: {str(e)}")
    finally:
        _close_quietly(cursor, conn)
    return ListResourcesResult(resources=resources)

async def read_resource(ctx, params) -> ReadResourceResult:
    """Read table contents."""
    config = get_db_config()
    uri = params.uri
    uri_str = str(uri)
    logger.info(f"Reading resource: {uri_str}")

    if not uri_str.startswith("mssql://"):
        raise ValueError(f"Invalid URI scheme: {uri_str}")

    parts = uri_str[8:].split('/')
    table = parts[0]

    conn = cursor = None
    try:
        # Validate table name to prevent SQL injection
        safe_table = validate_table_name(table)

        conn = pymssql.connect(**config)
        cursor = conn.cursor()
        # Use TOP 100 for MSSQL (equivalent to LIMIT in MySQL)
        cursor.execute(f"SELECT TOP 100 * FROM {safe_table}")
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        result = [",".join(map(str, row)) for row in rows]
        text = "\n".join([",".join(columns)] + result)
        return ReadResourceResult(
            contents=[TextResourceContents(uri=uri, mime_type="text/plain", text=text)]
        )
    except Exception as e:
        logger.error(f"Error reading resource '{uri_str}': {e}")
        return ReadResourceResult(
            contents=[
                TextResourceContents(
                    uri=uri, mime_type="text/plain", text=f"Error reading resource: {str(e)}"
                )
            ]
        )
    finally:
        _close_quietly(cursor, conn)

async def list_tools(ctx, params) -> ListToolsResult:
    """List available SQL Server tools."""
    command = get_command()
    logger.info("Listing tools...")
    return ListToolsResult(
        tools=[
            Tool(
                name=command,
                description="Execute an SQL query on the SQL Server",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The SQL query to execute"
                        }
                    },
                    "required": ["query"]
                }
            )
        ]
    )

async def call_tool(ctx, params) -> CallToolResult:
    """Execute SQL commands."""
    config = get_db_config()
    command = get_command()
    name = params.name
    arguments = params.arguments or {}
    logger.info(f"Calling tool: {name} with arguments: {arguments}")

    if name != command:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Unknown tool: {name}")],
            is_error=True,
        )

    query = arguments.get("query")
    if not query:
        return CallToolResult(
            content=[TextContent(type="text", text="Query is required")],
            is_error=True,
        )

    conn = cursor = None
    try:
        conn = pymssql.connect(**config)
        cursor = conn.cursor()
        cursor.execute(query)

        # Special handling for table listing
        if is_select_query(query) and "INFORMATION_SCHEMA.TABLES" in query.upper():
            tables = cursor.fetchall()
            result = ["Tables_in_" + config["database"]]  # Header
            result.extend([table[0] for table in tables])
            return CallToolResult(content=[TextContent(type="text", text="\n".join(result))])

        # Regular SELECT queries
        elif is_select_query(query):
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            result = [",".join(map(str, row)) for row in rows]
            return CallToolResult(
                content=[TextContent(type="text", text="\n".join([",".join(columns)] + result))]
            )

        # Non-SELECT queries
        else:
            conn.commit()
            affected_rows = cursor.rowcount
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=f"Query executed successfully. Rows affected: {affected_rows}",
                    )
                ]
            )

    except Exception as e:
        logger.error(f"Error executing SQL '{query}': {e}")
        return CallToolResult(
            content=[TextContent(type="text", text=f"Error executing query: {str(e)}")],
            is_error=True,
        )
    finally:
        _close_quietly(cursor, conn)

# Initialize server with the handlers wired in as constructor parameters.
app = Server(
    "mssql_mcp_server",
    on_list_resources=list_resources,
    on_read_resource=read_resource,
    on_list_tools=list_tools,
    on_call_tool=call_tool,
)

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
        except Exception as e:
            logger.error(f"Server error: {str(e)}", exc_info=True)
            raise

if __name__ == "__main__":
    asyncio.run(main())
