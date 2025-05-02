"""Microsoft SQL Server MCP Server package."""

__version__ = "0.1.0"

import asyncio

from . import server


def main():
    """Main entry point for the package."""
    asyncio.run(server.main())


# Expose important items at package level
__all__ = ["main", "server"]
