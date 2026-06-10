# Stock Pilot Skills

Stock Pilot Skills is a collection of Agent Skills for stock-focused workflows. The skills are intended to work with clients that support the `SKILL.md` skill format, including OpenClaw, Codex, and Claude.

The repository is organized as a skill collection. Each installable skill lives under `skills/<skill-name>/` and contains its own `SKILL.md`, scripts, references, and assets.

## Skills

| Skill | Description |
| --- | --- |
| `china-stock-daily-tracker` | Generates factual China A-share daily market reports for indexes, watchlists, and portfolios. |

## Phase 2: Chan Structure Analysis

Phase 2 adds a project-owned `chantheory` adapter layer for Chan Theory structure analysis. It uses `czsc` as the underlying engine, but skills, agents, and UIs consume the stable project schema instead of `czsc` native objects.

Current Phase 2 boundaries:

- `chantheory` normalizes OHLCV input, calls `czsc`, maps results into a project schema, and emits `plot_primitives`, summaries, and warnings.
- Visual structure output is the primary output; text is only a short supporting summary.
- Candidate buy/sell points are structure-only candidates. They are not standalone trading instructions; signal synthesis belongs to a later phase.
- The Streamlit app under `apps/chan-streamlit/` is a debug and validation tool, not the long-term product UI.

Related docs:

- [docs/product_design.md](docs/product_design.md)
- [docs/product_design.zh.md](docs/product_design.zh.md)
- [docs/chan_theory_v0.1.md](docs/chan_theory_v0.1.md)
- [docs/phase2_tasks.md](docs/phase2_tasks.md)

## Version History

See [CHANGELOG.md](CHANGELOG.md).

## Repository Layout

```text
stockpilotskills/
|-- README.md
|-- CHANGELOG.md
|-- docs/
|   |-- product_design.md
|   |-- product_design.zh.md
|   |-- phase2_tasks.md
|   `-- chan_theory_v0.1.md
|-- packages/
|   `-- chantheory/
|       |-- normalize.py
|       |-- adapters.py
|       |-- schema.py
|       |-- plotting.py
|       |-- describe.py
|       `-- config.py
|-- apps/
|   `-- chan-streamlit/
|       |-- app.py
|       |-- README.md
|       `-- sample_data/
`-- skills/
    `-- china-stock-daily-tracker/
        |-- SKILL.md
        |-- scripts/
        |-- references/
        `-- assets/
```

## Installation

Install one skill by copying its directory into the target client's skills directory:

```bash
cp -R skills/china-stock-daily-tracker <target-skills-dir>/
```

Expected installed layout:

```text
<target-skills-dir>/
`-- china-stock-daily-tracker/
    |-- SKILL.md
    |-- scripts/
    |-- references/
    `-- assets/
```

The target skills directory is defined by the client or by the install command parameters. For example, a client may install to a user-level skills directory, a workspace-level skills directory, or a custom path selected by the user.

Runtime data should stay outside the installed skill directory:

```text
<workspace-or-project-dir>/
`-- stockpilot/
    |-- config/
    |-- db/
    `-- reports/
```

The runtime directory is configurable. By default, `runtime_dir` is `stockpilot`.
Directory fields such as `config_dir`, `db_dir`, and `reports_dir` are resolved
under `runtime_dir` unless an absolute path is provided:

```json
{
  "workspace": ".",
  "runtime_dir": "stockpilot",
  "config_dir": "config",
  "db_dir": "db",
  "reports_dir": "reports",
  "data_source": {
    "provider": "tencent"
  }
}
```

Market data providers are isolated under the skill scripts. The default provider
is Tencent Finance; future providers should implement the same provider contract
instead of adding HTTP request code to `generate_report.py`.

Installed skills live in the target client's skills directory, separate from
runtime data:

```text
<target-skills-dir>/
`-- china-stock-daily-tracker/
    |-- SKILL.md
    |-- scripts/
    |-- references/
    `-- assets/
```

This keeps the skill install immutable and keeps private state out of the source repository.

## Development

Edit installable skill code under `skills/<skill-name>/`. Keep project-owned reusable packages under `packages/`, local validation apps under `apps/`, and product notes or long-form design documents under `docs/`.
