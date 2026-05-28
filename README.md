# cc-stuff — Claude Code Plugin Marketplace

A public collection of Claude Code plugins for developer workflows. Install any plugin directly into Claude Code to extend its capabilities.

## Available Plugins

| Plugin | Description | Version |
|--------|-------------|---------|
| [pr-review-toolkit](./pr-review-toolkit/) | Fetch active PR review comments, apply code fixes, and resolve threads automatically | 0.3.0 |

## Installing a Plugin

### Install from GitHub

```bash
cc plugin install https://github.com/SvetlovA/cc-stuff/tree/master/<plugin-name>
```

### Install locally (for development or testing)

```bash
git clone https://github.com/SvetlovA/cc-stuff.git
cc --plugin-dir ./cc-stuff/<plugin-name>
```

## Plugin Overview

### [pr-review-toolkit](./pr-review-toolkit/)

Addresses GitHub PR review comments end-to-end:

- Detects the current PR or lets you pick from a list
- Fetches **all active** inline threads and general comments (skips resolved and already-replied threads; outdated threads are included and labeled)
- Evaluates scope and recommends **address on the fly** (simple, few files) or **create a plan first** (complex, cross-file, architectural)
- On the fly: applies fixes and resolves threads in one pass, then prompts to commit
- Plan mode: delegates to `/planning:make` (optional dependency) to produce a structured plan ending with thread resolution and `git push`

**Invoke with:** `/pr-review-toolkit:address-review-comments [pr-number]`

**Requires:** `gh` CLI authenticated

---

## Contributing

Contributions welcome! To add a plugin:

1. Fork this repo
2. Create a new directory at the repo root: `my-plugin-name/`
3. Follow the [plugin structure](CLAUDE.md) — at minimum a `.claude-plugin/plugin.json` and at least one skill or component
4. Add a `README.md` for your plugin
5. Update the table above
6. Open a PR

See [CLAUDE.md](./CLAUDE.md) for conventions around skill writing, naming, and path portability.

## License

[MIT](./LICENSE)
