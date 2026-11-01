FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for pymssql
RUN apt-get update && apt-get install -y \
    freetds-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY . .

# Install the package (puts mssql_mcp_server on PYTHONPATH)
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir .

# Run the MCP server
CMD ["python", "-m", "mssql_mcp_server"]
