"""Custom exceptions for MSSQL MCP Server."""

from typing import Dict, Any, Optional


class MSSQLError(Exception):
    """Base exception for MSSQL MCP errors."""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary for JSON serialization."""
        return {
            'error': self.__class__.__name__,
            'message': self.message,
            'details': self.details
        }


class ConnectionError(MSSQLError):
    """Database connection failed."""
    pass


class QueryExecutionError(MSSQLError):
    """Query execution failed."""
    pass


class ValidationError(MSSQLError):
    """Invalid input or configuration error."""
    pass


class TransactionError(MSSQLError):
    """Transaction execution error."""
    pass


class PoolExhaustedError(MSSQLError):
    """Connection pool has no available connections."""
    pass
