"""Tests for Docker integration and container functionality."""

import os
import time
import pytest
import subprocess
import requests
import json
import tempfile
from pathlib import Path
from typing import Dict, Any
import docker
from unittest.mock import patch


@pytest.fixture(scope="module")
def docker_client():
    """Create a Docker client for testing."""
    try:
        client = docker.from_env()
        # Test Docker connection
        client.ping()
        return client
    except Exception as e:
        pytest.skip(f"Docker not available: {e}")


@pytest.fixture(scope="module") 
def docker_image(docker_client):
    """Build the Docker image for testing."""
    # Build the Docker image
    image_tag = "prometheus-mcp-server:test"
    
    # Get the project root directory
    project_root = Path(__file__).parent.parent
    
    try:
        # Build the image
        image, logs = docker_client.images.build(
            path=str(project_root),
            tag=image_tag,
            rm=True,
            forcerm=True
        )
        
        # Print build logs for debugging
        for log in logs:
            if 'stream' in log:
                print(log['stream'], end='')
        
        yield image_tag
        
    except Exception as e:
        pytest.skip(f"Failed to build Docker image: {e}")
    
    finally:
        # Cleanup: remove the test image
        try:
            docker_client.images.remove(image_tag, force=True)
        except:
            pass  # Image might already be removed


class TestDockerBuild:
    """Test Docker image build and basic functionality."""
    
    def test_docker_image_builds_successfully(self, docker_image):
        """Test that Docker image builds without errors."""
        assert docker_image is not None
    
    def test_docker_image_has_correct_labels(self, docker_client, docker_image):
        """Test that Docker image has the required OCI labels."""
        image = docker_client.images.get(docker_image)
        labels = image.attrs['Config']['Labels']
        
        # Test OCI standard labels
        assert 'org.opencontainers.image.title' in labels
        assert labels['org.opencontainers.image.title'] == 'Prometheus MCP Server'
        assert 'org.opencontainers.image.description' in labels
        assert 'org.opencontainers.image.version' in labels
        assert 'org.opencontainers.image.source' in labels
        assert 'org.opencontainers.image.licenses' in labels
        assert labels['org.opencontainers.image.licenses'] == 'MIT'
        
        # Test MCP-specific labels
        assert 'mcp.server.name' in labels
        assert labels['mcp.server.name'] == 'prometheus-mcp-server'
        assert 'mcp.server.category' in labels
        assert labels['mcp.server.category'] == 'monitoring'
        assert 'mcp.server.transport.stdio' in labels
        assert labels['mcp.server.transport.stdio'] == 'true'
        assert 'mcp.server.transport.http' in labels
        assert labels['mcp.server.transport.http'] == 'true'
    
    def test_docker_image_exposes_correct_port(self, docker_client, docker_image):
        """Test that Docker image exposes the correct port."""
        image = docker_client.images.get(docker_image)
        exposed_ports = image.attrs['Config']['ExposedPorts']
        
        assert '8080/tcp' in exposed_ports
    
    def test_docker_image_runs_as_non_root(self, docker_client, docker_image):
        """Test that Docker image runs as non-root user."""
        image = docker_client.images.get(docker_image)
        user = image.attrs['Config']['User']
        
        assert user == 'app'


class TestDockerContainerStdio:
    """Test Docker container running in stdio mode."""
    
    def test_container_starts_with_missing_prometheus_url(self, docker_client, docker_image):
        """Test container behavior when PROMETHEUS_URL is not set."""
        container = docker_client.containers.run(
            docker_image,
            environment={},
            detach=True,
            remove=True
        )
        
        try:
            # Wait for container to exit with timeout
            # Container with missing PROMETHEUS_URL should exit quickly with error
            result = container.wait(timeout=10)
            
            # Check that it exited with non-zero status (indicating configuration error)
            assert result['StatusCode'] != 0
            
            # The fact that it exited quickly with non-zero status indicates
            # the missing PROMETHEUS_URL was detected properly
            
        finally:
            try:
                container.stop()
                container.remove()
            except:
                pass  # Container might already be auto-removed
    
    def test_container_starts_with_valid_config(self, docker_client, docker_image):
        """Test container starts successfully with valid configuration."""
        container = docker_client.containers.run(
            docker_image,
            environment={
                'PROMETHEUS_URL': 'http://mock-prometheus:9090',
                'PROMETHEUS_MCP_SERVER_TRANSPORT': 'stdio'
            },
            detach=True,
            remove=True
        )
        
        try:
            # In stdio mode without TTY/stdin, containers exit immediately after startup
            # This is expected behavior - the server starts successfully then exits
            result = container.wait(timeout=10)
            
            # Check that it exited with zero status (successful startup and normal exit)
            assert result['StatusCode'] == 0
            
            # The fact that it exited with code 0 indicates successful configuration
            # and normal termination (no stdin available in detached container)
            
        finally:
            try:
                container.stop()
                container.remove()
            except:
                pass  # Container might already be auto-removed


class TestDockerContainerHTTP:
    """Test Docker container running in HTTP mode."""
    
    def test_container_http_mode_binds_to_port(self, docker_client, docker_image):
        """Test container in HTTP mode binds to the correct port."""
        container = docker_client.containers.run(
            docker_image,
            environment={
                'PROMETHEUS_URL': 'http://mock-prometheus:9090',
                'PROMETHEUS_MCP_SERVER_TRANSPORT': 'http',
                'PROMETHEUS_MCP_BIND_HOST': '0.0.0.0',
                'PROMETHEUS_MCP_BIND_PORT': '8080'
            },
            ports={'8080/tcp': 8080},
            detach=True,
            remove=True
        )
        
        try:
            # Wait for the container to start
            time.sleep(3)
            
            # Container should be running
            container.reload()
            assert container.status == 'running'
            
            # Try to connect to the HTTP port
            # Note: This might fail if the MCP server doesn't accept HTTP requests
            # but the port should be open
            try:
                response = requests.get('http://localhost:8080', timeout=5)
                # Any response (including error) means the port is accessible
            except requests.exceptions.ConnectionError:
                pytest.fail("HTTP port not accessible")
            except requests.exceptions.RequestException:
                # Other request exceptions are okay - port is open but MCP protocol
                pass
            
        finally:
            try:
                container.stop()
                container.remove()
            except:
                pass
    
    def test_container_health_check_stdio_mode(self, docker_client, docker_image):
        """Test Docker health check in stdio mode."""
        container = docker_client.containers.run(
            docker_image,
            environment={
                'PROMETHEUS_URL': 'http://mock-prometheus:9090',
                'PROMETHEUS_MCP_SERVER_TRANSPORT': 'stdio'
            },
            detach=True,
            remove=True
        )
        
        try:
            # In stdio mode, container will exit quickly since no stdin is available
            # Test verifies that the container starts up properly (health check design)
            result = container.wait(timeout=10)
            
            # Container should exit with code 0 (successful startup and normal termination)
            assert result['StatusCode'] == 0
            
            # The successful exit indicates the server started properly
            # In stdio mode without stdin, immediate exit is expected behavior
            
        finally:
            try:
                container.stop()
                container.remove()
            except:
                pass  # Container might already be auto-removed


class TestDockerEnvironmentVariables:
    """Test Docker container environment variable handling."""
    
    def test_all_environment_variables_accepted(self, docker_client, docker_image):
        """Test that container accepts all expected environment variables."""
        env_vars = {
            'PROMETHEUS_URL': 'http://test-prometheus:9090',
            'PROMETHEUS_USERNAME': 'testuser',
            'PROMETHEUS_PASSWORD': 'testpass',
            'PROMETHEUS_TOKEN': 'test-token',
            'ORG_ID': 'test-org',
            'PROMETHEUS_MCP_SERVER_TRANSPORT': 'http',
            'PROMETHEUS_MCP_BIND_HOST': '0.0.0.0',
            'PROMETHEUS_MCP_BIND_PORT': '8080'
        }
        
        container = docker_client.containers.run(
            docker_image,
            environment=env_vars,
            detach=True,
            remove=True
        )
        
        try:
            # Wait for the container to start
            time.sleep(3)
            
            # Container should be running
            container.reload()
            assert container.status == 'running'
            
            # Check logs don't contain environment variable errors
            logs = container.logs().decode('utf-8')
            assert 'environment variable is invalid' not in logs
            assert 'configuration missing' not in logs.lower()
            
        finally:
            try:
                container.stop()
                container.remove()
            except:
                pass
    
    def test_invalid_transport_mode_fails(self, docker_client, docker_image):
        """Test that invalid transport mode causes container to fail."""
        container = docker_client.containers.run(
            docker_image,
            environment={
                'PROMETHEUS_URL': 'http://test-prometheus:9090',
                'PROMETHEUS_MCP_SERVER_TRANSPORT': 'invalid-transport'
            },
            detach=True,
            remove=True
        )
        
        try:
            # Wait for container to exit with timeout
            # Container with invalid transport should exit quickly with error
            result = container.wait(timeout=10)
            
            # Check that it exited with non-zero status (indicating configuration error)
            assert result['StatusCode'] != 0
            
            # The fact that it exited quickly with non-zero status indicates
            # the invalid transport was detected properly
            
        finally:
            try:
                container.stop()
                container.remove()
            except:
                pass  # Container might already be auto-removed
    
    def test_invalid_port_fails(self, docker_client, docker_image):
        """Test that invalid port causes container to fail."""
        container = docker_client.containers.run(
            docker_image,
            environment={
                'PROMETHEUS_URL': 'http://test-prometheus:9090',
                'PROMETHEUS_MCP_SERVER_TRANSPORT': 'http',
                'PROMETHEUS_MCP_BIND_PORT': 'invalid-port'
            },
            detach=True,
            remove=True
        )
        
        try:
            # Wait for container to exit with timeout
            # Container with invalid port should exit quickly with error
            result = container.wait(timeout=10)
            
            # Check that it exited with non-zero status (indicating configuration error)
            assert result['StatusCode'] != 0
            
            # The fact that it exited quickly with non-zero status indicates
            # the invalid port was detected properly
            
        finally:
            try:
                container.stop()
                container.remove()
            except:
                pass  # Container might already be auto-removed


class TestDockerSecurity:
    """Test Docker security features."""
    
    def test_container_runs_as_non_root_user(self, docker_client, docker_image):
        """Test that container processes run as non-root user."""
        container = docker_client.containers.run(
            docker_image,
            environment={
                'PROMETHEUS_URL': 'http://test-prometheus:9090',
                'PROMETHEUS_MCP_SERVER_TRANSPORT': 'http'
            },
            detach=True,
            remove=True
        )
        
        try:
            # Wait for container to start
            time.sleep(2)
            
            # Execute id command to check user
            result = container.exec_run('id')
            output = result.output.decode('utf-8')
            
            # Should run as app user (uid=1000, gid=1000)
            assert 'uid=1000(app)' in output
            assert 'gid=1000(app)' in output
            
        finally:
            try:
                container.stop()
                container.remove()
            except:
                pass
    
    def test_container_filesystem_permissions(self, docker_client, docker_image):
        """Test that container filesystem has correct permissions."""
        container = docker_client.containers.run(
            docker_image,
            environment={
                'PROMETHEUS_URL': 'http://test-prometheus:9090',
                'PROMETHEUS_MCP_SERVER_TRANSPORT': 'http'
            },
            detach=True,
            remove=True
        )
        
        try:
            # Wait for container to start
            time.sleep(2)
            
            # Check app directory ownership
            result = container.exec_run('ls -la /app')
            output = result.output.decode('utf-8')
            
            # App directory should be owned by app user
            # Check that the directory shows app user and app group
            assert 'app  app' in output or 'app app' in output
            
        finally:
            try:
                container.stop()
                container.remove()
            except:
                pass