# ADR 0003: Use Local SQLite And Runtime Directories As The Data Boundary

- Status: Accepted

## Context

StockPilot needs a practical boundary between source code and runtime state.
The project already distinguishes checked-in code from local runtime files such
as configuration, databases, and generated reports.

For both market-data and Fundamental Screener workflows, the codebase also
needs a stable local storage layer that can cache synchronized data, support
quality checks, and feed repeatable reads to apps, skills, and CLI flows.

## Decision

Local runtime directories and SQLite databases are the current persistence
boundary.

This means:

- runtime data lives outside the installed skill bundle
- source code and docs remain in the repository
- synchronized market and fundamental data are cached locally in SQLite
- repositories assemble stable domain snapshots from local storage
- apps and skills do not directly embed their own ad hoc storage models

For Fundamental Screener specifically:

- external providers feed normalized rows into sync jobs
- sync jobs write lineage-aware rows into SQLite
- repositories build `MarketSnapshot` objects from local data
- apps, CLI, and skills consume the snapshot instead of talking to providers
  directly

## Consequences

Positive:

- local development stays simple and reproducible
- apps and skills can reuse cached data instead of always refetching
- quality, freshness, and lineage checks have a natural storage boundary
- source control stays free of private runtime state

Trade-offs:

- SQLite schemas and migration discipline matter
- contributors must keep provider details out of UI code
- local caches can become stale and need explicit quality/status handling

## Alternatives Considered

### Keep All Data In Memory Only

Rejected.

- would make refreshes slower and less reproducible
- would weaken lineage and quality tracking
- would force repeated remote calls from UI or skill flows

### Query External Providers Directly From Apps

Rejected.

- would entangle presentation code with data acquisition concerns
- would bypass repository and quality boundaries

### Introduce A Remote Database First

Rejected for now.

- adds deployment and operational overhead that the current local workflow does
  not need
- does not match the current single-workspace execution model
