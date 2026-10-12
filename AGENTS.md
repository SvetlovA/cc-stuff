# cc-stuff — Claude Code and Codex Plugin Marketplace

This repository is a public collection of plugins for Claude Code and Codex. Each plugin lives in its own top-level directory and can be installed independently.

## Repository Layout

```
cc-stuff/
├── .claude-plugin/
│   └── marketplace.json    # Claude Code marketplace
├── .agents/plugins/
│   └── marketplace.json    # Codex marketplace
├── <plugin-name>/          # One directory per plugin
│   ├── .claude-plugin/plugin.json  # Claude Code manifest
│   ├── .codex-plugin/plugin.json   # Codex manifest, skills: "./skills/"
│   ├── skills/             # User-invoked or auto-triggered skills
│   │   └── <skill-name>/
│   │       ├── SKILL.md    # Required for each skill
│   │       └── references/ # Detailed docs loaded on demand
│   ├── agents/             # Specialized subagents (optional)
│   ├── hooks/              # Event hooks (optional)
│   └── README.md           # Plugin documentation
├── AGENTS.md               # This file
└── README.md               # Marketplace overview
```

## Marketplace Registry

Maintain `.claude-plugin/marketplace.json` for Claude Code and `.agents/plugins/marketplace.json` for Codex. Both registries must list the same plugins and point to the same directories. Codex also accepts the Claude registry as a legacy fallback. Never use `.Codex-plugin/`: paths are lowercase and case-sensitive.

Claude registry format:

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

**When adding a new plugin, add an entry to both registries** pointing to its directory. Codex entries use `source: {"source": "local", "path": "./plugin-name"}`, `policy: {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}`, and `category: "Productivity"`. Resolve paths from the repository root.

## Adding a New Plugin

1. Create a top-level directory with a kebab-case name (e.g. `git-workflow-tools`)
2. Add `.claude-plugin/plugin.json` and `.codex-plugin/plugin.json` with matching `name`, `version`, description, and publisher metadata. Declare `skills: "./skills/"` in the Codex manifest.
3. Create components in `skills/`, `agents/`, `hooks/` as needed
4. Add a `README.md` describing what the plugin does and how to install it
5. Add matching entries to both marketplace registries
6. Update the root `README.md` plugins table and document installation and invocation for both hosts
7. Run `python3 scripts/validate_plugins.py` and the host validation commands below

## Skill Writing Conventions

These conventions apply to all skills in this repo:

- **Description (frontmatter):** Use third-person with specific trigger phrases — `"This skill should be used when the user asks to 'create X', 'fix Y'..."`
- **Body:** Write in imperative/infinitive form (`To do X, run Y`) — never second person (`You should...`)
- **Length:** Keep `SKILL.md` lean (1,500–2,000 words). Move detailed reference material to `references/` files
- **Progressive disclosure:** `SKILL.md` covers the workflow; `references/` files contain detailed API docs, patterns, and examples
- **User-invoked skills:** Include `argument-hint` and `allowed-tools` for Claude slash commands; Codex uses `$skill-name` and its available tools. Keep shared instructions usable without Claude's Skill or AskUserQuestion tools. Store version information under `metadata`.
- **Host capabilities:** Resolve helper paths from the active skill location. Use host-native shell, file, question, and asynchronous-process tools; do not assume Claude background-task notifications exist in Codex.

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

Keep this documentation-only field in the Claude manifest; it is not a runtime dependency declaration and Claude validation may warn that it is ignored. Document the same optional integration in the plugin README for both hosts. Skills that use an optional dependency must detect at runtime whether the dependency's skills appear in the active session, and fall back gracefully (offer install instructions or an alternative path) when they do not.

## Path Portability

Never hardcode absolute paths inside hook commands or MCP server configs. Hook commands may use `${CLAUDE_PLUGIN_ROOT}` (also supplied by Codex for compatibility). Scripts should accept `${PLUGIN_ROOT}` first and fall back to `${CLAUDE_PLUGIN_ROOT}` or their own file location. Skill shell commands must resolve helpers from the active skill path because these environment variables are not guaranteed outside hooks. Codex hooks require trust review in the host before running.

## Testing a Plugin Locally

```bash
claude --plugin-dir ./<plugin-name>
claude plugin validate .
claude plugin validate ./<plugin-name>
codex plugin list --available --json
python3 scripts/validate_plugins.py
python3 -m unittest discover -s tests
```

Verify skills load by asking questions that match their trigger phrases, or running `/plugin-name:skill-name` in Claude Code or `$skill-name` in Codex. For host format references, see [Claude manifests](https://code.claude.com/docs/en/plugins-reference) and [Codex packaging](https://developers.openai.com/plugins/build/plugins).
