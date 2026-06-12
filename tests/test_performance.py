"""Performance and load tests for production readiness."""
import pytest
import asyncio
import time
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor
import gc
import psutil
import os
from mssql_mcp_server.server import app


class TestPerformance:
    """Test performance characteristics under load."""
    
    @pytest.mark.asyncio
    async def test_query_response_time(self):
        """Test that queries respond within acceptable time limits."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        # Simulate reasonable query execution
        mock_cursor.description = [('id',), ('name',)]
        mock_cursor.fetchall.return_value = [(i, f'user_{i}') for i in range(100)]
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                start_time = time.time()
                result = await app.call_tool("query", {"query": "SELECT * FROM users"})
                end_time = time.time()
                
                # Query should complete in reasonable time (< 1 second for mock)
                assert end_time - start_time < 1.0
                assert len(result) == 1
                assert "user_99" in result[0].text
    
    @pytest.mark.asyncio
    async def test_concurrent_query_performance(self):
        """Test performance under concurrent query load."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        mock_cursor.description = [('count',)]
        mock_cursor.fetchall.return_value = [(42,)]
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                # Run 50 concurrent queries
                start_time = time.time()
                tasks = [
                    app.call_tool("query", {"query": f"SELECT COUNT(*) FROM table_{i}"})
                    for i in range(50)
                ]
                results = await asyncio.gather(*tasks)
                end_time = time.time()
                
                # All queries should complete
                assert len(results) == 50
                assert all("42" in r[0].text for r in results)
                
                # Should complete in reasonable time (< 5 seconds for 50 queries)
                assert end_time - start_time < 5.0
    
    @pytest.mark.asyncio
    async def test_large_result_set_performance(self):
        """Test performance with large result sets."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        # Create large result set (10,000 rows)
        large_result = [(i, f'user_{i}', f'email_{i}@test.com', i % 100) for i in range(10000)]
        mock_cursor.description = [('id',), ('name',), ('email',), ('status',)]
        mock_cursor.fetchall.return_value = large_result
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                start_time = time.time()
                result = await app.call_tool("query", {"query": "SELECT * FROM large_table"})
                end_time = time.time()
                
                # Should handle large results efficiently
                assert len(result) == 1
                lines = result[0].text.split('\n')
                assert len(lines) == 10001  # Header + 10000 rows
                
                # Should complete in reasonable time (< 10 seconds)
                assert end_time - start_time < 10.0


class TestMemoryUsage:
    """Test memory usage and leak prevention."""
    
    @pytest.mark.asyncio
    async def test_memory_usage_stability(self):
        """Test that memory usage remains stable over time."""
        if not hasattr(psutil.Process(), 'memory_info'):
            pytest.skip("Memory monitoring not available")
        
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        mock_cursor.fetchall.return_value = [('table1',), ('table2',)]
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                process = psutil.Process(os.getpid())
                
                # Get baseline memory
                gc.collect()
                baseline_memory = process.memory_info().rss / 1024 / 1024  # MB
                
                # Run many operations
                for _ in range(100):
                    await app.list_resources()
                
                # Check memory after operations
                gc.collect()
                final_memory = process.memory_info().rss / 1024 / 1024  # MB
                
                # Memory growth should be minimal (< 50 MB)
                memory_growth = final_memory - baseline_memory
                assert memory_growth < 50, f"Memory grew by {memory_growth} MB"
    
    @pytest.mark.asyncio
    async def test_large_data_memory_handling(self):
        """Test memory handling with large data sets."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        # Create very large result
        def generate_large_result():
            for i in range(100000):
                yield (i, f'data_{i}' * 100)  # Large strings
        
        mock_cursor.description = [('id',), ('data',)]
        mock_cursor.fetchall.return_value = list(generate_large_result())
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                # Should handle large data without excessive memory use
                result = await app.call_tool("query", {"query": "SELECT * FROM big_table"})
                
                # Result should be created
                assert len(result) == 1
                
                # Memory should be released after operation
                result = None
                gc.collect()


class TestLoadHandling:
    """Test system behavior under various load conditions."""
    
    @pytest.mark.asyncio
    async def test_burst_load_handling(self):
        """Test handling of sudden burst loads."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        mock_cursor.fetchall.return_value = [('result',)]
        mock_cursor.description = [('data',)]
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                # Simulate burst of 100 requests
                start_time = time.time()
                tasks = []
                for _ in range(100):
                    tasks.append(app.call_tool("query", {"query": "SELECT 1"}))
                
                results = await asyncio.gather(*tasks, return_exceptions=True)
                end_time = time.time()
                
                # Count successful results
                successful = sum(1 for r in results if not isinstance(r, Exception))
                
                # Most requests should succeed
                assert successful >= 90  # Allow 10% failure rate
                
                # Should complete within reasonable time
                assert end_time - start_time < 30.0
    
    @pytest.mark.asyncio
    async def test_sustained_load_handling(self):
        """Test handling of sustained load over time."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        mock_cursor.fetchall.return_value = [('ok',)]
        mock_cursor.description = [('status',)]
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                # Run continuous load for 10 seconds
                start_time = time.time()
                request_count = 0
                error_count = 0
                
                while time.time() - start_time < 10:
                    try:
                        result = await app.call_tool("query", {"query": "SELECT 'ok'"})
                        request_count += 1
                        assert "ok" in result[0].text
                    except Exception:
                        error_count += 1
                    
                    # Small delay to prevent overwhelming
                    await asyncio.sleep(0.01)
                
                # Should handle sustained load
                assert request_count > 500  # At least 50 req/sec
                assert error_count < request_count * 0.05  # Less than 5% errors


class TestScalability:
    """Test scalability characteristics."""
    
    @pytest.mark.asyncio
    async def test_resource_scaling(self):
        """Test handling of increasing number of resources."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        # Test with different table counts
        table_counts = [10, 100, 1000]
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                for count in table_counts:
                    # Create table list
                    tables = [(f'table_{i}',) for i in range(count)]
                    mock_cursor.fetchall.return_value = tables
                    
                    start_time = time.time()
                    resources = await app.list_resources()
                    end_time = time.time()
                    
                    assert len(resources) == count
                    
                    # Time should scale reasonably (not exponentially)
                    time_per_table = (end_time - start_time) / count
                    assert time_per_table < 0.01  # Less than 10ms per table
    
    @pytest.mark.asyncio
    async def test_query_complexity_scaling(self):
        """Test performance with increasingly complex queries."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_conn.cursor.return_value = mock_cursor
        
        with patch('pymssql.connect', return_value=mock_conn):
            with patch.dict('os.environ', {
                'MSSQL_USER': 'test',
                'MSSQL_PASSWORD': 'test',
                'MSSQL_DATABASE': 'testdb'
            }):
                # Test simple to complex queries
                queries = [
                    "SELECT 1",
                    "SELECT * FROM users WHERE id = 1",
                    "SELECT u.*, o.* FROM users u JOIN orders o ON u.id = o.user_id",
                    """SELECT u.name, COUNT(o.id), SUM(o.total), AVG(o.total)
                       FROM users u 
                       LEFT JOIN orders o ON u.id = o.user_id 
                       GROUP BY u.name 
                       HAVING COUNT(o.id) > 5
                       ORDER BY SUM(o.total) DESC"""
                ]
                
                mock_cursor.description = [('result',)]
                mock_cursor.fetchall.return_value = [('data',)]
                
                for query in queries:
                    start_time = time.time()
                    result = await app.call_tool("query", {"query": query})
                    end_time = time.time()
                    
                    # All queries should complete successfully
                    assert len(result) == 1
                    
                    # Response time should be reasonable
                    assert end_time - start_time < 2.0