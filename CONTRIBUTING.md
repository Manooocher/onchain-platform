# Contributing to Onchain Platform

Thank you for your interest in contributing! This document outlines how to contribute code, report issues, and keep the repository healthy.

## Code of Conduct

By participating you agree to maintain a respectful and inclusive environment.

## How to Contribute

### Reporting Bugs
1. Check existing issues to avoid duplicates.
2. Use the bug report template.
3. Include: a clear description, steps to reproduce, expected vs actual behavior, environment details, and relevant logs.

### Suggesting Features
1. Open a discussion issue first.
2. Explain the use case and a proposed solution (optional).
3. Wait for feedback before implementing.

### Submitting Code
1. Fork the repository.
2. Create a feature branch (`feature/my-feature`).
3. Make your changes (follow [DOC-013](docs/013-CodingStandards.md)).
4. Add tests.
5. Ensure all quality gates pass.
6. Open a pull request.

## Development Setup

See [DEVELOPMENT.md](docs/DEVELOPMENT.md) for the full setup. Quick start:

```bash
git clone git@github.com:Manooocher/onchain-platform.git
cd onchain-platform
uv sync
cp .env.example .env
docker compose up -d
make migrate
make test
```

## Code Standards

### Tools
- **Formatter / linter:** Ruff
- **Type checker:** mypy (strict)
- **Import contracts:** import-linter

Run all checks:
```bash
make lint
make typecheck
make import-check
```

### Principles (DOC-013)
1. **Financial precision** — `Decimal` for money; `str` on the wire; never `float` for amounts/prices.
2. **Determinism** — no wall-clock in capabilities, no unseeded randomness, no `set` iteration on aggregation paths.
3. **Immutability** — frozen Pydantic schemas; facts append-only once `FINALIZED`.
4. **Point-in-time** — derived values never use future data.
5. **Documentation** — docstrings on all public APIs.

### Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

[optional body]

Closes #123
```

**Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`.
**Scopes:** `acquisition`, `processing`, `analytics`, `intelligence`, `strategy`, `research`, `persistence`, `transport`, `platform`, `domain`.

## Testing

- **Unit tests** — every public function / schema.
- **Integration tests** — every repository function and API endpoint (real DB).
- **Replay tests** — determinism of historical processing (`make test-replay`).
- **Schema tests** — property-based canonical schema checks.

```bash
make test
make test-replay
```

## Pull Request Process

Before submitting:
- [ ] Code follows the style guide (`make lint`)
- [ ] Type checking passes (`make typecheck`)
- [ ] Import contracts kept (`make import-check` — 8/8)
- [ ] Tests added for new functionality; all pass
- [ ] Documentation updated
- [ ] Commit messages follow the convention

Review:
1. Automated checks must pass.
2. At least one maintainer approval required.
3. All review comments addressed.

## Architecture Decisions

Before significant changes:
1. Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
2. Check [docs/adr/](docs/adr/) for existing decisions.
3. Open a discussion issue for new architectural changes.
4. Document accepted decisions as new ADRs.

## License

By contributing, you agree that your contributions are licensed under the MIT License (see [LICENSE](LICENSE)).