#!/usr/bin/env python

import logging
import sys
from typing import Any, Dict

import structlog


def setup_logging() -> structlog.BoundLogger:
    """Configure structured JSON logging for the MCP server.
    
    Returns:
        Configured structlog logger instance
    """
    # Configure structlog to use standard library logging
    structlog.configure(
        processors=[
            # Add timestamp to every log record
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            # Add structured context
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            # Convert to JSON
            structlog.processors.JSONRenderer()
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        context_class=dict,
        cache_logger_on_first_use=True,
    )
    
    # Configure standard library logging to output to stderr
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=logging.INFO,
    )
    
    # Create and return the logger
    logger = structlog.get_logger("prometheus_mcp_server")
    return logger


def get_logger() -> structlog.BoundLogger:
    """Get the configured logger instance.
    
    Returns:
        Configured structlog logger instance
    """
    return structlog.get_logger("prometheus_mcp_server") 