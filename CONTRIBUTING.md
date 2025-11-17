# Contributing to Prometheus MCP Server

Thank you for your interest in contributing to Prometheus MCP Server! We welcome contributions from the community and are grateful for your support.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [How Can I Contribute?](#how-can-i-contribute)
  - [Reporting Bugs](#reporting-bugs)
  - [Suggesting Features](#suggesting-features)
  - [Submitting Pull Requests](#submitting-pull-requests)
- [Development Setup](#development-setup)
- [Coding Standards](#coding-standards)
- [Testing Guidelines](#testing-guidelines)
- [Pull Request Process](#pull-request-process)
- [Release and Versioning](#release-and-versioning)
- [Community and Support](#community-and-support)

## Code of Conduct

This project adheres to a code of conduct that all contributors are expected to follow. By participating, you are expected to uphold this code. Please be respectful, inclusive, and considerate in all interactions.

## How Can I Contribute?

### Reporting Bugs

Before creating bug reports, please check the [issue tracker](https://github.com/pab1it0/prometheus-mcp-server/issues) to avoid duplicates. When you create a bug report, include as many details as possible:

1. **Use the bug report template** - Fill in [the template](https://github.com/pab1it0/prometheus-mcp-server/issues/new?template=bug_report.yml)
2. **Use a clear and descriptive title** - Summarize the issue in the title
3. **Describe the exact steps to reproduce** - Be specific about what you did
4. **Provide specific examples** - Include code samples or configuration files
5. **Describe the behavior you observed** - Explain what actually happened
6. **Explain the expected behavior** - What you expected to happen instead
7. **Include screenshots** - If applicable, add screenshots to help explain your problem
8. **Specify your environment**:
   - OS version
   - Python version
   - Prometheus version
   - MCP client being used

### Suggesting Features

Feature suggestions are tracked as GitHub issues. When creating a feature suggestion:

1. **Use the feature request template** - Fill in [the template](https://github.com/pab1it0/prometheus-mcp-server/issues/new?template=feature_request.yml)
2. **Use a clear and descriptive title** - Summarize the feature in the title
3. **Provide a detailed description** - Explain the feature and its benefits
4. **Describe the current behavior** - If applicable, describe what currently happens
5. **Describe the proposed behavior** - Explain how the feature would work
6. **Explain why this would be useful** - Describe the use cases and benefits
7. **List alternatives considered** - If you've thought of other solutions, mention them

### Submitting Pull Requests

We actively welcome your pull requests! Here's how to contribute code:

1. **Fork the repository** and create your branch from `main`
2. **Make your changes** following our [coding standards](#coding-standards)
3. **Add tests** for any new functionality
4. **Ensure all tests pass** and maintain or improve code coverage
5. **Update documentation** if you've changed functionality
6. **Submit a pull request** with a clear description of your changes

## Development Setup

This project uses [`uv`](https://github.com/astral-sh/uv) for dependency management. Follow these steps to set up your development environment:

### Prerequisites

- Python 3.10 or higher
- A running Prometheus server (for testing)
- Git

### Installation

1. **Install uv**:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Clone your fork**:
   ```bash
   git clone https://github.com/YOUR_USERNAME/prometheus-mcp-server.git
   cd prometheus-mcp-server
   ```

3. **Create and activate a virtual environment**:
   ```bash
   uv venv
   source .venv/bin/activate  # On Unix/macOS
   .venv\Scripts\activate     # On Windows
   ```

4. **Install dependencies**:
   ```bash
   # Install the package in editable mode with dev dependencies
   uv pip install -e ".[dev]"
   ```

5. **Set up environment variables**:
   ```bash
   cp .env.template .env
   # Edit .env with your Prometheus URL and credentials
   ```

## Coding Standards

Please follow these guidelines when writing code:

### Python Style Guide

- Follow [PEP 8](https://peps.python.org/pep-0008/) style guide
- Use meaningful variable and function names
- Write docstrings for all public modules, functions, classes, and methods
- Keep functions focused and single-purpose
- Maximum line length: 100 characters (when practical)

### Code Organization

- Place new functionality in appropriate modules
- Keep related code together
- Avoid circular dependencies
- Use type hints where appropriate

### Documentation

- Update the README.md if you change functionality
- Add docstrings to new functions and classes
- Comment complex logic or non-obvious implementations
- Keep comments up-to-date with code changes

### Commit Messages

Write clear, concise commit messages:

- Use the present tense ("Add feature" not "Added feature")
- Use the imperative mood ("Move cursor to..." not "Moves cursor to...")
- Limit the first line to 72 characters or less
- Reference issues and pull requests when relevant
- For example:
  ```
  feat: add support for custom headers in Prometheus requests

  - Adds PROMETHEUS_CUSTOM_HEADERS environment variable
  - Updates documentation with usage examples
  - Includes tests for header validation

  Fixes #106
  ```

## Testing Guidelines

All contributions must include appropriate tests. We use `pytest` for testing.

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage report
pytest --cov=src --cov-report=term-missing

# Run specific test file
pytest tests/test_specific.py

# Run tests matching a pattern
pytest -k "test_pattern"
```

### Test Requirements

- **Write tests for new features** - All new functionality must have corresponding tests
- **Maintain code coverage** - Aim for 80%+ code coverage (enforced by CI)
- **Test edge cases** - Consider error conditions and boundary cases
- **Use meaningful test names** - Test names should describe what they're testing
- **Keep tests isolated** - Tests should not depend on each other
- **Mock external dependencies** - Use `pytest-mock` for mocking Prometheus API calls

### Test Structure

```python
def test_feature_description():
    """Test that feature does what it should."""
    # Arrange - Set up test conditions
    # Act - Execute the functionality being tested
    # Assert - Verify the results
```

## Pull Request Process

1. **Update your fork** with the latest changes from `main`:
   ```bash
   git fetch upstream
   git rebase upstream/main
   ```

2. **Create a feature branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```

3. **Make your changes** following the guidelines above

4. **Run tests locally**:
   ```bash
   pytest --cov=src --cov-report=term-missing
   ```

5. **Push to your fork**:
   ```bash
   git push origin feature/your-feature-name
   ```

6. **Create a Pull Request** with:
   - A clear title describing the change
   - A detailed description of what changed and why
   - References to related issues (e.g., "Fixes #123")
   - Screenshots or examples if applicable

7. **Address review feedback** - Be responsive to comments and suggestions

8. **Wait for CI/CD checks** - All automated checks must pass:
   - Tests must pass
   - Code coverage must meet minimum threshold (80%)
   - No security vulnerabilities detected

### Pull Request Checklist

Before submitting, ensure your PR:

- [ ] Follows the coding standards
- [ ] Includes tests for new functionality
- [ ] All tests pass locally
- [ ] Maintains or improves code coverage
- [ ] Updates documentation as needed
- [ ] Has a clear and descriptive title
- [ ] Includes a detailed description
- [ ] References any related issues

## Release and Versioning

**Important**: Releases and versioning are managed exclusively by repository administrators. Contributors should not:

- Modify version numbers in `pyproject.toml`
- Create release tags
- Update changelog entries for releases

The maintainers will handle:

- Version bumping according to [Semantic Versioning](https://semver.org/)
- Creating and publishing releases
- Updating changelogs
- Publishing to package registries
- Building and pushing Docker images

If you believe a release should be created, please open an issue to discuss it with the maintainers.

## Community and Support

### Getting Help

- **Questions**: Use the [question template](https://github.com/pab1it0/prometheus-mcp-server/issues/new?template=question.yml)
- **Discussions**: Check existing [issues](https://github.com/pab1it0/prometheus-mcp-server/issues) for similar questions
- **Documentation**: Review the [README](README.md) and [docs](docs/) folder

### Recognition

Contributors are recognized in:

- Commit history and pull request comments
- GitHub's contributor graph
- Release notes for significant contributions

Thank you for contributing to Prometheus MCP Server! Your efforts help make this project better for everyone.
