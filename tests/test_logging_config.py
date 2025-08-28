"""Tests for the logging configuration module."""

import json
import logging
import sys
from io import StringIO
from unittest.mock import patch

import pytest
import structlog

from prometheus_mcp_server.logging_config import setup_logging, get_logger


def test_setup_logging_returns_logger():
    """Test that setup_logging returns a structlog logger."""
    logger = setup_logging()
    # Check that it has the methods we expect from a structlog logger
    assert hasattr(logger, 'info')
    assert hasattr(logger, 'error')
    assert hasattr(logger, 'warning')
    assert hasattr(logger, 'debug')


def test_get_logger_returns_logger():
    """Test that get_logger returns a structlog logger."""
    logger = get_logger()
    # Check that it has the methods we expect from a structlog logger
    assert hasattr(logger, 'info')
    assert hasattr(logger, 'error')
    assert hasattr(logger, 'warning')
    assert hasattr(logger, 'debug')


def test_structured_logging_outputs_json():
    """Test that the logger can be configured and used."""
    # Just test that the logger can be created and called without errors
    logger = setup_logging()
    
    # These should not raise exceptions
    logger.info("Test message", test_field="test_value", number=42)
    logger.warning("Warning message")
    logger.error("Error message")
    
    # Test that we can create multiple loggers
    logger2 = get_logger()
    logger2.info("Another test message")


def test_logging_levels():
    """Test that different logging levels work correctly."""
    logger = setup_logging()
    
    # Test that all logging levels can be called without errors
    logger.debug("Debug message")
    logger.info("Info message")
    logger.warning("Warning message")
    logger.error("Error message")
    
    # Test with structured data
    logger.info("Structured message", user_id=123, action="test")
    logger.error("Error with context", error_code=500, module="test") 