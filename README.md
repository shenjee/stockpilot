# Stock Pilot Skills

Stock Pilot Skills is a collection of Agent Skills for stock-focused workflows. The skills are intended to work with clients that support the `SKILL.md` skill format, including OpenClaw, Codex, and Claude.

The repository is organized as a skill collection. Each installable skill lives under `skills/<skill-name>/` and contains its own `SKILL.md`, scripts, references, and assets.

## Skills

| Skill | Description |
| --- | --- |
| `china-stock-daily-tracker` | Generates factual China A-share daily market reports for indexes, watchlists, and portfolios. |

## Repository Layout

```text
stockpilotskills/
|-- README.md
|-- docs/
|   |-- PRODUCT_DESIGN.md
|   `-- PRODUCT_DESIGN.zh.md
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
|-- config/
|-- db/
|-- reports/
`-- <target-skills-dir>/
```

This keeps the skill install immutable and keeps private state out of the source repository.

## Development

Edit skill code under `skills/<skill-name>/`. Keep product notes and long-form design documents under `docs/`.
