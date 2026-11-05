"""Expose registered MCP handlers directly to unit-style tests."""

from mssql_mcp_server.server import (
    app as server_app,
)
from mssql_mcp_server.server import (
    call_tool,
    list_resources,
    list_tools,
    read_resource,
)


class DirectHandlerAdapter:
    """Keep Server metadata while calling decorated handlers directly."""

    call_tool = staticmethod(call_tool)
    list_resources = staticmethod(list_resources)
    list_tools = staticmethod(list_tools)
    read_resource = staticmethod(read_resource)

    def __getattr__(self, name):
        return getattr(server_app, name)


app = DirectHandlerAdapter()
