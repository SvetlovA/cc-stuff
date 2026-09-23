"""The hand-checked terminal table.

A terminal is described the same way a provider is: by data, in one place,
with the user able to add or replace an entry without touching this file.
There is no blessed set of terminals any more than there is a blessed set of
CLIs -- a terminal released this year, or a fork nobody here has heard of, is
nameable by whoever has it.

Each entry needs at most three things:

  open    the command that opens a tab running one script      (required)
  send    the command that types a line into that tab, if it can be
  handle  where the open command reports an id, when `send` needs one

Templates use the same substitution as a provider `--cmd`: split into
arguments first, then placeholders replaced inside each argument, so a path
with spaces stays one argument and a Windows path is never mangled by shell
quoting rules.

  {bin}   the resolved binary          {cwd}     the repo root
  {run}   the runner script path       {title}   partner:<id>
  {shell} the command that runs the runner script through a shell
  {text}  the line to type (send only) {handle}  the id from `handle`

Detection is per entry, not a hardcoded order of ifs: `env` (any of these set),
`bin` (on PATH), `path` (exists), `platform`. Two hints only affect order:
an entry whose `env` is set is a host we are already inside, and an entry
whose `term` matches $TERM / $TERM_PROGRAM is the terminal the user is
actually looking at. Otherwise the table order stands -- multiplexers first,
because a tab inside the window the user already has costs nothing; then
terminals with a control CLI, since those are also the ones that can be typed
into; then whatever else can open a window.
"""
from __future__ import annotations

from pathlib import Path


TERMINAL_FILES = [
    Path.home() / ".claude" / "partner-terminals.json",
    Path.home() / ".partner" / "terminals.json",
]

TERMINALS: dict[str, dict] = {
    # Orca manages its own tabs: inside it, a detached OS terminal would strand
    # the partner outside the workspace the user is looking at. Its env markers
    # are set for processes it launches, so they double as the detection.
    "orca": {
        "env": ["ORCA_WORKTREE_ID", "ORCA_TERMINAL_HANDLE", "ORCA_TAB_ID"],
        "bin": "orca",
        "bin_env": "ORCA_CODEX_LAUNCH_PREFLIGHT",
        "open": "{bin} terminal create --worktree path:{cwd} --title {title} "
                "--command {shell} --json",
        "handle": "json:result.terminal.handle",
        "send": "{bin} terminal send --terminal {handle} --text {text} --enter",
        "self_handle": "ORCA_TERMINAL_HANDLE",
        "list": "{bin} terminal list --json",
        "list_handle": "result.terminals[].handle@title",
        "list_titles": "result.terminals[].title",
        "label": "Orca tab",
    },
    "tmux": {
        "env": ["TMUX"], "bin": "tmux",
        "open": "{bin} new-window -n {title} -c {cwd} {shell}",
        "send": "{bin} send-keys -t {title} {text} Enter",
        "label": "tmux tab",
    },
    "zellij": {
        "env": ["ZELLIJ"], "bin": "zellij",
        "open": "{bin} run --name {title} --cwd {cwd} -- bash {run}",
        "label": "zellij pane",
    },
    "wezterm": {
        "bin": "wezterm",
        "open": "{bin} cli spawn --cwd {cwd} -- bash {run}",
        "handle": "stdout",
        "send": "{bin} cli send-text --pane-id {handle} --no-paste {text_nl}",
        "list": "{bin} cli list --format json",
        "list_handle": "[].pane_id@tab_title",
        "list_titles": "[].tab_title",
        "label": "WezTerm tab",
    },
    "kitty": {
        "env": ["KITTY_LISTEN_ON"], "bin": "kitty",
        "open": "{bin} @ launch --type=tab --tab-title {title} --cwd {cwd} "
                "bash {run}",
        "send": "{bin} @ send-text --match title:{title} {text_nl}",
        "label": "kitty tab",
    },
    "ghostty": {
        "bin": "ghostty", "term": ["ghostty", "xterm-ghostty"],
        "open": "{bin} +new-window -e bash {run}",
        "label": "Ghostty window",
    },
    "foot": {
        "bin": "footclient", "env": ["FOOT_SERVER"], "term": ["foot"],
        "open": "{bin} --working-directory={cwd} --title={title} bash {run}",
        "label": "foot window",
    },
    "rio": {
        "bin": "rio", "term": ["rio"],
        "open": "{bin} --working-dir {cwd} -e bash {run}",
        "label": "Rio window",
    },
    "wt": {
        "platform": "nt", "bin": "wt.exe", "term": ["Windows Terminal"],
        "open": "{bin} -w 0 nt --title {title} -d {cwd} cmd.exe /k {run}",
        "label": "Windows Terminal tab",
    },
    "cmd": {
        "platform": "nt", "bin": "cmd.exe",
        "open": "{bin} /c start {title} cmd.exe /k {run}",
        "label": "cmd window",
    },
    "iterm": {
        "platform": "darwin", "bin": "osascript",
        "path": "/Applications/iTerm.app",
        "open": '{bin} -e tell application "iTerm2" to tell current window to '
                'create tab with default profile command "bash {run}"',
        "label": "iTerm2 tab",
    },
    "apple-terminal": {
        "platform": "darwin", "bin": "osascript",
        "open": '{bin} -e tell application "Terminal" to do script "bash {run}" '
                '-e tell application "Terminal" to activate',
        "label": "Terminal.app tab",
    },
    "gnome-terminal": {
        "bin": "gnome-terminal",
        "open": "{bin} --tab --title={title} -- bash {run}",
        "label": "GNOME Terminal tab",
    },
    "konsole": {
        "bin": "konsole", "open": "{bin} --new-tab -e bash {run}",
        "label": "Konsole tab",
    },
    "xfce4-terminal": {
        "bin": "xfce4-terminal",
        "open": "{bin} --tab --title={title} -e {shell}",
        "label": "Xfce Terminal tab",
    },
    "terminator": {
        "bin": "terminator", "open": "{bin} -e {shell}",
        "label": "Terminator window",
    },
    "alacritty": {
        "bin": "alacritty", "open": "{bin} -e bash {run}",
        "label": "Alacritty window",
    },
    "xterm": {
        "bin": "xterm", "open": "{bin} -T {title} -e bash {run}",
        "label": "xterm window",
    },
    # Last resort, one per platform: whatever the system itself considers "a
    # terminal", so an unrecognised environment still gets a tab instead of a
    # manual command. These are the entries that make the feature not depend on
    # this table being complete.
    "x-terminal-emulator": {
        "bin": "x-terminal-emulator",       # Debian/Ubuntu alternatives link
        "open": "{bin} -e bash {run}",
        "label": "the system default terminal",
    },
    "xdg-terminal-exec": {
        "bin": "xdg-terminal-exec",         # freedesktop's terminal resolver
        "open": "{bin} bash {run}",
        "label": "the desktop's default terminal",
    },
    "macos-open": {
        "platform": "darwin", "bin": "open",
        "open": "{bin} -a Terminal {run}",
        "label": "Terminal.app (via open)",
    },
}
