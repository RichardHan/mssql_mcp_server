"""Security auditing and logging for MSSQL MCP Server."""

import logging
import json
from datetime import datetime
from typing import Optional, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)

# Create separate logger for security audit
security_logger = logging.getLogger("security_audit")
security_logger.setLevel(logging.INFO)


class SecurityAuditor:
    """Security audit logger for tracking database operations."""
    
    def __init__(self, log_file: Optional[Path] = None):
        self.log_file = log_file
        
        # Setup file handler if log file specified
        if log_file:
            self._setup_file_handler(log_file)
    
    def _setup_file_handler(self, log_file: Path):
        """Setup file handler for security audit logs."""
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            
            file_handler = logging.FileHandler(log_file)
            file_handler.setLevel(logging.INFO)
            
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            file_handler.setFormatter(formatter)
            
            security_logger.addHandler(file_handler)
            logger.info(f"Security audit logging to: {log_file}")
        except Exception as e:
            logger.error(f"Failed to setup audit log file: {e}")
    
    def log_query(
        self,
        query: str,
        user: str = "unknown",
        result: str = "success",
        duration_ms: Optional[float] = None,
        row_count: Optional[int] = None,
        client_id: Optional[str] = None,
        **extra
    ):
        """Log a database query for audit trail.
        
        Args:
            query: The SQL query executed
            user: User who executed the query
            result: Result status (success/error)
            duration_ms: Query duration in milliseconds
            row_count: Number of rows affected/returned
            client_id: Client identifier
            **extra: Additional context to log
        """
        audit_entry = {
            'timestamp': datetime.utcnow().isoformat(),
            'event_type': 'query_execution',
            'user': user,
            'client_id': client_id,
            'query': self._truncate_query(query),
            'result': result,
            'duration_ms': duration_ms,
            'row_count': row_count,
            **extra
        }
        
        security_logger.info(json.dumps(audit_entry))
    
    def log_connection(
        self,
        server: str,
        database: str,
        user: str = "unknown",
        result: str = "success",
        error: Optional[str] = None,
        client_id: Optional[str] = None,
        **extra
    ):
        """Log a database connection attempt.
        
        Args:
            server: Database server
            database: Database name
            user: User attempting connection
            result: Result status (success/error)
            error: Error message if failed
            client_id: Client identifier
            **extra: Additional context to log
        """
        audit_entry = {
            'timestamp': datetime.utcnow().isoformat(),
            'event_type': 'connection_attempt',
            'server': server,
            'database': database,
            'user': user,
            'client_id': client_id,
            'result': result,
            'error': error,
            **extra
        }
        
        security_logger.info(json.dumps(audit_entry))
    
    def log_security_event(
        self,
        event_type: str,
        severity: str,
        message: str,
        user: Optional[str] = None,
        client_id: Optional[str] = None,
        **extra
    ):
        """Log a security-related event.
        
        Args:
            event_type: Type of security event
            severity: Severity level (low/medium/high/critical)
            message: Event message
            user: User involved in event
            client_id: Client identifier
            **extra: Additional context to log
        """
        audit_entry = {
            'timestamp': datetime.utcnow().isoformat(),
            'event_type': event_type,
            'severity': severity,
            'message': message,
            'user': user,
            'client_id': client_id,
            **extra
        }
        
        # Use appropriate log level based on severity
        log_level = {
            'low': logging.INFO,
            'medium': logging.WARNING,
            'high': logging.ERROR,
            'critical': logging.CRITICAL
        }.get(severity, logging.INFO)
        
        security_logger.log(log_level, json.dumps(audit_entry))
    
    def _truncate_query(self, query: str, max_length: int = 500) -> str:
        """Truncate query for logging to prevent excessive log size.
        
        Args:
            query: SQL query to truncate
            max_length: Maximum length of query in log
            
        Returns:
            str: Truncated query
        """
        if len(query) <= max_length:
            return query
        
        return query[:max_length] + "... [truncated]"


# Global auditor instance
_global_auditor: Optional[SecurityAuditor] = None


def initialize_auditor(log_file: Optional[Path] = None) -> SecurityAuditor:
    """Initialize the global security auditor.
    
    Args:
        log_file: Optional path to audit log file
        
    Returns:
        SecurityAuditor: The initialized auditor
    """
    global _global_auditor
    _global_auditor = SecurityAuditor(log_file)
    return _global_auditor


def get_auditor() -> SecurityAuditor:
    """Get the global security auditor instance.
    
    Returns:
        SecurityAuditor: The global auditor instance
    """
    global _global_auditor
    if _global_auditor is None:
        _global_auditor = SecurityAuditor()
    return _global_auditor


def audit_query(
    query: str,
    user: str = "unknown",
    result: str = "success",
    **kwargs
):
    """Convenience function to audit a query using global auditor.
    
    Args:
        query: SQL query executed
        user: User who executed query
        result: Result status
        **kwargs: Additional context
    """
    auditor = get_auditor()
    auditor.log_query(query, user, result, **kwargs)


def audit_connection(
    server: str,
    database: str,
    user: str = "unknown",
    result: str = "success",
    **kwargs
):
    """Convenience function to audit a connection using global auditor.
    
    Args:
        server: Database server
        database: Database name
        user: User attempting connection
        result: Result status
        **kwargs: Additional context
    """
    auditor = get_auditor()
    auditor.log_connection(server, database, user, result, **kwargs)


def audit_security_event(
    event_type: str,
    severity: str,
    message: str,
    **kwargs
):
    """Convenience function to audit a security event using global auditor.
    
    Args:
        event_type: Type of security event
        severity: Severity level
        message: Event message
        **kwargs: Additional context
    """
    auditor = get_auditor()
    auditor.log_security_event(event_type, severity, message, **kwargs)
