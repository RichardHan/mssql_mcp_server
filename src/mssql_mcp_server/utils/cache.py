"""Query caching for improved performance."""

import hashlib
import json
import logging
import time
import threading
from typing import Any, Optional, Dict, Tuple
from collections import OrderedDict

from ..core.config import CacheConfig

logger = logging.getLogger(__name__)


class QueryCache:
    """LRU cache for query results with TTL support."""
    
    def __init__(self, config: CacheConfig):
        self.config = config
        self.cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self.lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        
        logger.info(
            f"Query cache initialized: enabled={config.enabled}, "
            f"max_size={config.max_size}, ttl={config.ttl}s"
        )
    
    def _get_cache_key(self, query: str, params: Optional[Dict] = None) -> str:
        """Generate a unique cache key for a query.
        
        Args:
            query: SQL query string
            params: Optional query parameters
            
        Returns:
            str: SHA256 hash of query and parameters
        """
        # Normalize query (remove extra whitespace)
        normalized_query = ' '.join(query.split())
        
        # Include parameters in key if present
        if params:
            key_data = f"{normalized_query}:{json.dumps(params, sort_keys=True)}"
        else:
            key_data = normalized_query
        
        return hashlib.sha256(key_data.encode()).hexdigest()
    
    def get(self, query: str, params: Optional[Dict] = None) -> Optional[Any]:
        """Get cached result for a query.
        
        Args:
            query: SQL query string
            params: Optional query parameters
            
        Returns:
            Cached result if found and not expired, None otherwise
        """
        if not self.config.enabled:
            return None
        
        key = self._get_cache_key(query, params)
        current_time = time.time()
        
        with self.lock:
            if key in self.cache:
                entry = self.cache[key]
                
                # Check if expired
                if current_time - entry['timestamp'] < self.config.ttl:
                    # Move to end (most recently used)
                    self.cache.move_to_end(key)
                    self.hits += 1
                    
                    logger.debug(f"Cache HIT for query: {query[:50]}...")
                    return entry['result']
                else:
                    # Expired, remove entry
                    del self.cache[key]
                    logger.debug(f"Cache entry expired for query: {query[:50]}...")
            
            self.misses += 1
            logger.debug(f"Cache MISS for query: {query[:50]}...")
            return None
    
    def set(self, query: str, result: Any, params: Optional[Dict] = None):
        """Store query result in cache.
        
        Args:
            query: SQL query string
            result: Query result to cache
            params: Optional query parameters
        """
        if not self.config.enabled:
            return
        
        key = self._get_cache_key(query, params)
        
        with self.lock:
            # Check if we need to evict
            if len(self.cache) >= self.config.max_size and key not in self.cache:
                # Remove oldest entry (LRU)
                oldest_key = next(iter(self.cache))
                del self.cache[oldest_key]
                self.evictions += 1
                logger.debug(f"Evicted oldest cache entry (size={self.config.max_size})")
            
            # Store new entry
            self.cache[key] = {
                'result': result,
                'timestamp': time.time(),
                'query': query[:100]  # Store truncated query for debugging
            }
            
            # Move to end (most recently used)
            self.cache.move_to_end(key)
            
            logger.debug(f"Cached result for query: {query[:50]}...")
    
    def invalidate(self, query: str, params: Optional[Dict] = None):
        """Invalidate a specific cache entry.
        
        Args:
            query: SQL query string
            params: Optional query parameters
        """
        if not self.config.enabled:
            return
        
        key = self._get_cache_key(query, params)
        
        with self.lock:
            if key in self.cache:
                del self.cache[key]
                logger.debug(f"Invalidated cache for query: {query[:50]}...")
    
    def invalidate_pattern(self, table_name: str):
        """Invalidate all cache entries that reference a table.
        
        Useful when data in a table is modified.
        
        Args:
            table_name: Name of the table
        """
        if not self.config.enabled:
            return
        
        with self.lock:
            keys_to_remove = []
            
            for key, entry in self.cache.items():
                query = entry['query'].upper()
                if table_name.upper() in query:
                    keys_to_remove.append(key)
            
            for key in keys_to_remove:
                del self.cache[key]
            
            if keys_to_remove:
                logger.info(
                    f"Invalidated {len(keys_to_remove)} cache entries "
                    f"for table: {table_name}"
                )
    
    def clear(self):
        """Clear all cache entries."""
        with self.lock:
            self.cache.clear()
            logger.info("Cache cleared")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics.
        
        Returns:
            Dict with cache statistics
        """
        with self.lock:
            total_requests = self.hits + self.misses
            hit_rate = (self.hits / total_requests * 100) if total_requests > 0 else 0
            
            return {
                'enabled': self.config.enabled,
                'size': len(self.cache),
                'max_size': self.config.max_size,
                'ttl': self.config.ttl,
                'hits': self.hits,
                'misses': self.misses,
                'hit_rate': round(hit_rate, 2),
                'evictions': self.evictions,
                'total_requests': total_requests
            }
    
    def reset_stats(self):
        """Reset cache statistics."""
        with self.lock:
            self.hits = 0
            self.misses = 0
            self.evictions = 0
            logger.info("Cache statistics reset")


class SmartCache:
    """Smart cache that auto-invalidates on write operations."""
    
    def __init__(self, cache: QueryCache):
        self.cache = cache
        self.write_keywords = {'INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 'CREATE', 'TRUNCATE'}
    
    def is_write_query(self, query: str) -> bool:
        """Check if query is a write operation.
        
        Args:
            query: SQL query to check
            
        Returns:
            bool: True if write operation
        """
        first_word = query.strip().split()[0].upper() if query.strip() else ""
        return first_word in self.write_keywords
    
    def extract_table_names(self, query: str) -> list[str]:
        """Extract table names from a query.
        
        This is a simple implementation. A more robust version would use SQL parsing.
        
        Args:
            query: SQL query
            
        Returns:
            List of table names found in query
        """
        import re
        
        # Find table names after FROM, JOIN, INTO, UPDATE keywords
        patterns = [
            r'FROM\s+([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)?)',
            r'JOIN\s+([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)?)',
            r'INTO\s+([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)?)',
            r'UPDATE\s+([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)?)',
        ]
        
        tables = set()
        for pattern in patterns:
            matches = re.findall(pattern, query, re.IGNORECASE)
            tables.update(matches)
        
        return list(tables)
    
    def handle_query(self, query: str, params: Optional[Dict] = None) -> Tuple[bool, Optional[Any]]:
        """Handle a query with smart caching.
        
        For write queries, invalidates relevant cache entries.
        For read queries, checks cache first.
        
        Args:
            query: SQL query
            params: Optional query parameters
            
        Returns:
            Tuple of (should_execute, cached_result)
            - should_execute: True if query should be executed
            - cached_result: Cached result if available
        """
        if self.is_write_query(query):
            # For write queries, invalidate cache for affected tables
            tables = self.extract_table_names(query)
            for table in tables:
                self.cache.invalidate_pattern(table)
            
            return (True, None)  # Execute query
        else:
            # For read queries, check cache
            cached = self.cache.get(query, params)
            if cached is not None:
                return (False, cached)  # Use cached result
            else:
                return (True, None)  # Execute query
