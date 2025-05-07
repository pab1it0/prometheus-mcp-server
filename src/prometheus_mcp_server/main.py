#!/usr/bin/env python
import sys
import dotenv
import logging
from prometheus_mcp_server.server import mcp, config

# Configure logging to stderr
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger(__name__)

def setup_environment():
    if dotenv.load_dotenv():
        logger.info("Loaded environment variables from .env file")
    else:
        logger.info("No .env file found or could not load it - using environment variables")

    if not config.url:
        logger.error("ERROR: PROMETHEUS_URL environment variable is not set")
        logger.error("Please set it to your Prometheus server URL")
        logger.error("Example: http://your-prometheus-server:9090")
        return False
    
    logger.info(f"Prometheus configuration:")
    logger.info(f"  Server URL: {config.url}")
    
    if config.username and config.password:
        logger.info("Authentication: Using basic auth")
    elif config.token:
        logger.info("Authentication: Using bearer token")
    else:
        logger.info("Authentication: None (no credentials provided)")
    
    return True

def run_server():
    """Main entry point for the Prometheus MCP Server"""
    # Setup environment
    if not setup_environment():
        sys.exit(1)
    
    logger.info("\nStarting Prometheus MCP Server...")
    logger.info("Running server in standard mode...")
    
    # Run the server with the stdio transport
    mcp.run(transport="stdio")

if __name__ == "__main__":
    run_server()
