#!/usr/bin/env python
"""Comprehensive test runner for MSSQL MCP Server."""

import argparse
import subprocess
import sys


def run_command(cmd, description):
    """Run a command and handle output."""
    print(f"\n{'='*60}")
    print(f"Running: {description}")
    print(f"Command: {' '.join(cmd)}")
    print('='*60)
    
    result = subprocess.run(cmd, capture_output=False, check=False)
    if result.returncode != 0:
        print(f"❌ {description} failed with return code {result.returncode}")
        return False
    print(f"✅ {description} passed")
    return True

def main():
    parser = argparse.ArgumentParser(description="Run MSSQL MCP Server tests")
    parser.add_argument('--suite', choices=['all', 'unit', 'security', 'integration', 'performance', 'quality'],
                        default='all', help='Test suite to run')
    parser.add_argument('--coverage', action='store_true', help='Generate coverage report')
    parser.add_argument('--parallel', action='store_true', help='Run tests in parallel')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    args = parser.parse_args()
    
    # Base pytest command
    pytest_cmd = ['pytest']
    if args.verbose:
        pytest_cmd.append('-v')
    if args.parallel:
        pytest_cmd.extend(['-n', 'auto'])
    if args.coverage:
        pytest_cmd.extend(['--cov=src/mssql_mcp_server', '--cov-report=html', '--cov-report=term'])
    
    success = True
    
    if args.suite in ['all', 'quality']:
        # Code quality checks
        print("\n🔍 Running code quality checks...")
        
        if not run_command(['black', '--check', 'src', 'tests'], "Black formatting check"):
            success = False
        
        if not run_command(['ruff', 'check', 'src', 'tests'], "Ruff linting"):
            success = False
        
        if not run_command(['mypy', 'src', '--ignore-missing-imports'], "MyPy type checking"):
            success = False
    
    if args.suite in ['all', 'unit']:
        # Unit tests
        print("\n🧪 Running unit tests...")
        cmd = pytest_cmd + ['tests/test_config.py', 'tests/test_server.py',
                            'tests/test_serialization.py', 'tests/test_result_store.py',
                            'tests/test_result_export.py', 'tests/test_context_benchmark.py']
        if not run_command(cmd, "Unit tests"):
            success = False
    
    if args.suite in ['all', 'security']:
        # Security tests
        print("\n🔒 Running security tests...")
        cmd = pytest_cmd + ['tests/test_security.py']
        if not run_command(cmd, "Security tests"):
            success = False
        
        # Run security scanning
        print("\n🔍 Running security scans...")
        if not run_command(['safety', 'check'], "Safety dependency check"):
            print("⚠️  Security vulnerabilities found in dependencies")
        
        if not run_command(['bandit', '-r', 'src', '-f', 'json', '-o', 'bandit-report.json'], 
                          "Bandit security scan"):
            print("⚠️  Security issues found in code")
    
    if args.suite in ['all', 'integration']:
        # Integration tests
        print("\n🔗 Running integration tests...")
        cmd = pytest_cmd + ['tests/test_integration.py', 'tests/test_error_handling.py',
                            'tests/test_query_results.py']
        if not run_command(cmd, "Integration tests"):
            success = False
    
    if args.suite in ['all', 'performance']:
        # Performance tests
        print("\n⚡ Running performance tests...")
        cmd = pytest_cmd + ['tests/test_performance.py', '-s']
        if not run_command(cmd, "Performance tests"):
            success = False
    
    # Summary
    print(f"\n{'='*60}")
    if success:
        print("✅ All tests passed!")
        if args.coverage:
            print("📊 Coverage report generated in htmlcov/")
    else:
        print("❌ Some tests failed. Please review the output above.")
        sys.exit(1)

if __name__ == "__main__":
    main()