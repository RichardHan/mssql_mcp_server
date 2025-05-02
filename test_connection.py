#!/usr/bin/env python3
import argparse
import pymssql
import sys

def test_connection(server, user, password, database, port):
    """Test connection to MSSQL database."""
    try:
        print(f"Connecting to {server}:{port}/{database} as {user}...")
        conn = pymssql.connect(
            server=server,
            user=user,
            password=password,
            database=database,
            port=int(port)
        )
        cursor = conn.cursor()
        cursor.execute("SELECT @@VERSION")
        version = cursor.fetchone()[0]
        conn.close()
        print(f"Connection successful!")
        print(f"SQL Server Version: {version}")
        return True
    except Exception as e:
        print(f"Connection failed: {str(e)}")
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Test MSSQL connection')
    parser.add_argument('--server', default='localhost', help='MSSQL server name or IP')
    parser.add_argument('--user', required=True, help='Username')
    parser.add_argument('--password', required=True, help='Password')
    parser.add_argument('--database', required=True, help='Database name')
    parser.add_argument('--port', default='1433', help='Port number (default: 1433)')

    args = parser.parse_args()

    success = test_connection(
        args.server,
        args.user,
        args.password,
        args.database,
        args.port
    )

    sys.exit(0 if success else 1)
