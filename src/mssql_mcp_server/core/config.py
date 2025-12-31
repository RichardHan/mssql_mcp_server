"""Configuration management for MSSQL MCP Server."""

import os
import logging
from typing import Optional, Dict, Any
from pathlib import Path
from pydantic import BaseModel, Field, validator

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

logger = logging.getLogger(__name__)


class ConnectionPoolConfig(BaseModel):
    """Connection pool configuration."""
    min_size: int = Field(default=2, ge=1, le=50)
    max_size: int = Field(default=10, ge=1, le=100)
    timeout: int = Field(default=30, ge=1, le=300)
    
    @validator('max_size')
    def max_greater_than_min(cls, v, values):
        if 'min_size' in values and v < values['min_size']:
            raise ValueError('max_size must be greater than or equal to min_size')
        return v


class CacheConfig(BaseModel):
    """Cache configuration."""
    enabled: bool = Field(default=True)  # Enabled by default
    ttl: int = Field(default=300, ge=0, le=3600)
    max_size: int = Field(default=1000, ge=1, le=100000)




class DatabaseConfig(BaseModel):
    """Database configuration with validation."""
    server: str = Field(..., min_length=1)
    database: str = Field(..., min_length=1)
    user: Optional[str] = None
    password: Optional[str] = None
    port: int = Field(default=1433, ge=1, le=65535)
    encrypt: bool = Field(default=False)
    trust_server_certificate: bool = Field(default=False)
    windows_auth: bool = Field(default=False)
    connection_timeout: int = Field(default=30, ge=1, le=300)
    
    # Advanced settings
    pool_config: ConnectionPoolConfig = Field(default_factory=ConnectionPoolConfig)
    cache_config: CacheConfig = Field(default_factory=CacheConfig)
    
    @validator('server')
    def validate_server(cls, v):
        """Validate server format."""
        if not v or v.strip() == '':
            raise ValueError('Server cannot be empty')
        return v.strip()
    
    @validator('password', always=True)
    def validate_auth(cls, v, values):
        """Validate authentication configuration."""
        if not values.get('windows_auth') and not v:
            raise ValueError('Password required when not using Windows Authentication')
        return v
    
    def to_pymssql_config(self) -> Dict[str, Any]:
        """Convert to pymssql connection parameters."""
        config = {
            'server': self.server,
            'database': self.database,
            'port': self.port,
            'timeout': self.connection_timeout,
        }
        
        # Handle LocalDB connections
        if self.server.startswith("(localdb)\\"):
            instance_name = self.server.replace("(localdb)\\", "")
            config['server'] = f".\\{instance_name}"
            logger.info(f"Detected LocalDB connection, converted to: {config['server']}")
        
        # Authentication
        if not self.windows_auth:
            config['user'] = self.user
            config['password'] = self.password
        
        # Encryption for Azure SQL
        if ".database.windows.net" in self.server:
            config['tds_version'] = "7.4"
            if self.encrypt:
                config['server'] += ";Encrypt=yes;TrustServerCertificate=no"
        elif self.encrypt:
            config['tds_version'] = "7.4"
            trust_cert = "yes" if self.trust_server_certificate else "no"
            config['server'] += f";Encrypt=yes;TrustServerCertificate={trust_cert}"
        
        return config


def load_config_from_file(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load configuration from YAML file."""
    if not YAML_AVAILABLE:
        logger.warning("PyYAML not installed, skipping file configuration")
        return {}
    
    if config_path is None:
        config_path = Path("mssql_config.yml")
    
    if not config_path.exists():
        logger.debug(f"Config file not found: {config_path}")
        return {}
    
    try:
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
        logger.info(f"Loaded configuration from {config_path}")
        return config
    except Exception as e:
        logger.error(f"Error loading config file: {e}")
        return {}


def get_db_config(config_path: Optional[Path] = None) -> DatabaseConfig:
    """Get database configuration from environment variables and optional file.
    
    Environment variables take precedence over file configuration.
    """
    # Load from file first
    file_config = load_config_from_file(config_path)
    
    # Build configuration with environment variable precedence
    config_dict = {
        'server': os.getenv('MSSQL_SERVER', file_config.get('server', 'localhost')),
        'database': os.getenv('MSSQL_DATABASE', file_config.get('database')),
        'user': os.getenv('MSSQL_USER', file_config.get('user')),
        'password': os.getenv('MSSQL_PASSWORD', file_config.get('password')),
        'port': int(os.getenv('MSSQL_PORT', file_config.get('port', 1433))),
        'encrypt': os.getenv('MSSQL_ENCRYPT', str(file_config.get('encrypt', 'false'))).lower() == 'true',
        'trust_server_certificate': os.getenv('MSSQL_TRUST_CERT', str(file_config.get('trust_server_certificate', 'false'))).lower() == 'true',
        'windows_auth': os.getenv('MSSQL_WINDOWS_AUTH', str(file_config.get('windows_auth', 'false'))).lower() == 'true',
        'connection_timeout': int(os.getenv('MSSQL_TIMEOUT', file_config.get('connection_timeout', 30))),
    }
    
    # Add pool config if present
    if 'connection_pool' in file_config:
        config_dict['pool_config'] = ConnectionPoolConfig(**file_config['connection_pool'])
    
    # Add cache config if present
    if 'cache' in file_config:
        config_dict['cache_config'] = CacheConfig(**file_config['cache'])
    
    try:
        config = DatabaseConfig(**config_dict)
        logger.info(f"Database config loaded successfully for {config.server}/{config.database}")
        return config
    except Exception as e:
        logger.error(f"Invalid database configuration: {e}")
        raise ValueError(f"Invalid database configuration: {e}")
