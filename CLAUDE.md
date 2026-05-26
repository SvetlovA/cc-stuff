# cc-stuff — Claude Code Plugin Marketplace

This repository is a public collection of Claude Code plugins. Each plugin lives in its own top-level directory and can be installed independently.

## Repository Layout

```
cc-stuff/
├── .claude-plugin/
│   └── marketplace.json    # Marketplace registry — lists all plugins
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

## Marketplace Registry

`.claude-plugin/marketplace.json` at the repo root is what makes this repo installable as a marketplace. Claude Code reads it when a user installs from the GitHub URL.

Format:
```json
{
  "name": "SvetlovA-cc-stuff",
  "owner": { "name": "SvetlovA" },
  "plugins": [
    {
      "name": "plugin-name",
      "source": "./plugin-name",
      "description": "One-line description"
    }
  ]
}
```

**When adding a new plugin, always add an entry here** pointing to the plugin's directory.

## Adding a New Plugin

1. Create a top-level directory with a kebab-case name (e.g. `git-workflow-tools`)
2. Add `.claude-plugin/plugin.json` inside it with at minimum `name`, `version`, and `description`
3. Create components in `skills/`, `agents/`, `hooks/` as needed
4. Add a `README.md` describing what the plugin does and how to install it
5. Add an entry to `.claude-plugin/marketplace.json` at the repo root
6. Update the root `README.md` plugins table

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

## Optional Plugin Dependencies

When a plugin benefits from another plugin but does not require it, declare it in `plugin.json` under `optionalDependencies`:

```json
"optionalDependencies": [
  {
    "name": "plugin-name",
    "source": "https://github.com/owner/repo",
    "reason": "One sentence explaining which feature uses it and why"
  }
]
```

The runtime does not enforce this field — it is documentation. Skills that use an optional dependency must detect at runtime whether the dependency's skills appear in the active session, and fall back gracefully (offer install instructions or an alternative path) when they do not.

## Path Portability

Never hardcode absolute paths inside hook commands or MCP server configs. Use `$CLAUDE_PLUGIN_ROOT` so the plugin works regardless of where it's installed.

## Testing a Plugin Locally

```bash
cc --plugin-dir /path/to/cc-stuff/<plugin-name>
```

Verify skills load by asking questions that match their trigger phrases, or running their slash command.
