# Contributing to TSM Backend

Thank you for your interest in contributing! This document provides guidelines and instructions for contributing.

## Code of Conduct

Be respectful, inclusive, and professional in all interactions.

## Getting Started

### Prerequisites

- Python 3.11 or higher
- Git
- A Cloudflare R2 account (optional, for testing cloud features)

### Development Setup

```bash
# Clone repository
git clone https://github.com/AnasSfar/tsm-backend.git
cd tsm-backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies with dev tools
pip install -r requirements.txt
pip install pytest pytest-cov black ruff mypy
```

### Configure Local Environment

```bash
# Copy template
cp .env.example .env

# Edit with your settings (leave R2 credentials empty for local testing)
vim .env  # or use your editor
```

## Development Workflow

### 1. Create Feature Branch

```bash
git checkout -b feature/your-feature-name
# or: git checkout -b fix/your-bug-fix
```

### 2. Make Changes

Follow these guidelines:

- **Code Style**: Use `black` for formatting and `ruff` for linting
- **Type Hints**: Add type hints to new functions
- **Comments**: Document complex logic
- **Logging**: Use Python's `logging` module (not print)

### 3. Write Tests

For new features:

```bash
# Create test file
touch collectors/your_module/tests/test_feature.py

# Write tests (example)
import unittest

class TestYourFeature(unittest.TestCase):
    def test_basic_functionality(self):
        # Test code here
        pass
```

Run tests:

```bash
# Run all tests
python -m pytest

# Run specific test file
python -m pytest collectors/apple_music/tests/test_http_config.py

# Run with coverage
pytest --cov=collectors --cov-report=html
```

### 4. Format Code

```bash
# Format with black
black collectors/ scripts/

# Check linting
ruff check --fix collectors/ scripts/

# Type check (optional)
mypy collectors/ scripts/
```

### 5. Commit Changes

```bash
# Commit with clear message
git commit -m "feat: add new feature"
git commit -m "fix: resolve bug in collector"
git commit -m "docs: improve documentation"
git commit -m "test: add unit tests"
```

Commit message types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `test`: Tests
- `refactor`: Code refactoring
- `perf`: Performance improvement
- `chore`: Maintenance

### 6. Push & Create PR

```bash
# Push branch
git push origin feature/your-feature-name

# Create Pull Request on GitHub
# - Clear title and description
# - Reference related issues
# - Describe changes and testing
```

## Pull Request Checklist

Before submitting a PR:

- [ ] Code passes formatting (`black`, `ruff`)
- [ ] Type hints added to new functions
- [ ] Unit tests written and passing
- [ ] No hardcoded secrets or credentials
- [ ] No unnecessary dependencies added
- [ ] Documentation updated if needed
- [ ] Commit history is clean
- [ ] Branch is up to date with `main`

## Testing

### Unit Tests

Located in `tests/` subdirectories:

```bash
# Example: Apple Music HTTP tests
python -m pytest collectors/apple_music/tests/

# With verbose output
pytest -v collectors/apple_music/tests/
```

### Manual Testing

For collectors that require external APIs:

```bash
# Test Apple Music collector
APPLE_MUSIC_COUNTRIES=fr python collectors/apple_music/ts_page.py

# Test Spotify collector (requires valid session)
python collectors/spotify/streams/update_streams.py

# Test export
python scripts/export_apple_music.py
```

### Environment-Specific Tests

```bash
# Test with custom timeout
APPLE_MUSIC_TIMEOUT=30 python -m pytest

# Test without R2 upload
UPLOAD_TO_R2=0 python collectors/apple_music/run_apple_music.py
```

## Key Areas for Contribution

### High Priority

1. **Spotify/Billboard Tests**: Add comprehensive unit tests (similar to Apple Music)
2. **Error Handling**: Improve error handling and recovery in collectors
3. **Documentation**: Expand API documentation and troubleshooting guides
4. **CI/CD**: Add GitHub Actions workflows for Spotify and Billboard

### Medium Priority

1. **Data Validation**: Add schema validation for exported JSON/CSV
2. **Performance**: Optimize bulk export operations
3. **Logging**: Standardize logging across all collectors
4. **Caching**: Implement smart caching to reduce API calls

### Nice to Have

1. **Docker**: Create Dockerfile for containerized deployment
2. **Monitoring**: Add health checks and metrics
3. **CLI**: Improve command-line interface with better options
4. **Web UI**: Basic dashboard for manual data collection

## Reporting Issues

### Bug Reports

Include:
- Python version and OS
- .env configuration (without secrets)
- Full error message and traceback
- Steps to reproduce
- Expected vs actual behavior

### Feature Requests

Include:
- Clear problem statement
- Proposed solution
- Use case and benefit
- Any API/data sources involved

## Security Considerations

⚠️ **CRITICAL**: Never commit secrets!

- R2 credentials → use .env (gitignored)
- Apple Music tokens → auto-cached, never commit
- API keys → use environment variables

If you accidentally commit secrets:

```bash
# Use git-filter-repo to clean history
git-filter-repo --invert-paths --path .env

# Force push (caution!)
git push --force
```

## Code Review

All PRs require review. Reviewers will check:

- Code quality and style
- Test coverage
- Security issues
- Documentation clarity
- API compatibility

Expect constructive feedback and iterative improvements.

## Documentation

Update docs for:
- New features
- Configuration options
- Script changes
- API modifications
- Bug fixes (if non-obvious)

Docs locations:
- README files in collector directories
- This CONTRIBUTING.md for workflow
- Inline code comments for complex logic
- [DEPLOYMENT_AUDIT.md](DEPLOYMENT_AUDIT.md) for deployment checklist

## Licensing

By contributing, you agree that your contributions will be licensed under the MIT License.

## Questions?

- Open an issue for questions
- Check existing issues before asking
- Search README and docs first

---

Thank you for contributing! 🙏
