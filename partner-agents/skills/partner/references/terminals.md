# Terminals

`spawn` opens each partner in its own terminal tab. The tab runs the provider's normal interactive interface, so the user can read the debate as it happens and type at that agent directly at any moment.

## Nothing here is a fixed list either

Terminals are resolved the way providers are — three layers, none of which is a table of blessed names:

| Layer | What it supplies | Where it lives |
|-------|------------------|----------------|
| **Entry** | templates checked by hand | `TERMINALS` in `scripts/partner.py` |
| **Your config** | terminals you describe or correct | `~/.claude/partner-terminals.json`, `.partner/terminals.json` |
| **Discovery** | the terminal hosting this session, driven from its own `--help` | the environment and the binary itself |
| **Platform default** | whatever the system itself calls a terminal, so nothing depends on the three above succeeding | `cmd.exe`, `open -a Terminal`, `x-terminal-emulator`, `xdg-terminal-exec` |

**A terminal nobody here has heard of still gets used.** Every emulator announces itself in the environment — `TERM_PROGRAM`, `TERM`, or a marker of its own — so *what am I running in* is a question the machine answers. Whatever that names is then probed like an unknown agent CLI: find the flag that runs a command, the flag that sets the directory, the flag that sets a title, and build the `open` template from what was actually found. A flag that was not found is not passed.

**And if even that finds nothing, a spawn still opens a tab**, because the table ends with each platform's own default terminal. The manual-command path is the last resort, not the second one.

Discovery is bounded on purpose: only the host terminal is probed, and only when it is not already described, so the usual case costs no subprocesses at all. Results are cached against the binary, so the first unknown terminal costs one probe and every spawn after it costs none.

```bash
python partner.py terminals              # what can open a tab here, in order
python partner.py terminals --prefer kitty
```

The listing says, for every entry: whether it is usable here (and if not, what is missing), whether an agent in it **can be typed into** later, which layer it came from, and which one a spawn would pick right now. `--no-probe` skips discovery.

```
-> mytermx          available                          cannot be typed into   [discovered from your environment]
   orca             not running inside it (ORCA_WOR   can be typed into
   tmux             not running inside it (TMUX unse   can be typed into
   wezterm          wezterm not on PATH                can be typed into
   wt               available                          cannot be typed into
   iterm            macOS only                         cannot be typed into
```

That first row is a terminal this plugin has never heard of: named by `TERM_PROGRAM`, found on PATH, and driven from flags read out of its own help.

## What an entry looks like

Three keys carry all of it, and only the first is required:

| Key | What it is |
|-----|------------|
| `open` | the command that opens a tab running one script |
| `send` | the command that types a line into that tab — omit it if the terminal cannot be typed into |
| `handle` | where the open command reports an id, when `send` needs one |

Templates use the same substitution as a provider `--cmd`: the template is split into arguments **first**, then placeholders are replaced inside each argument. That is what keeps a path with spaces one argument, and stops a Windows backslash being read as an escape.

| Placeholder | Value |
|-------------|-------|
| `{bin}` | the resolved binary |
| `{run}` | the runner script path |
| `{shell}` | the command that runs the runner through a shell (`bash "…"` / `cmd /c "…"`) |
| `{cwd}` | the repo root |
| `{title}` | `partner:<id>` |
| `{text}` / `{text_nl}` | the line to type, plain or newline-terminated (`send` only) |
| `{handle}` | the id from `handle` or `list_handle` |

Detection is per entry, not a chain of ifs in code:

| Key | Meaning |
|-----|---------|
| `bin` | binary that must be on PATH (defaults to the first word of `open`) |
| `bin_env` | env var holding a path to the binary, when a host does not put it on PATH |
| `env` | any one of these env vars set → we are running **inside** this terminal |
| `path` | a path that must exist (an app bundle, say) |
| `platform` | `nt`, `darwin`, `linux` |
| `term` | strings matched against `$TERM` / `$TERM_PROGRAM` → this is the terminal on screen |
| `list` + `list_handle` / `list_titles` | how to enumerate live tabs, for recovering a handle by title and for corroborating liveness |

## Selection order

1. **`--terminal <name>`**, or `PARTNER_TERMINAL` — an explicit choice comes first. It is a preference, not a constraint: if that terminal turns out to be unusable, detection continues rather than failing the spawn.
   Discovery takes part in this ordering rather than acting as a fallback after it, because a user sitting in an unrecognised terminal should get the partner beside them, not in whichever other terminal happens to be installed.
2. **A host we are already inside** (`env` matched) — a multiplexer or a workspace app. A tab inside the window the user already has costs nothing, and inside a workspace app a detached OS window would strand the partner outside the workspace they are looking at.
3. **The terminal on screen** (`term` matched) — the partner appears where the user is actually looking.
4. **The table order** — terminals with a control CLI first, since those are also the ones that can be typed into; then whatever else can open a window.

Built-in entries, in table order: `orca`, `tmux`, `zellij`, `wezterm`, `kitty`, `ghostty`, `foot`, `rio`, `wt`, `cmd`, `iterm`, `apple-terminal`, `gnome-terminal`, `konsole`, `xfce4-terminal`, `terminator`, `alacritty`, `xterm`, then the platform defaults `x-terminal-emulator`, `xdg-terminal-exec` and `macos-open`. Any of them can be corrected or replaced by a user entry of the same name — which is the intended fix when a terminal changes its flags, rather than waiting for this plugin to catch up.

## Adding or fixing one

`~/.claude/partner-terminals.json`, or `.partner/terminals.json` for a single repo (repo wins):

```json
{
  "ghostty": {
    "bin": "ghostty",
    "term": ["ghostty"],
    "open": "{bin} +new-window -e bash {run}",
    "label": "Ghostty window"
  },
  "mux": {
    "env": ["MUX_SESSION"],
    "open": "{bin} split --title {title} --cwd {cwd} -- {shell}",
    "send": "{bin} type --pane {title} {text_nl}",
    "label": "mux pane"
  }
}
```

An entry that names an existing key replaces that built-in; a new key is added. Entries from the user's file are ranked ahead of the built-in table, because a terminal somebody described by hand is a deliberate choice.

## The runner-script indirection

Quoting a nested agent command through `wt.exe`, `osascript` and `gnome-terminal` is where cross-platform launchers normally break — each has its own rules, and `wt` additionally treats `;` as a command separator.

So the real command is never passed through the terminal at all. `spawn` writes it into a per-partner runner script:

```
.partner/<id>/run.cmd    # Windows
.partner/<id>/run.sh     # macOS / Linux
```

Every terminal then only has to run one plain file path with no arguments — which is also why a new entry is one line of template rather than an escaping exercise.

## Typing into a tab that has gone quiet

A tab agent whose CLI ended its turn cannot be reached from inside the system — nothing re-invokes it, and only Claude Code has a Stop hook. The recovery is to type into its tab the way the human would, and that works whatever is running in it.

`spawn` records which terminal owns each tab (`tab_kind`, `tab_handle`, `tab_title` on the roster entry), because the owning terminal is not recoverable after the fact. `nudge` then reads that entry's `send` template. A terminal with no `send` reports that it cannot be typed into and prints the command that restarts the agent instead — relay it.

When the handle was never recorded (an older roster, a recreated tab), `list_handle` recovers it by title. That is best-effort: some terminals let the running process rename its own tabs, in which case the handle recorded at spawn is the only reliable id.

The line that gets typed opens by saying it is an automated wake-up and **not** the human, and tells the agent not to claim the baton. Without that, an agent follows its briefing ("the human addressed me → claim") and takes write permission from whoever is actually working.

## When no terminal is available

This is now genuinely rare — it means the host terminal could not be identified *or* driven, no described terminal is installed, and the platform's own default terminal is absent too. Headless servers, SSH sessions without a display, and containers are where it happens. `spawn` still registers the partner and prints the exact command to run:

```
p2 (codex/gpt-5-codex/high, auto=edits) registered, but no terminal could be opened.
Open a tab yourself and run:
  bash "/repo/.partner/p2/run.sh"
```

Relay that command rather than reporting the spawn as failed — everything except the tab worked, and the partner starts participating the moment that command runs. `--no-tab` requests this deliberately, which is also the right choice when driving partners from a script or a CI job.

## Verifying

`terminals` says where partners will open before anything is spawned. `list` shows the method that was actually used per partner:

```
  p2       codex    gpt-5-codex   effort=high   auto=edits [running] Orca tab
```

An empty method column means the agent is registered but its runner has not been started.
