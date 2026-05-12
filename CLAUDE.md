# cc-stuff — Claude Code Plugin Marketplace

This repository is a public collection of Claude Code plugins. Each plugin lives in its own top-level directory and can be installed independently.

## Repository Layout

```
cc-stuff/
├── <plugin-name>/          # One directory per plugin
│   ├── .claude-plugin/
│   │   └── plugin.json     # Required: plugin manifest (name, version, description)
│   ├── skills/             # User-invoked or auto-triggered skills
│   │   └── <skill-name>/
│   │       ├── SKILL.md    # Required for each skill
│   │       └── references/ # Detailed docs loaded on demand
│   ├── agents/             # Specialized subagents (optional)
│   ├── hooks/              # Event hooks (optional)
│   └── README.md           # Plugin documentation
├── CLAUDE.md               # This file
└── README.md               # Marketplace overview
```

## Adding a New Plugin

1. Create a top-level directory with a kebab-case name (e.g. `git-workflow-tools`)
2. Add `.claude-plugin/plugin.json` with at minimum `name`, `version`, and `description`
3. Create components in `skills/`, `agents/`, `hooks/` as needed
4. Add a `README.md` describing what the plugin does and how to install it
5. Update the root `README.md` plugins table

## Skill Writing Conventions

These conventions apply to all skills in this repo:

- **Description (frontmatter):** Use third-person with specific trigger phrases — `"This skill should be used when the user asks to 'create X', 'fix Y'..."`
- **Body:** Write in imperative/infinitive form (`To do X, run Y`) — never second person (`You should...`)
- **Length:** Keep `SKILL.md` lean (1,500–2,000 words). Move detailed reference material to `references/` files
- **Progressive disclosure:** `SKILL.md` covers the workflow; `references/` files contain detailed API docs, patterns, and examples
- **User-invoked skills:** Include `argument-hint` and `allowed-tools` frontmatter when the skill is a slash command

## Plugin Naming

- Use kebab-case: `my-plugin-name`
- Be descriptive: prefer `pr-review-toolkit` over `prt`
- A plugin name should describe its domain, not a specific feature

## Path Portability

Never hardcode absolute paths inside hook commands or MCP server configs. Use `$CLAUDE_PLUGIN_ROOT` so the plugin works regardless of where it's installed.

## Testing a Plugin Locally

```bash
cc --plugin-dir /path/to/cc-stuff/<plugin-name>
```

Verify skills load by asking questions that match their trigger phrases, or running their slash command.
