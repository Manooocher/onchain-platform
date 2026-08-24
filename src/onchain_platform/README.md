# Onchain Platform Core

This package contains the main application code, organized by capability (DOC-009).

## Package Structure

| Package | Responsibility | Details |
|---------|---------------|---------|
| `domain/` | Domain models & canonical schemas | [README](domain/README.md) |
| `acquisition/` | Blockchain data collection | providers, collector |
| `processing/` | Fact extraction & finality | normalizer, finality engine |
| `domain_management/` | Entity resolution & metadata | resolver, wallet, metadata |
| `analytics/` | Features, projections, outcomes | [README](analytics/README.md) |
| `intelligence/` | Risk analysis & insights | GoPlus client, risk rules |
| `strategy/` | Candidate ranking | [README](strategy/README.md) |
| `research/` | API & dashboard | [README](research/README.md) |
| `persistence/` | Database repositories | postgres + timescale |
| `transport/` | Event streams & state cache | Redis-backed |
| `platform/` | Config, logging, scheduler | settings, structlog, APScheduler |
| `main.py` | Composition root (exempt from contracts) | wiring only |

## Dependency Rules

This project enforces strict dependency boundaries via `import-linter` (DOC-011). See [DOC-011 Repository Structure](../../docs/011-RepositoryStructure.md) for the complete contract specification.

**Key Rules:**
- `domain/` imports nothing else in the repository
- Each capability may only import from lower layers
- Cross-cutting concerns (`persistence/`, `transport/`, `platform/`) never import capability packages
- `main.py` is the composition root — the only file allowed to see more than one capability (exempt from the contracts)

## Coding Standards

All code follows [DOC-013 Coding Standards](../../docs/013-CodingStandards.md):
- **Financial precision** — money is `Decimal`/`str`, never `float` (except `Feature.value` and `avg_block_time_seconds`)
- **Determinism discipline** — no wall-clock, no unseeded randomness, no `set` iteration on aggregation paths
- **Immutability** — frozen Pydantic schemas; facts append-only once finalized
- **Point-in-Time correctness** — derived values never use future data