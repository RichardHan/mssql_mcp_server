"""Stored procedure handling for MSSQL MCP Server."""

import logging
import time
import json
from typing import Dict, Any, List, Optional
import pymssql

from ..core import ConnectionPool, QueryExecutionError
from ..security import validate_table_name, sanitize_error_message
from ..security.audit import audit_query

logger = logging.getLogger(__name__)


class StoredProcedureHandler:
    """Handler for stored procedure execution."""
    
    def __init__(self, pool: ConnectionPool):
        self.pool = pool
    
    def execute_procedure(
        self,
        procedure_name: str,
        parameters: Optional[Dict[str, Any]] = None,
        client_id: str = "unknown",
        timeout: int = 30
    ) -> Dict[str, Any]:
        """Execute a stored procedure.
        
        Args:
            procedure_name: Name of the stored procedure
            parameters: Dictionary of parameter names to values
            client_id: Client identifier for auditing
            timeout: Execution timeout in seconds
            
        Returns:
            Dict with procedure results
            
        Raises:
            QueryExecutionError: If execution fails
        """
        parameters = parameters or {}
        start_time = time.time()
        
        try:
            # Validate procedure name (same validation as table names)
            safe_proc_name = validate_table_name(procedure_name)
            
            with self.pool.get_connection(timeout=timeout) as conn:
                cursor = conn.cursor()
                
                # Build EXEC statement
                if parameters:
                    # Build parameter list
                    param_assignments = [
                        f"@{key} = %s" for key in parameters.keys()
                    ]
                    exec_statement = f"EXEC {safe_proc_name} {', '.join(param_assignments)}"
                    param_values = list(parameters.values())
                else:
                    exec_statement = f"EXEC {safe_proc_name}"
                    param_values = []
                
                logger.info(f"Executing stored procedure: {safe_proc_name}")
                logger.debug(f"Parameters: {parameters}")
                
                # Execute procedure
                if param_values:
                    cursor.execute(exec_statement, param_values)
                else:
                    cursor.execute(exec_statement)
                
                # Collect all result sets
                results = []
                result_set_count = 0
                
                while True:
                    # Get current result set
                    if cursor.description:
                        columns = [desc[0] for desc in cursor.description]
                        rows = cursor.fetchall()
                        
                        results.append({
                            'result_set': result_set_count,
                            'columns': columns,
                            'row_count': len(rows),
                            'rows': rows
                        })
                        
                        result_set_count += 1
                    
                    # Try to move to next result set
                    if not cursor.nextset():
                        break
                
                # Commit if needed
                conn.commit()
                
                # Get return value and output parameters (if any)
                # Note: pymssql doesn't directly support OUTPUT parameters
                # This would need to be handled differently
                
                duration_ms = (time.time() - start_time) * 1000
                
                # Audit
                audit_query(
                    exec_statement,
                    user="system",
                    result="success",
                    duration_ms=duration_ms,
                    client_id=client_id,
                    procedure_name=procedure_name
                )
                
                cursor.close()
                
                logger.info(
                    f"Stored procedure executed successfully: {procedure_name} "
                    f"({result_set_count} result sets in {duration_ms:.2f}ms)"
                )
                
                return {
                    'status': 'success',
                    'procedure_name': procedure_name,
                    'result_sets': results,
                    'result_set_count': result_set_count,
                    'duration_ms': duration_ms
                }
        
        except Exception as e:
            error_msg = sanitize_error_message(str(e))
            logger.error(f"Error executing stored procedure {procedure_name}: {error_msg}")
            
            # Audit failed execution
            duration_ms = (time.time() - start_time) * 1000
            audit_query(
                f"EXEC {procedure_name}",
                user="system",
                result="error",
                duration_ms=duration_ms,
                error=error_msg,
                client_id=client_id
            )
            
            raise QueryExecutionError(
                f"Failed to execute stored procedure: {error_msg}",
                details={
                    'procedure_name': procedure_name,
                    'parameters': parameters,
                    'error': error_msg
                }
            )
    
    def list_procedures(
        self,
        schema: Optional[str] = None
    ) -> List[Dict[str, str]]:
        """List available stored procedures.
        
        Args:
            schema: Optional schema to filter by
            
        Returns:
            List of procedure information
        """
        try:
            with self.pool.get_connection() as conn:
                cursor = conn.cursor()
                
                if schema:
                    query = """
                    SELECT 
                        ROUTINE_SCHEMA,
                        ROUTINE_NAME,
                        ROUTINE_TYPE,
                        CREATED,
                        LAST_ALTERED
                    FROM INFORMATION_SCHEMA.ROUTINES
                    WHERE ROUTINE_TYPE = 'PROCEDURE'
                        AND ROUTINE_SCHEMA = %s
                    ORDER BY ROUTINE_SCHEMA, ROUTINE_NAME
                    """
                    cursor.execute(query, (schema,))
                else:
                    query = """
                    SELECT 
                        ROUTINE_SCHEMA,
                        ROUTINE_NAME,
                        ROUTINE_TYPE,
                        CREATED,
                        LAST_ALTERED
                    FROM INFORMATION_SCHEMA.ROUTINES
                    WHERE ROUTINE_TYPE = 'PROCEDURE'
                    ORDER BY ROUTINE_SCHEMA, ROUTINE_NAME
                    """
                    cursor.execute(query)
                
                procedures = []
                for row in cursor.fetchall():
                    procedures.append({
                        'schema': row[0],
                        'name': row[1],
                        'type': row[2],
                        'created': str(row[3]) if row[3] else None,
                        'last_altered': str(row[4]) if row[4] else None,
                        'full_name': f"{row[0]}.{row[1]}"
                    })
                
                cursor.close()
                
                logger.info(f"Found {len(procedures)} stored procedures")
                return procedures
        
        except Exception as e:
            error_msg = sanitize_error_message(str(e))
            logger.error(f"Error listing stored procedures: {error_msg}")
            return []
    
    def get_procedure_parameters(
        self,
        procedure_name: str
    ) -> List[Dict[str, Any]]:
        """Get parameters for a stored procedure.
        
        Args:
            procedure_name: Name of the procedure
            
        Returns:
            List of parameter information
        """
        try:
            # Parse schema and name
            parts = procedure_name.split('.')
            if len(parts) == 2:
                schema, proc_name = parts
            else:
                schema = 'dbo'
                proc_name = procedure_name
            
            with self.pool.get_connection() as conn:
                cursor = conn.cursor()
                
                query = """
                SELECT 
                    PARAMETER_NAME,
                    DATA_TYPE,
                    CHARACTER_MAXIMUM_LENGTH,
                    PARAMETER_MODE,
                    ORDINAL_POSITION
                FROM INFORMATION_SCHEMA.PARAMETERS
                WHERE SPECIFIC_SCHEMA = %s
                    AND SPECIFIC_NAME = %s
                ORDER BY ORDINAL_POSITION
                """
                
                cursor.execute(query, (schema, proc_name))
                
                parameters = []
                for row in cursor.fetchall():
                    param_name = row[0]
                    # Remove @ prefix if present
                    if param_name and param_name.startswith('@'):
                        param_name = param_name[1:]
                    
                    parameters.append({
                        'name': param_name,
                        'data_type': row[1],
                        'max_length': row[2],
                        'mode': row[3],
                        'position': row[4]
                    })
                
                cursor.close()
                
                logger.debug(f"Found {len(parameters)} parameters for {procedure_name}")
                return parameters
        
        except Exception as e:
            error_msg = sanitize_error_message(str(e))
            logger.error(f"Error getting procedure parameters: {error_msg}")
            return []
