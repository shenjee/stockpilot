# ADR 0004: Prefer Schema-First Analysis Contracts

- Status: Accepted

## Context

StockPilot exposes analysis results to different consumers:

- Streamlit apps
- CLI commands
- installable skill workflows
- tests and fixtures

Those consumers need stable field names and predictable payload shapes. The
current codebase already reflects this approach:

- `packages/chantheory` exposes stable structured outputs instead of raw engine
  objects
- `packages/fundamentalscreener/schema.py` defines explicit payload structures
- repository and frontend adapters preserve `snake_case` field names

Without a schema-first rule, internal engine or provider details could leak into
apps, skills, and persisted outputs.

## Decision

StockPilot treats stable schemas and domain contracts as the primary public
boundary between analysis logic and delivery layers.

This means:

- contracts are defined explicitly in project-owned structures
- field names remain stable and use `snake_case`
- apps and skills consume project contracts, not raw provider or engine objects
- storage and provider changes should be mapped into stable contracts before
  reaching UI or skill surfaces

## Consequences

Positive:

- UI and skill code remain insulated from engine churn
- fixtures and regression tests can validate contract stability directly
- documentation can refer to stable payloads instead of transient internals
- new providers can be integrated behind the same domain-facing schema

Trade-offs:

- contract evolution needs deliberate review
- adapters are required when upstream providers or engines differ from project
  schemas

## Alternatives Considered

### Expose Raw Engine Objects

Rejected.

- would leak unstable implementation details into higher layers
- would make tests and docs harder to keep stable

### Let Each App Define Its Own Payloads

Rejected.

- would fragment the public contract
- would increase duplication and drift between app, CLI, and skill outputs

### Use Provider Fields Directly End To End

Rejected.

- upstream providers can change fields and semantics
- provider-native contracts do not express the project's domain language or
  quality expectations clearly enough
