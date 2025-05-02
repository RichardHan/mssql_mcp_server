# tests/conftest.py
import logging
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Setup logging for tests
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mssql_mcp_test")

# Create a mock pymssql module
mock_pymssql = MagicMock()


# Configure mock pymssql behaviors
class MockConnection:
    def __init__(self, server=None, user=None, password=None, database=None, port=None):
        self.server = server
        self.user = user
        self.password = password
        self.database = database
        self.port = port
        self.messages = []
        self.is_connected = True

    def cursor(self):
        return MockCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.is_connected = False


# Mock cursor class
class MockCursor:
    def __init__(self, connection):
        self.connection = connection
        self.description = [("id",), ("name",), ("value",)]
        self.rowcount = 0
        self._results = []

    def execute(self, query, params=None):
        self.last_query = query

        # Mock different query results
        if "SELECT @@VERSION" in query:
            self._results = [("Microsoft SQL Server 2019 (mock)",)]
            self.rowcount = 1
        elif "FROM INFORMATION_SCHEMA.TABLES" in query:
            self._results = [("test_table1",), ("test_table2",)]
            self.rowcount = 2
        elif query.startswith("SELECT"):
            self._results = [(1, "test1", 100), (2, "test2", 200)]
            self.rowcount = 2
        else:
            self._results = []
            self.rowcount = 2  # Pretend it affected rows

    def fetchone(self):
        if self._results:
            return self._results[0]
        return None

    def fetchall(self):
        return self._results

    def close(self):
        pass


# Add mock classes to mock module
mock_pymssql.connect = MockConnection
mock_pymssql.Error = Exception

# Mock the pymssql module import
try:
    import pymssql

    PYMSSQL_AVAILABLE = True
    logger.info("Using real pymssql module")
except (ImportError, ModuleNotFoundError, Exception) as e:
    sys.modules["pymssql"] = mock_pymssql
    pymssql = mock_pymssql
    PYMSSQL_AVAILABLE = False
    logger.warning(f"Using mock pymssql implementation: {str(e)}")


@pytest.fixture(scope="session")
def mssql_connection():
    """Create a test database connection."""
    return pymssql.connect(
        server="mock-server",
        user="mock-user",
        password="mock-password",
        database="mock-db",
        port=1433,
    )


@pytest.fixture
def mock_mssql_connection():
    """Create a mock database connection for unit tests."""
    return pymssql.connect(
        server="mock-server",
        user="mock-user",
        password="mock-password",
        database="mock-db",
        port=1433,
    )


@pytest.fixture(scope="session")
def mssql_cursor(mssql_connection):
    """Create a test cursor."""
    cursor = mssql_connection.cursor()
    yield cursor
    cursor.close()
