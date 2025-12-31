"""Query handling for MSSQL MCP Server."""

import logging
import time
from typing import Dict, Any, Optional, List
import pymssql

from ..core import ConnectionPool, QueryExecutionError
from ..security import (
    is_select_query,
    sanitize_error_message,
    QueryValidator
)
from ..security.audit import audit_query
from ..utils import (
    QueryCache,
    SmartCache,
    paginate_query,
    format_table_result,
    estimate_query_cost,
    duration_to_human
)

logger = logging.getLogger(__name__)


class QueryHandler:
    """Handler for SQL query execution with caching and optimization."""
    
    def __init__(
        self,
        pool: ConnectionPool,
        cache: Optional[QueryCache] = None
    ):
        self.pool = pool
        self.cache = cache
        self.smart_cache = SmartCache(cache) if cache else None
    
    def execute_query(
        self,
        query: str,
        client_id: str = "unknown",
        timeout: int = 30,
        use_cache: bool = True,
        page: Optional[int] = None,
        page_size: int = 100,
        format_type: str = "csv"
    ) -> Dict[str, Any]:
        """Execute a SQL query with optional caching and pagination.
        
        Args:
            query: SQL query to execute
            client_id: Client identifier for auditing
            timeout: Query timeout in seconds
            use_cache: Whether to use query cache
            page: Optional page number for pagination (1-indexed)
            page_size: Number of rows per page
            format_type: Output format ('csv', 'table', 'json')
            
        Returns:
            Dict with query results
            
        Raises:
            QueryExecutionError: If query execution fails
        """
        start_time = time.time()
        
        try:
            # Validate query
            validated = QueryValidator(query=query, timeout=timeout)
            query = validated.query
            
            # Estimate query cost
            cost_estimate = estimate_query_cost(query)
            logger.debug(f"Query complexity: {cost_estimate['category']}")
            
            # Apply pagination if requested and query is SELECT
            original_query = query
            if page is not None and is_select_query(query):
                logger.info(f"Applying pagination: page={page}, size={page_size}")
                query = paginate_query(query, page, page_size)
                logger.info(f"Paginated query: {query}")
            
            # Check cache
            cached_result = None
            if use_cache and self.cache and is_select_query(original_query):
                cached_result = self.cache.get(query)
                
                if cached_result is not None:
                    duration_ms = (time.time() - start_time) * 1000
                    logger.info(f"Cache HIT - Query completed in {duration_ms:.2f}ms")
                    
                    return {
                        'status': 'success',
                        'from_cache': True,
                        'duration_ms': duration_ms,
                        **cached_result
                    }

            
            # Execute query
            logger.info(f"Executing query: {query[:200]}")
            with self.pool.get_connection(timeout=timeout) as conn:
                cursor = conn.cursor()
                cursor.execute(query)
                
                # Handle different query types
                if is_select_query(original_query):
                    columns = [desc[0] for desc in cursor.description]
                    rows = cursor.fetchall()
                    
                    # Format result
                    formatted_data = format_table_result(columns, rows, format_type)
                    
                    result = {
                        'type': 'SELECT',
                        'columns': columns,
                        'row_count': len(rows),
                        'rows': rows if format_type == 'json' else None,
                        'formatted_data': formatted_data if format_type != 'json' else None,
                        'complexity': cost_estimate
                    }
                    
                    # Cache SELECT results
                    if use_cache and self.cache:
                        self.cache.set(query, result)
                else:
                    # Non-SELECT query
                    conn.commit()
                    affected_rows = cursor.rowcount
                    
                    result = {
                        'type': 'WRITE',
                        'affected_rows': affected_rows,
                        'message': f"Query executed successfully. Rows affected: {affected_rows}"
                    }
                
                cursor.close()
            
            duration_ms = (time.time() - start_time) * 1000
            
            # Audit
            audit_query(
                query,
                user="system",
                result="success",
                duration_ms=duration_ms,
                row_count=result.get('row_count') or result.get('affected_rows', 0),
                client_id=client_id,
                complexity=cost_estimate['category']
            )
            
            logger.info(
                f"Query executed successfully in {duration_to_human(duration_ms/1000)} "
                f"({cost_estimate['category']} complexity)"
            )
            
            return {
                'status': 'success',
                'from_cache': False,
                'duration_ms': duration_ms,
                **result
            }
        
        except Exception as e:
            error_msg = sanitize_error_message(str(e))
            duration_ms = (time.time() - start_time) * 1000
            
            logger.error(f"Query execution error: {error_msg}")
            
            # Audit failure
            audit_query(
                query,
                user="system",
                result="error",
                duration_ms=duration_ms,
                error=error_msg,
                client_id=client_id
            )
            
            raise QueryExecutionError(
                f"Query execution failed: {error_msg}",
                details={
                    'query': query[:100] + ('...' if len(query) > 100 else ''),
                    'error': error_msg,
                    'duration_ms': duration_ms
                }
            )
    
    def explain_query(self, query: str) -> Dict[str, Any]:
        """Get execution plan for a query.
        
        Args:
            query: SQL query to explain
            
        Returns:
            Dict with execution plan
        """
        try:
            with self.pool.get_connection() as conn:
                cursor = conn.cursor()
                
                # Enable SHOWPLAN
                cursor.execute("SET SHOWPLAN_TEXT ON")
                cursor.execute(query)
                
                plan = []
                for row in cursor.fetchall():
                    plan.append(row[0])
                
                cursor.execute("SET SHOWPLAN_TEXT OFF")
                cursor.close()
                
                return {
                    'status': 'success',
                    'query': query[:100] + ('...' if len(query) > 100 else ''),
                    'execution_plan': '\n'.join(plan)
                }
        
        except Exception as e:
            error_msg = sanitize_error_message(str(e))
            logger.error(f"Error explaining query: {error_msg}")
            
            return {
                'status': 'error',
                'error': error_msg
            }
    
    def get_query_stats(self) -> Dict[str, Any]:
        """Get statistics about query execution.
        
        Returns:
            Dict with query statistics
        """
        stats = {
            'pool': self.pool.get_stats()
        }
        
        if self.cache:
            stats['cache'] = self.cache.get_stats()
        
        return stats
