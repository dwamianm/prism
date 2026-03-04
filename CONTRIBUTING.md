# Contributing to PRME

Thanks for your interest in contributing! PRME is an early-stage project and contributions of all kinds are welcome — bug reports, documentation improvements, feature ideas, and code.

## Getting Started

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

### Setup

```bash
git clone https://github.com/dwamianm/prism.git
cd prism

# Using uv (recommended)
uv sync --all-extras

# Or using pip
pip install -e ".[postgres,dev]"
```

### Running Tests

```bash
# All tests
pytest

# Specific test file
pytest tests/test_retrieval_scoring.py

# With verbose output
pytest -v
```

### Code Style

We use [Ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```bash
# Check
ruff check .
ruff format --check .

# Fix
ruff check --fix .
ruff format .
```

### Type Checking

```bash
mypy src/prme
```

## Making Changes

1. **Fork** the repository
2. **Create a branch** from `main` (`git checkout -b my-feature`)
3. **Make your changes** — write tests for new functionality
4. **Run the checks** — `pytest`, `ruff check`, `ruff format`
5. **Commit** with a clear message describing what and why
6. **Push** and open a **Pull Request**

### Commit Messages

Write clear, descriptive commit messages. No strict format enforced, but prefer:

```
Short summary (50 chars or less)

Longer explanation if needed. Wrap at 72 characters.
Explain *what* changed and *why*, not *how*.
```

### PR Guidelines

- Keep PRs focused — one feature or fix per PR
- Include tests for new functionality
- Update documentation if behavior changes
- Link to relevant issues if applicable

## Reporting Bugs

Open a [GitHub Issue](https://github.com/dwamianm/prism/issues) with:

- What you expected to happen
- What actually happened
- Steps to reproduce
- Python version and OS
- Relevant error output or logs

## Feature Requests

Open a [GitHub Issue](https://github.com/dwamianm/prism/issues) with the `enhancement` label. Describe the use case and why it would be valuable.

## Security

If you discover a security vulnerability, **do not** open a public issue. See [SECURITY.md](SECURITY.md) for responsible disclosure instructions.

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
