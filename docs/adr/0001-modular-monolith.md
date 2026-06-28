# ADR 0001: Use A Modular Monolith As The Default Architecture

- Status: Accepted

## Context

StockPilot currently ships as one local codebase that combines reusable
packages, Streamlit apps, installable skill scripts, and local runtime data.
The project needs clear internal boundaries, but it does not yet need the
operational complexity of distributed services.

Contributors need an explicit statement of the default architecture so they do
not assume that every subsystem should become a separate deployable service.

## Decision

StockPilot adopts a modular monolith as the current default architecture.

That means:

- one source repository contains the main product code
- modules are separated by package and directory boundaries
- apps and skills consume shared domain packages instead of forking logic
- local runtime storage remains process-local rather than network-distributed
- microservices are not the default for current development

## Consequences

Positive:

- architecture stays simple to run locally and test in one workspace
- package boundaries still give the project room to grow
- code review focuses on dependency direction rather than service choreography
- domain logic can be shared across CLI, app, and skill flows

Trade-offs:

- teams must enforce module boundaries in code review
- a monorepo can drift into accidental coupling if apps or skills bypass shared
  packages
- future service extraction, if needed, should be intentional and justified by
  concrete runtime needs

## Alternatives Considered

### Microservices First

Rejected for now.

- adds deployment, networking, observability, and contract-versioning overhead
- does not match the current local execution model
- would force infrastructure choices before the domain boundaries are stable

### Single Flat Script Collection

Rejected.

- hides ownership boundaries
- encourages logic duplication across apps and skills
- makes contract reuse and testing harder
