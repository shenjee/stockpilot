# ADR 0002: Reusable Domain Logic Lives In `packages/`

- Status: Accepted

## Context

StockPilot serves multiple delivery surfaces:

- Streamlit apps under `apps/`
- installable skill scripts under `skills/`
- CLI-style entry points inside reusable packages

Without a clear rule, sector rotation, company ranking, financial quality,
valuation, Chan analysis, and similar logic can be reimplemented in UI or skill
code. That would create inconsistent behavior and make tests harder to trust.

## Decision

Reusable business and analysis logic belongs in `packages/`.

The dependency direction is:

```text
apps -> packages
skills -> packages
packages -> provider adapters / repositories / local storage boundaries
```

This means:

- `packages/chantheory` owns the stable Chan analysis contract
- `packages/fundamentalscreener` owns screening calculations, repositories,
  sync, quality, and stable output contracts
- `apps/` act as presentation and orchestration adapters
- `skills/` act as runtime and workflow adapters

## Consequences

Positive:

- one implementation can serve UI, CLI, and skill use cases
- tests stay close to the domain logic that matters
- apps and skills become thinner and easier to evolve
- regression fixes land in one place instead of many copies

Trade-offs:

- package APIs need care because multiple delivery surfaces depend on them
- contributors must resist quick UI-local workarounds that bypass shared logic

## Alternatives Considered

### Put Logic In Streamlit Apps

Rejected.

- would couple algorithms to presentation concerns
- would make CLI and skill reuse harder
- would duplicate behavior across delivery surfaces

### Put Logic In Skill Scripts

Rejected.

- would make local app and CLI reuse awkward
- would blur the difference between shared domain logic and one runtime flavor

### Split Shared Logic Later

Rejected for the current project direction.

- waiting would increase duplication debt
- current docs and code already point to shared packages as the stable center
