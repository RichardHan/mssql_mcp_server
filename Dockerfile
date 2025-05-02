FROM python:3.11-slim

WORKDIR /app

# Install system dependencies including FreeTDS for pymssql
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    freetds-dev \
    freetds-bin \
    unixodbc \
    unixodbc-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .
COPY requirements-dev.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir -r requirements-dev.txt

# Copy project files
COPY . .

# Run the MCP server
CMD ["python", "-m", "mssql_mcp_server"]