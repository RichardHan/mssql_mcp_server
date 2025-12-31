"""Helper utilities for MSSQL MCP Server."""

import logging
import re
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


def format_table_result(
    columns: List[str],
    rows: List[tuple],
    format_type: str = "csv"
) -> str:
    """Format query results in various formats.
    
    Args:
        columns: List of column names
        rows: List of row tuples
        format_type: Output format ('csv', 'table', 'json')
        
    Returns:
        str: Formatted result
    """
    if format_type == "csv":
        result = [",".join(columns)]
        result.extend([",".join(map(str, row)) for row in rows])
        return "\n".join(result)
    
    elif format_type == "table":
        # Calculate column widths
        widths = [len(col) for col in columns]
        for row in rows:
            for i, val in enumerate(row):
                widths[i] = max(widths[i], len(str(val)))
        
        # Create header
        header = " | ".join(col.ljust(widths[i]) for i, col in enumerate(columns))
        separator = "-+-".join("-" * width for width in widths)
        
        # Create rows
        result = [header, separator]
        for row in rows:
            result.append(" | ".join(str(val).ljust(widths[i]) for i, val in enumerate(row)))
        
        return "\n".join(result)
    
    elif format_type == "json":
        import json
        result = []
        for row in rows:
            result.append(dict(zip(columns, row)))
        return json.dumps(result, indent=2, default=str)
    
    else:
        raise ValueError(f"Unknown format type: {format_type}")


def parse_connection_string(conn_str: str) -> Dict[str, str]:
    """Parse a SQL Server connection string.
    
    Args:
        conn_str: Connection string
        
    Returns:
        Dict with connection parameters
    """
    params = {}
    
    # Split by semicolon
    parts = conn_str.split(';')
    
    for part in parts:
        if '=' in part:
            key, value = part.split('=', 1)
            key = key.strip().lower()
            value = value.strip()
            
            # Map common keys
            key_mapping = {
                'server': 'server',
                'data source': 'server',
                'database': 'database',
                'initial catalog': 'database',
                'user id': 'user',
                'uid': 'user',
                'password': 'password',
                'pwd': 'password',
            }
            
            mapped_key = key_mapping.get(key, key)
            params[mapped_key] = value
    
    return params


def estimate_query_cost(query: str) -> Dict[str, Any]:
    """Estimate the cost/complexity of a query.
    
    This is a simple heuristic-based estimation.
    
    Args:
        query: SQL query
        
    Returns:
        Dict with cost estimation
    """
    query_upper = query.upper()
    
    # Count complexity indicators
    joins = len(re.findall(r'\bJOIN\b', query_upper))
    subqueries = query.count('(SELECT')
    aggregations = len(re.findall(r'\b(COUNT|SUM|AVG|MAX|MIN|GROUP BY)\b', query_upper))
    where_clauses = len(re.findall(r'\bWHERE\b', query_upper))
    order_by = 1 if 'ORDER BY' in query_upper else 0
    
    # Calculate complexity score (0-100)
    complexity = min(100, (
        joins * 10 +
        subqueries * 15 +
        aggregations * 5 +
        where_clauses * 3 +
        order_by * 2
    ))
    
    # Estimate category
    if complexity < 20:
        category = "simple"
    elif complexity < 50:
        category = "moderate"
    elif complexity < 75:
        category = "complex"
    else:
        category = "very_complex"
    
    return {
        'complexity_score': complexity,
        'category': category,
        'joins': joins,
        'subqueries': subqueries,
        'aggregations': aggregations,
        'has_where': where_clauses > 0,
        'has_order_by': order_by > 0,
        'estimated_time_ms': complexity * 10,  # Rough estimate
    }


def extract_query_type(query: str) -> str:
    """Extract the type of SQL query.
    
    Args:
        query: SQL query
        
    Returns:
        str: Query type (SELECT, INSERT, UPDATE, DELETE, etc.)
    """
    first_word = query.strip().split()[0].upper() if query.strip() else "UNKNOWN"
    return first_word


def is_read_only_query(query: str) -> bool:
    """Check if a query is read-only.
    
    Args:
        query: SQL query
        
    Returns:
        bool: True if query is read-only
    """
    query_type = extract_query_type(query)
    read_only_types = {'SELECT', 'SHOW', 'DESCRIBE', 'EXPLAIN'}
    return query_type in read_only_types


def sanitize_table_name(table_name: str) -> str:
    """Sanitize a table name for display.
    
    Args:
        table_name: Table name
        
    Returns:
        str: Sanitized table name
    """
    # Remove brackets if present
    return table_name.replace('[', '').replace(']', '')


def build_where_clause(
    conditions: Dict[str, Any],
    operator: str = "AND"
) -> tuple[str, list]:
    """Build a WHERE clause from a dictionary of conditions.
    
    Args:
        conditions: Dict of column: value pairs
        operator: Logical operator (AND/OR)
        
    Returns:
        Tuple of (where_clause, parameters)
    """
    if not conditions:
        return ("", [])
    
    clauses = []
    params = []
    
    for column, value in conditions.items():
        if value is None:
            clauses.append(f"{column} IS NULL")
        else:
            clauses.append(f"{column} = ?")
            params.append(value)
    
    where_clause = f" {operator} ".join(clauses)
    return (f"WHERE {where_clause}", params)


def truncate_text(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """Truncate text to a maximum length.
    
    Args:
        text: Text to truncate
        max_length: Maximum length
        suffix: Suffix to add when truncated
        
    Returns:
        str: Truncated text
    """
    if len(text) <= max_length:
        return text
    
    return text[:max_length - len(suffix)] + suffix


def bytes_to_human(bytes_size: int) -> str:
    """Convert bytes to human-readable format.
    
    Args:
        bytes_size: Size in bytes
        
    Returns:
        str: Human-readable size
    """
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} PB"


def duration_to_human(seconds: float) -> str:
    """Convert duration in seconds to human-readable format.
    
    Args:
        seconds: Duration in seconds
        
    Returns:
        str: Human-readable duration
    """
    if seconds < 0.001:
        return f"{seconds * 1000000:.2f} µs"
    elif seconds < 1:
        return f"{seconds * 1000:.2f} ms"
    elif seconds < 60:
        return f"{seconds:.2f} s"
    elif seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.2f} min"
    else:
        hours = seconds / 3600
        return f"{hours:.2f} h"
