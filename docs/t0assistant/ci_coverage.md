# T+0 CI coverage and acceptance (#185)

The `.github/workflows/t0-assistant.yml` workflow is a T+0 and shared-core
regression gate, not a repository-wide green signal. Its four existing jobs
remain separate. Python uses 3.11 and Node uses 22 on `ubuntu-latest`.

## Required coverage matrix

All Python rows use `apps/t0-assistant/tests/run_required_python.py` from the
repository root. Directory rows discover `test_*.py`; individual-file rows
use that exact filename as the discovery pattern.

| Job | Required target | Verified test count |
| --- | --- | ---: |
| Python smoke | `packages/marketdata/tests` | 185 |
| Python smoke | `packages/t0assistant/tests` | 617 |
| Python smoke | `packages/chantheory/tests` | 122 |
| Python smoke | `packages/indicators/tests` | 10 |
| Python smoke | `apps/t0-assistant/tests/test_ci_gates.py` | 5 |
| Python smoke | `apps/t0-assistant/tests/test_service.py` (actual service lifecycle) | 26 |
| Renderer smoke | `tests/ci-gates.test.mjs` | 2 |
| Renderer smoke | `tests/chart-model.test.mjs` | 53 |
| Renderer smoke | `tests/chart-primitives-lc.test.mjs` | 11 |
| Renderer smoke | `tests/chart-snapshot-smoke.test.mjs` | 2 |
| Renderer smoke | `npm run test:app` / `app-session-viewport.vitest.tsx` | 5 |
| Renderer smoke | `npm run smoke:renderer` (TypeScript + Vite build) | Compiler/build checks |
| Electron smoke | `tests/python-service-host.test.mjs` | 13 |
| Electron smoke | `tests/backend-gateway.test.mjs` | 16 |
| Contract smoke | `apps/t0-assistant/tests/test_contracts.py` | 21 |
| Contract smoke | `tests/replay-contract.test.mjs` | 2 |
| Contract smoke | `tests/fake-safe-bridge.test.mjs` | 8 |
| Contract smoke | `tests/historical-snapshot-contract.test.mjs` | 2 |

Node filenames are relative to `apps/t0-assistant`. Counts are an acceptance
snapshot, not hardcoded expectations: future tests may increase them. Each
required target must discover and execute tests and return success.

## Discovery gate

- Python logs interpreter, platform, target, discovered count, run count, and
  skipped count. Zero discovery, import/discovery errors, failures, and
  entirely skipped suites return nonzero.
- Node runs each named file in a separate process using the built-in test
  harness and TAP reporter. Running the file directly avoids `node --test`
  counting an empty file itself as a passing test. The wrapper checks the
  reported test and pass counts for **each file**, and preserves child failures.
- React runs Vitest with `--passWithNoTests=false`, then checks its JSON total
  and per-file assertion counts. Empty/all-skipped targets cannot pass.
- The gate adds no known-failure skips or `continue-on-error`. Existing
  typecheck/build commands retain their own nonzero failure behavior.

Negative regression tests create missing, empty, all-skipped, and failing
targets. A passing Node file followed by an empty file must still fail. The
React negative test uses a nonexistent Vitest filter. Example manual probes:

```bash
source ~/.venvs/czsc/bin/activate
python apps/t0-assistant/tests/run_required_python.py apps/t0-assistant/tests test_does_not_exist.py
cd apps/t0-assistant
node tests/run-required-tests.mjs node tests/does-not-exist.test.mjs
node tests/run-required-tests.mjs vitest __issue_185_missing_test__
```

Each command must return nonzero; they are intentionally separate probes.

## Trigger surface

Both pull requests and pushes to `main` include:

- The T+0 app (including tests, schemas, fixtures, `package.json`, lockfile,
  TypeScript/Vite/Vitest configuration, and gate runners).
- All of `marketdata`, `t0assistant`, `chantheory`, and `indicators` under
  `packages/`, including package contracts and tests.
- `packages/__init__.py`, `packages/_import_aliases.py`, and the root `marketdata/` and `chantheory/`
  compatibility entry points.
- The 0008 spike support code and 0009 baseline fixtures consumed by the
  Chan Theory regression tests.
- `pyproject.toml`, the workflow itself, and `docs/t0assistant/`.

Any one of those paths starts all four jobs. The workflow regression test
checks representative single-file changes against both event path lists,
including each core package, contracts, dependency configuration, and fixtures.
It also retains the explicit chart-step file list. Path checks use Python
`fnmatch` for these exact-file and directory-prefix patterns; they are not an
implementation of GitHub's path-filter engine or proof of remote event delivery.

## Scope and closing policy

Not covered by this CI matrix: chan-viewer, fundamental-screener and its
independent core package, Skills, all other T+0 app Python/Node test files not
listed above, Electron GUI/physical-device acceptance, packaging/signing, and
release workflows. Local `npm test` is broader than the CI Node subset.

Issue #185 may close once this Must matrix and negative gates pass on remote
Ubuntu CI; it does
not wait for #184. Skill isolation fixes and the complete repository-local
regression belong to #184. This change introduces no Skill exceptions and
does not claim that CI was necessarily failing before the change.

## Reproduction and local verification

The starting checkout was `72ebd3e` (including the existing #184 import-identity
work). Reinspection confirmed that CI still selected the absent
`test_fake_service.py` and omitted the three core-package trigger paths.
On the validated local Python 3.14.5 environment, the old discovery command
returned 5 with zero tests. In a fresh Python 3.11.17 environment, that same
old command returned **0** with zero tests; the new runner returned **1** for
the same nonexistent pattern. The repair explicitly enforces nonzero
empty-test behavior across versions.

Before submission, the uncommitted #185 files were moved to a new branch from
the refreshed `origin/main` at `0999682`. By then #184 had independently merged
through PR #196. The new branch adds no separate #184 commits; its PR diff
contains only #185. The refreshed main tree matched the original local starting
tree, so the prior runtime results still apply; the strengthened workflow gate
was rechecked after migration.

The validated local environment passed all Python targets above. Node 22
also passed the broader `npm test` suite (51 Node files / 481 tests and 5 React
tests), TypeScript checking, and the production build. Local service tests
need permission to bind an ephemeral loopback port; initial sandbox-denied
runs were repeated with that permission and passed.

A temporary Python 3.11.17 environment installed only the base project
requirements (plus pip/setuptools/wheel for the existing wheel smoke tests).
All 986 Python tests in the matrix passed, with zero skips. The discovery,
empty-target, and trigger-path regression tests also passed under that actual
CI Python version. A second minimal environment containing only jsonschema
passed all 21 Python contract tests, matching that job's installation surface.
Node 22.23.3 passed the matrix above, including Electron's 29 tests with
`T0_PYTHON` pointing to Python 3.11.17. The original `npm test` entry point was
retained and passed 481 Node tests plus 5 React tests; the new runner clears
inherited Node test-reporting context for its independent child processes.

These runs used macOS arm64. Ubuntu GitHub Actions execution, remote PR/push
event delivery, and a fresh Linux `npm ci` remain unverified locally. Trigger
coverage was checked against both workflow event configurations. No dependency
or lockfile changes were needed, and no repository-wide green claim is made.
