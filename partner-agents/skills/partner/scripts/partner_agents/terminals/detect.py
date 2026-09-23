"""Finding the terminals this machine has, including ones not in the catalog.

The table in catalog.py is an accelerator, exactly like RECIPES for providers: it
holds entries whose flags were checked by hand. It is not the set of
terminals that work, because that set is not knowable from here -- so there
are two more layers, and they are the same two providers have.

  * The terminal hosting this process announces itself in the environment.
    Every emulator sets something -- TERM_PROGRAM, TERM, or a marker of its
    own -- so "what am I running in" is a question the machine answers.
  * Whatever that names is then driven from its own `--help`, the same way an
    unknown agent CLI is: find the flag that runs a command, the flag that
    sets the directory, the flag that sets a title, and build the `open`
    template out of what was actually found.

And because a spawn must not fail just because nothing was recognised, the
table ends with each platform's own idea of "the default terminal" --
`cmd.exe` on Windows, `open`/AppleScript on macOS, and the two standard
indirections on Linux, which distributions point at whatever the user chose.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

from ..state import cache_dir, state_dir
from ..terminals.catalog import TERMINAL_FILES, TERMINALS
from ..util import run_command


# Environment markers that name the terminal hosting this process. These are
# used to *find and probe* a binary, never as a list of what is allowed --
# a name here with no built-in entry is discovered, not rejected.
HOST_ENV_HINTS: list[tuple[str, str]] = [
    ("WT_SESSION", "wt"),
    ("KITTY_WINDOW_ID", "kitty"),
    ("KITTY_LISTEN_ON", "kitty"),
    ("WEZTERM_PANE", "wezterm"),
    ("WEZTERM_EXECUTABLE", "wezterm"),
    ("ALACRITTY_WINDOW_ID", "alacritty"),
    ("ALACRITTY_SOCKET", "alacritty"),
    ("GHOSTTY_RESOURCES_DIR", "ghostty"),
    ("GHOSTTY_BIN_DIR", "ghostty"),
    ("KONSOLE_VERSION", "konsole"),
    ("FOOT_SERVER", "footclient"),
    ("VTE_VERSION", "gnome-terminal"),
    ("TERMINATOR_UUID", "terminator"),
    ("TILIX_ID", "tilix"),
    ("TMUX", "tmux"),
    ("ZELLIJ", "zellij"),
]

# What a terminal's own help calls the flags we need. Order is preference, and
# anything not found is simply not passed -- the same conservatism the provider
# probe uses, for the same reason: a guessed flag opens a tab that prints a
# usage error and closes.
TERM_EXEC_FLAGS = ["-e", "--command", "--", "-x"]

TERM_CWD_FLAGS = ["--working-directory", "--cwd", "--directory", "--workdir"]

TERM_TITLE_FLAGS = ["--title", "--tab-title", "-T"]

# Phrases that mean "this binary is a terminal", read from its help.
TERM_HELP_SIGNALS = ("terminal emulator", "terminal for", "a terminal",
                     "terminal multiplexer", "pseudo-terminal", "pty",
                     "opens a new terminal", "terminal window")


def _term_name_from_env() -> list[str]:
    """Candidate binary names for the terminal hosting this process."""
    names: list[str] = []
    for var, name in HOST_ENV_HINTS:
        if os.environ.get(var):
            names.append(name)
    for var in ("TERM_PROGRAM", "TERMINAL_EMULATOR", "TERM"):
        raw = (os.environ.get(var) or "").strip().lower()
        if not raw or raw in ("dumb", "linux", "cygwin", "unknown"):
            continue
        # "iTerm.app" -> iterm, "xterm-ghostty" -> ghostty, "Apple_Terminal"
        # -> apple terminal, "JetBrains-JediTerm" -> jetbrains
        raw = re.sub(r"\.app$", "", raw)
        parts = [p for p in re.split(r"[-_. ]+", raw)
                 if p and p not in ("256color", "color", "direct", "truecolor",
                                    "xterm", "screen", "vt100", "vt220")]
        if parts:
            names.append("".join(parts) if len(parts) == 1 else parts[-1])
            names.append("-".join(parts))
    seen, out = set(), []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def probe_terminal(name: str, timeout: int = 15,
                   refresh: bool = False) -> dict | None:
    """Derive an `open` template for a terminal from its own --help.

    Returns an entry in the same shape as a table one, or None when the binary
    is missing, does not look like a terminal, or offers no way to hand it a
    command -- the one thing that cannot be worked around.
    """
    binary = shutil.which(name) or (shutil.which(name + ".exe")
                                    if os.name == "nt" else None)
    if not binary:
        return None
    try:
        st = Path(binary).stat()
        key = f"{binary}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        key = binary
    cf = cache_dir() / ("term-" + re.sub(r"[^a-z0-9]+", "_", name.lower()) + ".json")
    if not refresh and cf.exists():
        try:
            hit = json.loads(cf.read_text(encoding="utf-8"))
            if hit.get("key") == key:
                return hit.get("entry") or None
        except (OSError, ValueError):
            pass

    rc, help_text = run_command([binary, "--help"], timeout)
    if len(help_text) < 40:
        rc, help_text = run_command([binary, "-h"], timeout)
    low = help_text.lower()
    entry = None
    if help_text and (any(s in low for s in TERM_HELP_SIGNALS)
                      or any(f in help_text for f in TERM_EXEC_FLAGS)):
        exec_flag = next((f for f in TERM_EXEC_FLAGS if f in help_text), None)
        if exec_flag:
            cwd_flag = next((f for f in TERM_CWD_FLAGS if f in help_text), None)
            title_flag = next((f for f in TERM_TITLE_FLAGS if f in help_text), None)
            parts = ["{bin}"]
            if cwd_flag:
                parts += [cwd_flag, "{cwd}"]
            if title_flag:
                parts += [title_flag, "{title}"]
            parts += ([exec_flag] if exec_flag != "--" else ["--"])
            parts += ["bash", "{run}"]
            entry = {"bin": name, "open": " ".join(parts),
                     "label": f"{name} window", "kind_source": "discovered",
                     "term": [name]}
    try:
        cf.parent.mkdir(parents=True, exist_ok=True)
        cf.write_text(json.dumps({"key": key, "entry": entry}), encoding="utf-8")
    except OSError:
        pass
    return entry


def discover_terminals(known: dict) -> dict:
    """Entries for terminals on this machine that `known` does not cover.

    Only the host terminal is probed, and only when it is not already
    described: that is the one case where being unrecognised actually costs the
    user something (a partner opening somewhere other than where they are
    working), and it keeps this to at most one or two subprocesses.
    """
    out: dict = {}
    for name in _term_name_from_env():
        if name in known or name in out:
            continue
        entry = probe_terminal(name)
        if entry:
            out[name] = {**entry, "source": "discovered from your environment"}
    return out


def load_terminals(discover: bool = False) -> dict:
    """Every terminal entry known here, in three layers.

    The user's file, then the built-in table, then -- when asked -- whatever
    the environment says is hosting this process and can be driven from its
    own --help. Same shape and the same precedence as `partner-providers.json`:
    an entry in the user's file replaces the built-in of that name, a new name
    is simply added, and repo-level beats home-level. Between them, "which
    terminals exist" is never this file's decision -- and the table still ends
    with a platform default, so an unrecognised environment gets a tab rather
    than an apology.
    """
    out = {k: dict(v) for k, v in TERMINALS.items()}
    files = list(TERMINAL_FILES)
    try:
        files.append(state_dir() / "terminals.json")
    except OSError:
        pass
    user: dict = {}
    for f in files:
        try:
            if f.is_file():
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for k, v in data.items():
                        if isinstance(v, dict) and v.get("open"):
                            user[k] = {**v, "source": str(f)}
        except (OSError, ValueError):
            continue
    # The user's entries lead: a terminal somebody described by hand is a
    # deliberate choice and should win over this table's detection order.
    merged = {k: {**out.get(k, {}), **v} for k, v in user.items()}
    for k, v in out.items():
        merged.setdefault(k, v)
    if discover:
        for k, v in discover_terminals(merged).items():
            merged.setdefault(k, v)
    return merged


def own_tab() -> dict:
    """The tab this process runs in, when its terminal says which one.

    A terminal whose entry names a `self_handle` env var hands every process it
    runs the id of its own tab. That is how the agent that started the session
    -- which no `spawn` ever opened a tab for -- becomes wakeable exactly like
    the partners it spawned.
    """
    for name, spec in load_terminals().items():
        handle = os.environ.get(spec.get("self_handle") or "", "").strip()
        if handle and spec.get("send"):
            return {"kind": name, "handle": handle}
    return {}


def terminal_bin(spec: dict) -> str | None:
    """The binary this entry drives, if it is here at all."""
    name = spec.get("bin") or (spec["open"].split() or [""])[0]
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt" and not name.lower().endswith(".exe"):
        found = shutil.which(name + ".exe")
        if found:
            return found
    # Some hosts point at their own binary through the environment rather than
    # putting it on an agent's PATH.
    alt = os.environ.get(spec.get("bin_env") or "", "")
    if alt and Path(alt).exists():
        return alt
    return None


def terminal_available(spec: dict) -> tuple[bool, str]:
    """Can this entry open a tab here, and if not, what is missing."""
    plat = spec.get("platform")
    if plat == "nt" and os.name != "nt":
        return False, "Windows only"
    if plat == "darwin" and sys.platform != "darwin":
        return False, "macOS only"
    if plat in ("linux", "posix") and os.name == "nt":
        return False, "POSIX only"
    env = spec.get("env") or []
    if env and not any(os.environ.get(e) for e in env):
        return False, f"not running inside it ({' / '.join(env)} unset)"
    p = spec.get("path")
    if p and not Path(p).exists():
        return False, f"{p} not present"
    if not terminal_bin(spec):
        return False, f"{spec.get('bin') or 'binary'} not on PATH"
    return True, "available"


def terminal_order(prefer: str | None = None,
                   discover: bool = False) -> list[tuple[str, dict]]:
    """Entries in the order they should be tried.

    `prefer` -- `--terminal <name>`, or PARTNER_TERMINAL -- moves one to the
    front. Nothing is removed by it: a preference that turns out not to be
    usable falls through to detection rather than failing the spawn.
    """
    specs = load_terminals(discover)
    want = prefer or os.environ.get("PARTNER_TERMINAL") or ""
    here = " ".join(os.environ.get(k, "") for k in
                    ("TERM_PROGRAM", "TERM", "TERMINAL_EMULATOR")).lower()

    def rank(item: tuple[str, dict]) -> tuple[int, int, int]:
        name, spec = item
        inside = any(os.environ.get(e) for e in (spec.get("env") or []))
        looks = any(h.lower() in here for h in (spec.get("term") or []))
        # Asked for > a host we are inside > the terminal on screen > the table.
        return (0 if name == want else 1,
                0 if inside else 1,
                0 if looks else 1)

    # Stable, so the table order breaks every tie.
    return sorted(specs.items(), key=rank)


def terminal_choice(prefer: str | None = None,
                    discover: bool = True) -> tuple[str, dict, str] | None:
    """The terminal a spawn would use right now: (name, spec, label)."""
    for name, spec in terminal_order(prefer, discover):
        if terminal_available(spec)[0]:
            return name, spec, spec.get("label") or f"{name} tab"
    return None
