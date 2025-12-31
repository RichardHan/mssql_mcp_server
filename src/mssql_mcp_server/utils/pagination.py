"""Query pagination utilities."""

import re
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class QueryPaginator:
    """Advanced query pagination for SQL Server."""
    
    @staticmethod
    def has_order_by(query: str) -> bool:
        """Check if query has ORDER BY clause.
        
        Args:
            query: SQL query to check
            
        Returns:
            bool: True if query has ORDER BY
        """
        # Simple check - could be improved with SQL parsing
        return bool(re.search(r'\bORDER\s+BY\b', query, re.IGNORECASE))
    
    @staticmethod
    def add_default_order_by(query: str) -> str:
        """Add default ORDER BY clause if missing.
        
        SQL Server requires ORDER BY for OFFSET/FETCH.
        
        Args:
            query: SQL query
            
        Returns:
            str: Query with ORDER BY clause
        """
        if QueryPaginator.has_order_by(query):
            return query
        
        # Add ORDER BY (SELECT NULL) for compatibility
        # This works but doesn't guarantee order
        return f"{query.rstrip(';')} ORDER BY (SELECT NULL)"
    
    @staticmethod
    def extract_top_clause(query: str) -> Tuple[Optional[int], str]:
        """Extract and remove TOP clause from query.
        
        Args:
            query: SQL query
            
        Returns:
            Tuple of (top_value, query_without_top)
        """
        # Match: SELECT TOP n or SELECT TOP (n)
        match = re.search(
            r'\bSELECT\s+TOP\s+\(?\s*(\d+)\s*\)?\s+',
            query,
            re.IGNORECASE
        )
        
        if match:
            top_value = int(match.group(1))
            query_without_top = query[:match.start(1)-4] + query[match.end():]
            return (top_value, query_without_top)
        
        return (None, query)
    
    @staticmethod
    def paginate(
        query: str,
        page: int = 1,
        page_size: int = 100,
        preserve_top: bool = False
    ) -> str:
        """Add pagination to a SELECT query using OFFSET/FETCH.
        
        Args:
            query: SQL SELECT query
            page: Page number (1-indexed)
            page_size: Number of rows per page
            preserve_top: If True, respects existing TOP clause
            
        Returns:
            str: Paginated query
        """
        if page < 1:
            raise ValueError("Page must be >= 1")
        
        if page_size < 1:
            raise ValueError("Page size must be >= 1")
        
        # Check if it's a SELECT query
        if not re.match(r'^\s*SELECT\b', query, re.IGNORECASE):
            logger.warning(f"Query is not a SELECT statement, skipping pagination")
            return query
        
        # Handle existing TOP clause
        top_value, query_no_top = QueryPaginator.extract_top_clause(query)
        
        if preserve_top and top_value:
            # Use the smaller of TOP value and requested page_size
            effective_page_size = min(top_value, page_size * page)
            page_size = min(page_size, top_value)
        else:
            query = query_no_top
        
        # Ensure ORDER BY exists
        query = QueryPaginator.add_default_order_by(query)
        
        # Calculate offset
        offset = (page - 1) * page_size
        
        # Add OFFSET/FETCH clause
        # Remove trailing semicolon if present
        query = query.rstrip().rstrip(';')
        
        paginated = f"{query} OFFSET {offset} ROWS FETCH NEXT {page_size} ROWS ONLY"
        
        logger.debug(f"Paginated query: page={page}, page_size={page_size}, offset={offset}")
        
        return paginated


def paginate_query(
    query: str,
    page: int = 1,
    page_size: int = 100
) -> str:
    """Convenience function to paginate a query.
    
    Args:
        query: SQL SELECT query
        page: Page number (1-indexed)
        page_size: Number of rows per page
        
    Returns:
        str: Paginated query
    """
    return QueryPaginator.paginate(query, page, page_size)


def calculate_total_pages(total_rows: int, page_size: int) -> int:
    """Calculate total number of pages.
    
    Args:
        total_rows: Total number of rows
        page_size: Number of rows per page
        
    Returns:
        int: Total number of pages
    """
    if page_size <= 0:
        raise ValueError("Page size must be > 0")
    
    return (total_rows + page_size - 1) // page_size


def get_count_query(query: str) -> str:
    """Generate a COUNT query from a SELECT query.
    
    Args:
        query: Original SELECT query
        
    Returns:
        str: COUNT query
    """
    # Simple implementation - wrap in COUNT
    # Remove ORDER BY and TOP for efficiency
    query_no_order = re.sub(
        r'\bORDER\s+BY\s+[^;]+',
        '',
        query,
        flags=re.IGNORECASE
    )
    
    # Remove TOP clause
    _, query_no_top = QueryPaginator.extract_top_clause(query_no_order)
    
    # Wrap in COUNT
    count_query = f"SELECT COUNT(*) as total FROM ({query_no_top.rstrip(';')}) as count_query"
    
    return count_query
