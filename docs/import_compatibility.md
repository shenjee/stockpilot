# Python import compatibility — issue #184

Current base: `158173c` (includes #182 and the merged #183). Rebased on
2026-10-04 using `~/.venvs/czsc`; only the #184 implementation commit was replayed.
`git range-diff` confirmed the implementation patch was identical before the
verification-record update. The four old #183 conflict files are not in this PR.

## Post-rebase verification (2026-10-04)

All requested affected suites were rerun on the rebased branch:

| Suite | Tests | Result |
| --- | ---: | --- |
| `packages/marketdata/tests` | 185 | Pass; includes 11 import-identity tests / 29 probes |
| `skills/china-stock-analysis/tests` | 13 | Pass; includes 9 compatibility tests / 8 isolated standalone checks |
| `packages/fundamentalscreener/tests` | 342 | Pass; includes main's #182 coverage |
| `apps/fundamental-screener/tests` | 43 | Pass; includes main's #182 coverage |
| **Post-rebase rerun total** | **583** | **Pass** |

Short-name and canonical CLI `--help` output was also rechecked and matched.
The previous 1,466-test complete Python run and JS/TS results below remain
historical evidence against `94c8479`; they are not claimed as a new full run
against `158173c`. Unaffected suites and JS/TS were not rerun for this rebase.

## Failure before the change (original baseline `94c8479`)

`marketdata.provider_result.MarketDataResult` and
`packages.marketdata.provider_result.MarketDataResult` were different classes.
Passing the former's failed result to the latter namespace's default queue
retry predicate returned `False`. The two queue modules also created different
shared queues. This demonstrates a mixed-import defect; it does not imply that
all entry points were broken.

The original skill compatibility suite ran 9 tests with 1 failure:
`test_wrappers_fall_back_in_standalone_skill_layout`. Removing repository paths
from `sys.path` did not isolate an installed package or an editable finder.

## Implementation boundary

`packages/_import_aliases.py` owns one finder limited to the three short names
`marketdata`, `chantheory`, and `fundamentalscreener`. It resolves them and their
future child imports to `packages.*`, returning the canonical module object
without executing a second business-module body. The loader restores canonical
`__spec__` metadata so it agrees with `__package__` for relative imports. It also
supports `python -m fundamentalscreener.cli`.

All six package boundaries call this mechanism. When `packages/` is ahead of
the root, the short-name implementation boundary redirects before its guarded
imports/exports run. If only `packages/` is accessible, that boundary appends
its repository/install root to resolve the canonical namespace. Existing caller
path setup and public exports remain supported. Class `__module__` values use
the canonical `packages.*` names.

No business module, standalone copy, dependency declaration, or publication-copy
generator changed. `_standalone_marketdata` is outside the alias allowlist and
retains its own types and state. Chan Theory and Fundamental Screener import no
market-data package merely to set up aliases.

## Production entry points and smoke coverage

| Consumer | Actual imports | Verification |
| --- | --- | --- |
| Fundamental Screener CLI / sync console scripts | `packages.fundamentalscreener.cli:main` / `.sync:main` | Full CLI and sync Python tests; installed CLI help; canonical `sectors --format json` and short-name `screen --format json` parse successfully; short/canonical help output matches. |
| Chan Viewer Streamlit app | App inserts `packages/`; short `chantheory`, `marketdata`, and their children in app/services/UI modules | All 37 app tests, including app import with Streamlit stub; 122 Chan Theory tests. |
| Fundamental Screener Streamlit app | App inserts `packages/`; short `fundamentalscreener` and children in app/services | All 43 app tests after rebase, including import and `main()` smoke with recorded/stubbed UI and data services. |
| China stock analysis Skill | Flat provider/runtime/repository/service wrappers prefer `marketdata.*`; fallback `_standalone_marketdata.*` | Wrapper object identity; cold-wrapper editable and regular-install tests; all 8 standalone behavior checks in isolated processes. |
| T+0 desktop/backend | Canonical `packages.marketdata.*`, `packages.chantheory.*`, `packages.t0assistant.*` | Backend `service.py --help`; all 104 app/backend Python tests and 479 Node tests, including real authenticated local-service startup/shutdown and transport; 617 T+0 package tests. |

## Independent acceptance checks

### Runtime identity and installed layouts

`packages/marketdata/tests/test_import_compat.py` runs 11 test methods containing
29 fresh-interpreter probes:

- Three packages × two import orders × four layouts: repository root,
  `packages/` first, real editable installation, and regular wheel installation.
- Three packages loaded short-name first with only `packages/` initially on the
  path (no repository root or host site initialization).
- Two cold Skill-wrapper probes, one per installation mode.

Temporary installs use pip with `--no-deps --no-build-isolation --no-index` on a
source copy; the regular install is built as a wheel by pip. Each installed
probe uses `-I -S`, adds only its temporary installation's site directory, and
checks installation metadata, module origins, and permitted editable-finder
origins. The regular wheel must import from its installation, not its source.

The probes directly assert package/submodule and type identity, late nested
imports, parent attributes, canonical metadata, cross-namespace exception
catching, and shared queue object identity. A failed result from the opposite
namespace actually causes a second provider attempt and succeeds. A profile
hook counts business-module body executions to rule out double loading followed
by re-exporting. Standalone types remain distinct even when aliases are active.

### Standalone isolation

`skills/china-stock-analysis/tests/test_marketdata_compat.py` copies scripts and
the standalone test helper into a fresh temporary tree for each check. It runs
`python -I -S` from that tree, disabling environment paths and site/.pth startup.
The child verifies that no editable finder/module loaded, no shared market-data
package loaded, and every loaded standalone module file lies in the temporary
scripts directory. All assertions from the original eight standalone test
bodies are preserved; their AST assertion-call lists were compared during review.

## Original full local regression (2026-10-03, base `94c8479`)

These are the pre-rebase results; the latest rerun counts are listed above.
Python suites run separately to retain their existing discovery/import setup:

| Suite | Tests | Result |
| --- | ---: | --- |
| `packages/chantheory/tests` | 122 | Pass |
| `packages/fundamentalscreener/tests` | 337 | Pass |
| `packages/indicators/tests` | 10 | Pass |
| `packages/marketdata/tests` | 185 | Pass |
| `packages/t0assistant/tests` | 617 | Pass |
| `apps/chan-viewer/tests` | 37 | Pass |
| `apps/fundamental-screener/tests` | 41 | Pass |
| `apps/t0-assistant/tests` | 104 | Pass |
| `skills/china-stock-analysis/tests` | 13 | Pass |
| **Python total** | **1,466** | **Pass** |

The standalone child checks and the identity matrix above are exercised within
these totals, not added again as separate unittest test counts.

| JavaScript / TypeScript validation | Tests | Result |
| --- | ---: | --- |
| T+0 `npm test`: Node | 479 | Pass |
| T+0 `npm test`: React/Vitest | 5 | Pass |
| Chart spike `npm test` | 56 | Pass |
| Electron lifecycle/transport spike `npm test` | 56 | Pass |
| T+0 `npm run typecheck` | — | Pass |
| T+0 `npm run build` | — | Pass |
| Chart spike `tsc -p tsconfig.json` | 15 diagnostics | Existing baseline failure, unchanged |
| Chart spike `npm run build` | — | Pass |

Reproduction commands (from the repository root, unless using `npm --prefix`):

```bash
source ~/.venvs/czsc/bin/activate
for suite in packages/{chantheory,fundamentalscreener,indicators,marketdata,t0assistant}/tests \
             apps/{chan-viewer,fundamental-screener,t0-assistant}/tests \
             skills/china-stock-analysis/tests; do
    python -m unittest discover -s "$suite" -p 'test_*.py'
done
npm --prefix apps/t0-assistant test
npm --prefix apps/t0-assistant run typecheck
npm --prefix apps/t0-assistant run build
npm --prefix spikes/0005-t0-chart-engine-and-logical-time-axis test
npm --prefix spikes/0006-0007-electron-python test
spikes/0005-t0-chart-engine-and-logical-time-axis/node_modules/.bin/tsc \
    -p spikes/0005-t0-chart-engine-and-logical-time-axis/tsconfig.json
npm --prefix spikes/0005-t0-chart-engine-and-logical-time-axis run build
```

Environment notes and limits:

- Local-service tests initially failed under sandbox restrictions on loopback
  listening; they were rerun with loopback access. These are not waived tests.
- The historical transport spike requires its README-declared `aiohttp`, absent
  from the validated environment. It was installed in a temporary directory and
  supplied only to that spike through `PYTHONPATH`, without changing project
  dependencies or the validated virtual environment.
- The chart spike has 15 existing TypeScript diagnostics (chart option/time
  types, DOM element typing, and unused symbols). An isolated source export of
  baseline `94c8479` using the same compiler/dependencies produces byte-identical
  diagnostic output. These unrelated historical errors are recorded, not fixed
  by the Python import change. Its 56 runtime tests and Vite build pass.
- Streamlit smoke uses the existing UI stubs. No manual desktop/Streamlit visual
  acceptance, signed Electron packaging, or live remote-provider request was
  performed. Python 3.14.5/macOS was exercised; other OS/Python versions were not.
- Existing T+0 tests emit expected injected-error logging and SQLite resource
  warnings, but finish successfully. The alias-induced relative-import warning
  discovered during implementation was fixed before the final regression.
