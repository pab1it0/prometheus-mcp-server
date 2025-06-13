#!/usr/bin/env python
import sys
import dotenv
from prometheus_mcp_server.server import mcp, config
from prometheus_mcp_server.logging_config import setup_logging, get_logger

# Initialize structured logging
logger = setup_logging()

def setup_environment():
    if dotenv.load_dotenv():
        logger.info("Environment configuration loaded", source=".env file")
    else:
        logger.info("Environment configuration loaded", source="environment variables", note="No .env file found")

    if not config.url:
        logger.error(
            "Missing required configuration",
            error="PROMETHEUS_URL environment variable is not set",
            suggestion="Please set it to your Prometheus server URL",
            example="http://your-prometheus-server:9090"
        )
        return False
    
    # Determine authentication method
    auth_method = "none"
    if config.username and config.password:
        auth_method = "basic_auth"
    elif config.token:
        auth_method = "bearer_token"
    
    logger.info(
        "Prometheus configuration validated",
        server_url=config.url,
        authentication=auth_method,
        org_id=config.org_id if config.org_id else None
    )
    
    return True

def run_server():
    """Main entry point for the Prometheus MCP Server"""
    # Setup environment
    if not setup_environment():
        logger.error("Environment setup failed, exiting")
        sys.exit(1)
    
    logger.info("Starting Prometheus MCP Server", transport="stdio")
    
    # Run the server with the stdio transport
    mcp.run(transport="stdio")

if __name__ == "__main__":
    run_server()
