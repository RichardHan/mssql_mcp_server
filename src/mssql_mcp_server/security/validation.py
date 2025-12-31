"""Input validation and SQL injection prevention."""

import re
import logging
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field, validator

from ..core.exceptions import ValidationError

logger = logging.getLogger(__name__)


class QueryValidator(BaseModel):
    """Validator for SQL query requests."""
    query: str = Field(..., min_length=1, max_length=100000)
    timeout: int = Field(default=30, ge=1, le=300)
    max_rows: Optional[int] = Field(default=1000, ge=1, le=100000)
    
    @validator('query')
    def validate_query_not_empty(cls, v):
        """Ensure query is not empty or whitespace."""
        if not v or v.strip() == '':
            raise ValueError("Query cannot be empty")
        return v.strip()
    
    @validator('query')
    def validate_dangerous_patterns(cls, v):
        """Check for potentially dangerous patterns."""
        # These are warnings, not hard blocks, as legitimate queries may contain them
        dangerous_patterns = [
            (r'xp_cmdshell', 'xp_cmdshell detected - system command execution'),
            (r'sp_executesql', 'Dynamic SQL detected - ensure it is intentional'),
            (r'OPENROWSET', 'OPENROWSET detected - potential data exfiltration'),
            (r'OPENDATASOURCE', 'OPENDATASOURCE detected - potential data exfiltration'),
        ]
        
        for pattern, warning in dangerous_patterns:
            if re.search(pattern, v, re.IGNORECASE):
                logger.warning(f"Potentially dangerous pattern in query: {warning}")
        
        return v


class StoredProcedureRequest(BaseModel):
    """Validator for stored procedure execution."""
    procedure_name: str = Field(..., min_length=1, max_length=128)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    timeout: int = Field(default=30, ge=1, le=300)
    
    @validator('procedure_name')
    def validate_procedure_name(cls, v):
        """Validate stored procedure name format."""
        if not re.match(r'^[a-zA-Z0-9_]+(\\.[a-zA-Z0-9_]+)?$', v):
            raise ValueError(f"Invalid procedure name format: {v}")
        return v


class TransactionRequest(BaseModel):
    """Validator for transaction execution."""
    queries: List[str] = Field(..., min_items=1, max_items=100)
    timeout: int = Field(default=60, ge=1, le=600)
    
    @validator('queries')
    def validate_queries_not_empty(cls, v):
        """Ensure all queries are non-empty."""
        for i, query in enumerate(v):
            if not query or query.strip() == '':
                raise ValueError(f"Query at index {i} is empty")
        return [q.strip() for q in v]


def validate_table_name(table_name: str) -> str:
    """Validate and escape table name to prevent SQL injection.
    
    Args:
        table_name: The table name to validate
        
    Returns:
        str: Escaped table name with brackets
        
    Raises:
        ValidationError: If table name format is invalid
    """
    # Allow only alphanumeric, underscore, and dot (for schema.table)
    if not re.match(r'^[a-zA-Z0-9_]+(\\.[a-zA-Z0-9_]+)?$', table_name):
        raise ValidationError(
            f"Invalid table name: {table_name}",
            details={
                'table_name': table_name,
                'reason': 'Table name must contain only letters, numbers, underscores, and optional schema prefix',
                'example': 'dbo.Users or MyTable'
            }
        )
    
    # Split schema and table if present
    parts = table_name.split('.')
    if len(parts) == 2:
        # Escape both schema and table name
        escaped = f"[{parts[0]}].[{parts[1]}]"
    else:
        # Just table name
        escaped = f"[{table_name}]"
    
    logger.debug(f"Validated and escaped table name: {table_name} -> {escaped}")
    return escaped


def validate_query_params(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate query parameters for safe use.
    
    Args:
        params: Dictionary of query parameters
        
    Returns:
        Dict[str, Any]: Validated parameters
        
    Raises:
        ValidationError: If parameters are invalid
    """
    if params is None:
        return {}
    
    if not isinstance(params, dict):
        raise ValidationError(
            "Query parameters must be a dictionary",
            details={
                'type_received': type(params).__name__,
                'expected': 'dict'
            }
        )
    
    # Validate each parameter value
    validated = {}
    for key, value in params.items():
        # Ensure key is a valid identifier
        if not re.match(r'^[a-zA-Z0-9_]+$', key):
            raise ValidationError(
                f"Invalid parameter name: {key}",
                details={
                    'parameter': key,
                    'reason': 'Parameter names must contain only letters, numbers, and underscores'
                }
            )
        
        # Ensure value is a safe type
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise ValidationError(
                f"Invalid parameter type for {key}",
                details={
                    'parameter': key,
                    'type_received': type(value).__name__,
                    'allowed_types': ['str', 'int', 'float', 'bool', 'None']
                }
            )
        
        validated[key] = value
    
    return validated


def is_select_query(query: str) -> bool:
    """Check if a query is a SELECT statement, accounting for comments.
    
    Handles both single-line (--) and multi-line (/* */) SQL comments.
    
    Args:
        query: SQL query to check
        
    Returns:
        bool: True if query is a SELECT statement
    """
    # Remove multi-line comments /* ... */
    query_cleaned = re.sub(r'/\\*.*?\\*/', '', query, flags=re.DOTALL)
    
    # Remove single-line comments -- ...
    lines = query_cleaned.split('\\n')
    cleaned_lines = []
    for line in lines:
        # Find -- comment marker and remove everything after it
        comment_pos = line.find('--')
        if comment_pos != -1:
            line = line[:comment_pos]
        cleaned_lines.append(line)
    
    query_cleaned = '\\n'.join(cleaned_lines)
    
    # Get the first non-empty word after stripping whitespace
    first_word = query_cleaned.strip().split()[0] if query_cleaned.strip() else ""
    return first_word.upper() == "SELECT"


def sanitize_error_message(error_message: str) -> str:
    """Remove sensitive information from error messages.
    
    Args:
        error_message: Original error message
        
    Returns:
        str: Sanitized error message
    """
    # Remove potential passwords
    sanitized = re.sub(
        r"(password|pwd)(\\s*=\\s*|:\\s*)['\"]?[^'\"\\s]+['\"]?",
        r"\\1=***REDACTED***",
        error_message,
        flags=re.IGNORECASE
    )
    
    # Remove connection strings
    sanitized = re.sub(
        r"(Server|Data Source|Initial Catalog)=[^;]+",
        r"\\1=***REDACTED***",
        sanitized,
        flags=re.IGNORECASE
    )
    
    return sanitized
