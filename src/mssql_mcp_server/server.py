import asyncio
import json
import logging
import os
import re
from urllib.parse import quote, unquote
import pymssql
from mcp.server import Server
from mcp.types import Resource, Tool, TextContent
from pydantic import AnyUrl

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("mssql_mcp_server")


def validate_table_name(table_name: str) -> str:
    """Validate and escape table name to prevent SQL injection."""
    # Allow only alphanumeric, underscore, and dot (for schema.table)
    if not re.match(r"^[a-zA-Z0-9_]+(\.[a-zA-Z0-9_]+)?$", table_name):
        raise ValueError(f"Invalid table name: {table_name}")

    # Split schema and table if present
    parts = table_name.split(".")
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
    logger.info(
        f"MSSQL_SERVER environment variable: {os.getenv('MSSQL_SERVER', 'NOT SET')}"
    )
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
            config["server"] += (
                ";Encrypt=yes;TrustServerCertificate=yes"  # Add explicit setting
            )

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
            logger.error(
                "Missing required database configuration. Please check environment variables:"
            )
            logger.error("MSSQL_USER, MSSQL_PASSWORD, and MSSQL_DATABASE are required")
            raise ValueError("Missing required database configuration")

    return config


def build_connection_config(conn: dict) -> dict:
    """Build a pymssql-compatible config dict from a connection definition.

    Accepts a dict with keys: server, database, user, password, port,
    encrypt, windows_auth.  Applies the same Azure/LocalDB/encryption
    logic as get_db_config() so every connection is treated uniformly.
    """
    server = conn.get("server", "localhost")

    # Handle LocalDB connections
    if server.startswith("(localdb)\\"):
        instance_name = server.replace("(localdb)\\", "")
        server = f".\\{instance_name}"
        logger.info(f"Detected LocalDB connection, converted to: {server}")

    config: dict = {
        "server": server,
        "database": conn.get("database"),
    }

    # Port
    port = conn.get("port")
    if port is not None:
        try:
            config["port"] = int(port)
        except (ValueError, TypeError):
            logger.warning(f"Invalid port value: {port}. Skipping.")

    # Encryption / Azure
    if config["server"] and ".database.windows.net" in config["server"]:
        config["tds_version"] = "7.4"
        encrypt = str(conn.get("encrypt", "true")).lower()
        if encrypt == "true":
            config["server"] += ";Encrypt=yes;TrustServerCertificate=no"
    else:
        encrypt = str(conn.get("encrypt", "false")).lower()
        if encrypt == "true":
            config["tds_version"] = "7.4"
            config["server"] += ";Encrypt=yes;TrustServerCertificate=yes"

    # Authentication
    use_windows_auth = str(conn.get("windows_auth", "false")).lower() == "true"

    if use_windows_auth:
        if not config["database"]:
            raise ValueError("Missing required database configuration")
        logger.info("Using Windows Authentication")
    else:
        user = conn.get("user")
        password = conn.get("password")
        if not all([user, password, config["database"]]):
            raise ValueError(
                "Missing required database configuration "
                "(user, password, and database are required for SQL authentication)"
            )
        config["user"] = user
        config["password"] = password

    return config


def _scan_host_env_vars() -> list[dict] | None:
    """Scan for ``MSSQL_HOST_<N>_*`` indexed environment variables.

    Returns a list of connection dicts (one per host index found) or
    ``None`` when no ``MSSQL_HOST_*`` variables exist.

    Expected env var pattern::

        MSSQL_HOST_1_NAME=prod
        MSSQL_HOST_1_SERVER=prod.example.com
        MSSQL_HOST_1_DATABASE=proddb
        MSSQL_HOST_1_USER=admin
        MSSQL_HOST_1_PASSWORD=secret
        MSSQL_HOST_1_PORT=1433
        MSSQL_HOST_1_ENCRYPT=false
        MSSQL_HOST_1_WINDOWS_AUTH=false

    Indexes don't have to be contiguous — any positive integer works.
    """
    # Collect all unique host indexes present in the environment.
    prefix = "MSSQL_HOST_"
    indexes: set[int] = set()
    for key in os.environ:
        if key.startswith(prefix):
            # e.g. MSSQL_HOST_1_NAME → parts = ["MSSQL", "HOST", "1", "NAME"]
            parts = key.split("_")
            if len(parts) >= 4:
                try:
                    indexes.add(int(parts[2]))
                except ValueError:
                    continue

    if not indexes:
        return None

    # Map of allowed suffixes → connection-dict key
    field_map = {
        "NAME": "name",
        "SERVER": "server",
        "DATABASE": "database",
        "USER": "user",
        "PASSWORD": "password",
        "PORT": "port",
        "ENCRYPT": "encrypt",
        "WINDOWS_AUTH": "windows_auth",
    }

    hosts: list[dict] = []
    for idx in sorted(indexes):
        entry: dict = {}
        for suffix, field in field_map.items():
            value = os.getenv(f"{prefix}{idx}_{suffix}")
            if value is not None:
                entry[field] = value
        if entry:
            hosts.append(entry)

    return hosts if hosts else None


def get_all_connections() -> dict[str, dict]:
    """Return a mapping of connection-name → pymssql config dict.

    Resolution order:
    1. ``MSSQL_HOST_<N>_*`` indexed environment variables.
    2. ``MSSQL_CONNECTIONS_FILE`` – path to a JSON file with an array of
       connection objects (each must have a ``name`` key).
    3. Fall back to the legacy single-connection env vars via
       ``get_db_config()`` (connection name = ``"default"``).
    """
    raw: list[dict] | None = None

    # 1. Indexed env vars  (MSSQL_HOST_1_*, MSSQL_HOST_2_*, …)
    raw = _scan_host_env_vars()
    if raw is not None:
        logger.info(f"Loaded {len(raw)} connection(s) from MSSQL_HOST_* env vars")

    # 2. File-based config
    if raw is None:
        connections_file = os.getenv("MSSQL_CONNECTIONS_FILE")
        if connections_file:
            try:
                with open(connections_file, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                logger.info(f"Loaded connections from file: {connections_file}")
            except (OSError, json.JSONDecodeError) as exc:
                logger.error(
                    f"Failed to load connections file '{connections_file}': {exc}"
                )
                raise ValueError(f"Invalid connections file: {exc}") from exc

    # 3. Legacy single-host fallback
    if raw is None:
        return {"default": get_db_config()}

    # Validate & build
    if not isinstance(raw, list) or len(raw) == 0:
        raise ValueError("MSSQL connections config must be a non-empty JSON array")

    connections: dict[str, dict] = {}
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"Connection entry at index {idx} is not a JSON object")
        name = entry.get("name")
        if not name or not isinstance(name, str):
            raise ValueError(
                f"Connection entry at index {idx} is missing a valid 'name' field"
            )
        if name in connections:
            raise ValueError(f"Duplicate connection name: '{name}'")
        connections[name] = build_connection_config(entry)

    return connections


def is_multi_host() -> bool:
    """Return True when multi-host configuration is active."""
    return bool(
        _scan_host_env_vars() is not None or os.getenv("MSSQL_CONNECTIONS_FILE")
    )


def get_connection_config(connection_name: str) -> dict:
    """Resolve a single connection by name."""
    conns = get_all_connections()
    if connection_name not in conns:
        available = ", ".join(sorted(conns.keys()))
        raise ValueError(
            f"Unknown connection '{connection_name}'. "
            f"Available connections: {available}"
        )
    return conns[connection_name]


def get_command():
    """Get the command to execute SQL queries."""
    return os.getenv("MSSQL_COMMAND", "execute_sql")


def is_select_query(query: str) -> bool:
    """
    Check if a query is a SELECT statement, accounting for comments.
    Handles both single-line (--) and multi-line (/* */) SQL comments.
    """
    # Remove multi-line comments /* ... */
    query_cleaned = re.sub(r"/\*.*?\*/", "", query, flags=re.DOTALL)

    # Remove single-line comments -- ...
    lines = query_cleaned.split("\n")
    cleaned_lines = []
    for line in lines:
        # Find -- comment marker and remove everything after it
        comment_pos = line.find("--")
        if comment_pos != -1:
            line = line[:comment_pos]
        cleaned_lines.append(line)

    query_cleaned = "\n".join(cleaned_lines)

    # Get the first non-empty word after stripping whitespace
    first_word = query_cleaned.strip().split()[0] if query_cleaned.strip() else ""
    return first_word.upper() == "SELECT"


# Initialize server
app = Server("mssql_mcp_server")


@app.list_resources()
async def list_resources() -> list[Resource]:
    """List SQL Server tables as resources.

    In multi-host mode the URI contains the connection name so that
    ``read_resource`` can route to the correct server:
        mssql://<connection>/<table>/data
    In single-host (legacy) mode the original format is preserved:
        mssql://<table>/data
    """
    connections = get_all_connections()
    multi = is_multi_host()
    resources: list[Resource] = []

    for conn_name, config in connections.items():
        try:
            db_conn = pymssql.connect(**config)
            cursor = db_conn.cursor()
            cursor.execute("""
                SELECT TABLE_NAME
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_TYPE = 'BASE TABLE'
            """)
            tables = cursor.fetchall()
            logger.info(f"[{conn_name}] Found tables: {tables}")

            for table in tables:
                if multi:
                    # Percent-encode connection and table names so URIs containing
                    # spaces or other characters illegal in the URI host/path stay
                    # valid (pydantic AnyUrl rejects raw spaces in the authority).
                    uri = (
                        f"mssql://{quote(conn_name, safe='')}"
                        f"/{quote(table[0], safe='')}/data"
                    )
                    label = f"[{conn_name}] Table: {table[0]}"
                    desc = f"Data in table {table[0]} on connection '{conn_name}'"
                else:
                    uri = f"mssql://{quote(table[0], safe='')}/data"
                    label = f"Table: {table[0]}"
                    desc = f"Data in table: {table[0]}"

                resources.append(
                    Resource(
                        uri=uri,
                        name=label,
                        mimeType="text/plain",
                        description=desc,
                    )
                )
            cursor.close()
            db_conn.close()
        except Exception as e:
            logger.error(f"[{conn_name}] Failed to list resources: {str(e)}")

    return resources


@app.read_resource()
async def read_resource(uri: AnyUrl) -> str:
    """Read table contents.

    URI formats:
        Single-host:  mssql://<table>/data
        Multi-host:   mssql://<connection>/<table>/data
    """
    uri_str = str(uri)
    logger.info(f"Reading resource: {uri_str}")

    if not uri_str.startswith("mssql://"):
        raise ValueError(f"Invalid URI scheme: {uri_str}")

    parts = uri_str[8:].split("/")
    multi = is_multi_host()

    if multi:
        # mssql://<connection>/<table>/data  →  parts = [conn, table, "data"]
        # Connection and table names are percent-encoded by list_resources,
        # so decode them before routing/lookup.
        if len(parts) < 2:
            raise ValueError(f"Invalid multi-host URI: {uri_str}")
        conn_name = unquote(parts[0])
        table = unquote(parts[1])
        config = get_connection_config(conn_name)
    else:
        # mssql://<table>/data  →  parts = [table, "data"]
        table = unquote(parts[0])
        config = get_db_config()

    try:
        safe_table = validate_table_name(table)

        db_conn = pymssql.connect(**config)
        cursor = db_conn.cursor()
        cursor.execute(f"SELECT TOP 100 * FROM {safe_table}")
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        result = [",".join(map(str, row)) for row in rows]
        cursor.close()
        db_conn.close()
        return "\n".join([",".join(columns)] + result)

    except Exception as e:
        logger.error(f"Database error reading resource {uri}: {str(e)}")
        raise RuntimeError(f"Database error: {str(e)}")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available SQL Server tools.

    In multi-host mode an extra ``connection`` parameter is required on the
    execute tool so the caller can target a specific server, and a
    ``list_connections`` tool is exposed.
    """
    command = get_command()
    multi = is_multi_host()
    logger.info("Listing tools...")

    # -- execute_sql (or custom command name) --------------------------------
    properties: dict = {
        "query": {
            "type": "string",
            "description": "The SQL query to execute",
        }
    }
    required = ["query"]

    if multi:
        connections = get_all_connections()
        conn_names = sorted(connections.keys())
        properties["connection"] = {
            "type": "string",
            "description": (
                "Name of the database connection to use. "
                f"Available: {', '.join(conn_names)}"
            ),
            "enum": conn_names,
        }
        required.append("connection")
        execute_description = (
            "Execute an SQL query on the specified SQL Server connection"
        )
    else:
        execute_description = "Execute an SQL query on the SQL Server"

    tools: list[Tool] = [
        Tool(
            name=command,
            description=execute_description,
            inputSchema={
                "type": "object",
                "properties": properties,
                "required": required,
            },
        )
    ]

    # -- list_connections (multi-host only) -----------------------------------
    if multi:
        tools.append(
            Tool(
                name="list_connections",
                description="List all configured database connections",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            )
        )

    return tools


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute SQL commands or list connections."""
    command = get_command()
    multi = is_multi_host()
    logger.info(f"Calling tool: {name} with arguments: {arguments}")

    # -- list_connections ----------------------------------------------------
    if name == "list_connections":
        connections = get_all_connections()
        lines: list[str] = []
        for cname, cfg in connections.items():
            server_info = cfg.get("server", "?")
            if "port" in cfg:
                server_info += f":{cfg['port']}"
            db = cfg.get("database", "?")
            user = cfg.get("user", "Windows Auth")
            lines.append(f"- {cname}: {server_info}/{db} (user: {user})")
        return [
            TextContent(
                type="text", text="Configured connections:\n" + "\n".join(lines)
            )
        ]

    # -- execute_sql (or custom command) -------------------------------------
    if name != command:
        raise ValueError(f"Unknown tool: {name}")

    query = arguments.get("query")
    if not query:
        raise ValueError("Query is required")

    # Resolve the target connection config
    if multi:
        connection_name = arguments.get("connection")
        if not connection_name:
            raise ValueError(
                "The 'connection' parameter is required in multi-host mode"
            )
        config = get_connection_config(connection_name)
    else:
        config = get_db_config()

    try:
        db_conn = pymssql.connect(**config)
        cursor = db_conn.cursor()
        cursor.execute(query)

        # Special handling for table listing
        if is_select_query(query) and "INFORMATION_SCHEMA.TABLES" in query.upper():
            tables = cursor.fetchall()
            result = ["Tables_in_" + config.get("database", "unknown")]
            result.extend([table[0] for table in tables])
            cursor.close()
            db_conn.close()
            return [TextContent(type="text", text="\n".join(result))]

        # Regular SELECT queries
        elif is_select_query(query):
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            result = [",".join(map(str, row)) for row in rows]
            cursor.close()
            db_conn.close()
            return [
                TextContent(type="text", text="\n".join([",".join(columns)] + result))
            ]

        # Non-SELECT queries
        else:
            db_conn.commit()
            affected_rows = cursor.rowcount
            cursor.close()
            db_conn.close()
            return [
                TextContent(
                    type="text",
                    text=f"Query executed successfully. Rows affected: {affected_rows}",
                )
            ]

    except Exception as e:
        logger.error(f"Error executing SQL '{query}': {e}")
        return [TextContent(type="text", text=f"Error executing query: {str(e)}")]


async def main():
    """Main entry point to run the MCP server."""
    from mcp.server.stdio import stdio_server

    logger.info("Starting MSSQL MCP server...")

    # Log connection info without exposing sensitive data
    connections = get_all_connections()
    multi = is_multi_host()

    if multi:
        logger.info(f"Multi-host mode: {len(connections)} connection(s) configured")
    else:
        logger.info("Single-host mode (legacy env vars)")

    for conn_name, config in connections.items():
        server_info = config.get("server", "?")
        if "port" in config:
            server_info += f":{config['port']}"
        user_info = config.get("user", "Windows Auth")
        logger.info(
            f"  [{conn_name}] {server_info}/{config.get('database', '?')} "
            f"as {user_info}"
        )

    async with stdio_server() as (read_stream, write_stream):
        try:
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options(),
            )
        except Exception as e:
            logger.error(f"Server error: {str(e)}", exc_info=True)
            raise


if __name__ == "__main__":
    asyncio.run(main())
