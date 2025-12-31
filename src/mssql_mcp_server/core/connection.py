"""Connection pooling for MSSQL MCP Server."""

import logging
import threading
import time
from queue import Queue, Empty, Full
from typing import Optional
import pymssql

from .config import DatabaseConfig
from .exceptions import ConnectionError, PoolExhaustedError

logger = logging.getLogger(__name__)


class PooledConnection:
    """Wrapper for a pooled database connection."""
    
    def __init__(self, connection: pymssql.Connection, pool: 'ConnectionPool'):
        self.connection = connection
        self.pool = pool
        self.in_use = True
        self.created_at = time.time()
        self.last_used = time.time()
    
    def __enter__(self):
        return self.connection
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.pool.release_connection(self)
        return False
    
    def is_alive(self) -> bool:
        """Check if connection is still alive."""
        try:
            cursor = self.connection.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
            return True
        except Exception as e:
            logger.warning(f"Connection health check failed: {e}")
            return False


class ConnectionPool:
    """Thread-safe connection pool for database connections."""
    
    def __init__(self, config: DatabaseConfig):
        self.config = config
        self.pool_config = config.pool_config
        self._pool: Queue = Queue(maxsize=self.pool_config.max_size)
        self._lock = threading.Lock()
        self._total_connections = 0
        self._closed = False
        
        # Pre-create minimum connections
        self._initialize_pool()
        
        logger.info(
            f"Connection pool initialized: min={self.pool_config.min_size}, "
            f"max={self.pool_config.max_size}, timeout={self.pool_config.timeout}s"
        )
    
    def _initialize_pool(self):
        """Create minimum number of connections."""
        pymssql_config = self.config.to_pymssql_config()
        
        for i in range(self.pool_config.min_size):
            try:
                conn = self._create_connection(pymssql_config)
                self._pool.put(PooledConnection(conn, self))
                self._total_connections += 1
                logger.debug(f"Created initial connection {i+1}/{self.pool_config.min_size}")
            except Exception as e:
                logger.error(f"Failed to create initial connection: {e}")
                raise ConnectionError(
                    "Failed to initialize connection pool",
                    details={
                        'error': str(e),
                        'server': self.config.server,
                        'database': self.config.database,
                        'suggestion': 'Check your database credentials and network connectivity'
                    }
                )
    
    def _create_connection(self, pymssql_config: dict) -> pymssql.Connection:
        """Create a new database connection."""
        try:
            logger.debug(f"Creating new connection to {pymssql_config.get('server')}")
            conn = pymssql.connect(**pymssql_config)
            logger.debug("Connection created successfully")
            return conn
        except Exception as e:
            logger.error(f"Failed to create connection: {e}")
            raise
    
    def get_connection(self, timeout: Optional[int] = None) -> PooledConnection:
        """Get a connection from the pool.
        
        Args:
            timeout: Maximum time to wait for a connection (default: pool timeout)
            
        Returns:
            PooledConnection: A pooled database connection
            
        Raises:
            PoolExhaustedError: If no connection available within timeout
            ConnectionError: If connection creation fails
        """
        if self._closed:
            raise ConnectionError("Connection pool is closed")
        
        timeout = timeout or self.pool_config.timeout
        pymssql_config = self.config.to_pymssql_config()
        
        try:
            # Try to get existing connection
            pooled_conn = self._pool.get(timeout=timeout)
            
            # Check if connection is still alive
            if not pooled_conn.is_alive():
                logger.warning("Got dead connection from pool, creating new one")
                try:
                    pooled_conn.connection.close()
                except:
                    pass
                
                with self._lock:
                    self._total_connections -= 1
                
                # Create new connection
                new_conn = self._create_connection(pymssql_config)
                pooled_conn = PooledConnection(new_conn, self)
                
                with self._lock:
                    self._total_connections += 1
            
            pooled_conn.in_use = True
            pooled_conn.last_used = time.time()
            
            logger.debug(f"Connection acquired from pool (total: {self._total_connections})")
            return pooled_conn
            
        except Empty:
            # Pool is empty, try to create new connection if under max
            with self._lock:
                if self._total_connections < self.pool_config.max_size:
                    try:
                        conn = self._create_connection(pymssql_config)
                        pooled_conn = PooledConnection(conn, self)
                        self._total_connections += 1
                        pooled_conn.in_use = True
                        pooled_conn.last_used = time.time()
                        
                        logger.info(
                            f"Created new connection (total: {self._total_connections}/"
                            f"{self.pool_config.max_size})"
                        )
                        return pooled_conn
                    except Exception as e:
                        logger.error(f"Failed to create new connection: {e}")
                        raise ConnectionError(
                            "Failed to create database connection",
                            details={
                                'error': str(e),
                                'server': self.config.server,
                                'database': self.config.database
                            }
                        )
            
            # Pool is exhausted
            raise PoolExhaustedError(
                "Connection pool exhausted",
                details={
                    'max_size': self.pool_config.max_size,
                    'timeout': timeout,
                    'suggestion': 'Increase pool size or reduce connection usage'
                }
            )
    
    def release_connection(self, pooled_conn: PooledConnection):
        """Return a connection to the pool."""
        if self._closed:
            try:
                pooled_conn.connection.close()
            except:
                pass
            return
        
        pooled_conn.in_use = False
        pooled_conn.last_used = time.time()
        
        # Check if connection is still healthy
        if pooled_conn.is_alive():
            try:
                self._pool.put_nowait(pooled_conn)
                logger.debug("Connection returned to pool")
            except Full:
                # Pool is full, close excess connection
                logger.warning("Pool full, closing excess connection")
                try:
                    pooled_conn.connection.close()
                except:
                    pass
                with self._lock:
                    self._total_connections -= 1
        else:
            # Connection is dead, don't return to pool
            logger.warning("Not returning dead connection to pool")
            try:
                pooled_conn.connection.close()
            except:
                pass
            with self._lock:
                self._total_connections -= 1
    
    def close_all(self):
        """Close all connections in the pool."""
        logger.info("Closing connection pool")
        self._closed = True
        
        # Close all pooled connections
        while not self._pool.empty():
            try:
                pooled_conn = self._pool.get_nowait()
                try:
                    pooled_conn.connection.close()
                except Exception as e:
                    logger.error(f"Error closing connection: {e}")
            except Empty:
                break
        
        with self._lock:
            self._total_connections = 0
        
        logger.info("Connection pool closed")
    
    def get_stats(self) -> dict:
        """Get pool statistics."""
        return {
            'total_connections': self._total_connections,
            'available_connections': self._pool.qsize(),
            'in_use_connections': self._total_connections - self._pool.qsize(),
            'max_size': self.pool_config.max_size,
            'min_size': self.pool_config.min_size,
            'closed': self._closed
        }
    
    def __del__(self):
        """Cleanup on deletion."""
        if not self._closed:
            self.close_all()
