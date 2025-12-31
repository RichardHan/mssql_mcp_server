"""Transaction handling for MSSQL MCP Server."""

import logging
import time
from typing import List, Dict, Any, Optional
import pymssql

from ..core import ConnectionPool, QueryExecutionError, TransactionError
from ..security import is_select_query, sanitize_error_message
from ..security.audit import audit_query

logger = logging.getLogger(__name__)


class TransactionHandler:
    """Handler for database transactions."""
    
    def __init__(self, pool: ConnectionPool):
        self.pool = pool
    
    def execute_transaction(
        self,
        queries: List[str],
        client_id: str = "unknown",
        timeout: int = 60
    ) -> Dict[str, Any]:
        """Execute multiple queries in a transaction.
        
        All queries execute successfully or all are rolled back.
        
        Args:
            queries: List of SQL queries to execute
            client_id: Client identifier for auditing
            timeout: Transaction timeout in seconds
            
        Returns:
            Dict with transaction results
            
        Raises:
            TransactionError: If transaction fails
            QueryExecutionError: If a query fails
        """
        if not queries:
            raise TransactionError("No queries provided for transaction")
        
        if len(queries) > 100:
            raise TransactionError(
                "Transaction too large",
                details={
                    'query_count': len(queries),
                    'max_allowed': 100,
                    'suggestion': 'Split into smaller transactions'
                }
            )
        
        start_time = time.time()
        results = []
        conn = None
        
        try:
            # Get connection from pool
            with self.pool.get_connection(timeout=timeout) as conn:
                cursor = conn.cursor()
                
                # Disable autocommit for transaction
                original_autocommit = conn.autocommit
                conn.autocommit = False
                
                try:
                    # Execute each query
                    for i, query in enumerate(queries):
                        query_start = time.time()
                        
                        try:
                            cursor.execute(query)
                            
                            # Get result based on query type
                            if is_select_query(query):
                                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                                rows = cursor.fetchall()
                                
                                results.append({
                                    'query_index': i,
                                    'query': query[:100] + ('...' if len(query) > 100 else ''),
                                    'type': 'SELECT',
                                    'columns': columns,
                                    'row_count': len(rows),
                                    'rows': rows[:10],  # Limit rows in response
                                    'duration_ms': (time.time() - query_start) * 1000
                                })
                            else:
                                affected_rows = cursor.rowcount
                                
                                results.append({
                                    'query_index': i,
                                    'query': query[:100] + ('...' if len(query) > 100 else ''),
                                    'type': 'WRITE',
                                    'affected_rows': affected_rows,
                                    'duration_ms': (time.time() - query_start) * 1000
                                })
                            
                            # Audit individual query
                            audit_query(
                                query,
                                user="system",
                                result="success",
                                duration_ms=(time.time() - query_start) * 1000,
                                client_id=client_id,
                                transaction_id=id(conn)
                            )
                            
                        except Exception as e:
                            error_msg = sanitize_error_message(str(e))
                            logger.error(f"Query {i} failed in transaction: {error_msg}")
                            
                            # Audit failed query
                            audit_query(
                                query,
                                user="system",
                                result="error",
                                duration_ms=(time.time() - query_start) * 1000,
                                error=error_msg,
                                client_id=client_id,
                                transaction_id=id(conn)
                            )
                            
                            raise QueryExecutionError(
                                f"Query {i} failed: {error_msg}",
                                details={
                                    'query_index': i,
                                    'query': query[:100],
                                    'error': error_msg
                                }
                            )
                    
                    # All queries succeeded, commit
                    conn.commit()
                    
                    total_duration = (time.time() - start_time) * 1000
                    
                    logger.info(
                        f"Transaction completed successfully: {len(queries)} queries "
                        f"in {total_duration:.2f}ms"
                    )
                    
                    return {
                        'status': 'success',
                        'query_count': len(queries),
                        'results': results,
                        'total_duration_ms': total_duration,
                        'committed': True
                    }
                    
                except Exception as e:
                    # Rollback on any error
                    logger.warning(f"Rolling back transaction due to error: {str(e)}")
                    conn.rollback()
                    
                    raise TransactionError(
                        "Transaction failed and was rolled back",
                        details={
                            'error': sanitize_error_message(str(e)),
                            'queries_attempted': len(results),
                            'total_queries': len(queries)
                        }
                    )
                
                finally:
                    # Restore autocommit
                    conn.autocommit = original_autocommit
                    cursor.close()
        
        except Exception as e:
            if isinstance(e, (TransactionError, QueryExecutionError)):
                raise
            
            error_msg = sanitize_error_message(str(e))
            logger.error(f"Transaction error: {error_msg}")
            
            raise TransactionError(
                f"Transaction execution failed: {error_msg}",
                details={'error': error_msg}
            )
    
    def validate_transaction(self, queries: List[str]) -> Dict[str, Any]:
        """Validate queries before executing transaction.
        
        Args:
            queries: List of queries to validate
            
        Returns:
            Dict with validation results
        """
        issues = []
        warnings = []
        
        # Check for empty queries
        for i, query in enumerate(queries):
            if not query or query.strip() == '':
                issues.append(f"Query {i} is empty")
        
        # Check for queries that might conflict
        has_ddl = False
        for i, query in enumerate(queries):
            first_word = query.strip().split()[0].upper() if query.strip() else ""
            
            if first_word in ['CREATE', 'ALTER', 'DROP']:
                has_ddl = True
                warnings.append(
                    f"Query {i} contains DDL ({first_word}). "
                    "DDL in transactions may have limitations."
                )
        
        # Check for multiple writes to same table
        # (Simple check - could be improved)
        write_queries = [q for q in queries if not is_select_query(q)]
        if len(write_queries) > 1:
            warnings.append(
                f"Transaction contains {len(write_queries)} write operations. "
                "Ensure proper order to avoid conflicts."
            )
        
        return {
            'valid': len(issues) == 0,
            'issues': issues,
            'warnings': warnings,
            'query_count': len(queries),
            'has_ddl': has_ddl
        }
