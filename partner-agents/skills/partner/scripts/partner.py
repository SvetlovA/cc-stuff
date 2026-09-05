#!/usr/bin/env python3
"""partner.py - run several AI agents as equal partners on one repository.

Every agent, including the one that spawned the others, is an ordinary
interactive session in its own terminal tab. You can walk into any tab and type
at that agent directly; between your interruptions each agent watches a shared
transcript and answers the others on its own.

One file, no third-party deps, runs on Windows / macOS / Linux.

State lives in <repo>/.partner/:
    roster.json        every agent, which one is you, who holds the write baton
    chat.md            the single shared debate transcript
    <id>/seed.md       the briefing that agent was launched with
    <id>/handoff.md    what was decided before it joined
    <id>/cursor        byte offset of the last message that agent consumed
    <id>/run.sh|.cmd   the command its terminal tab runs

Every subcommand prints either plain text or JSON (--json) so an agent can
parse it without screen-scraping.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

NL = chr(10)
MSG_DELIM = "<!--/msg-->"
WAIT_POLL = 3.0
WAIT_TIMEOUT = 120


# --------------------------------------------------------------------------
# paths + state
# --------------------------------------------------------------------------

def repo_root(start: Path | None = None) -> Path:
    """Anchor state at the git root so every tab agrees on one .partner dir."""
    cur = (start or Path.cwd()).resolve()
    for cand in [cur, *cur.parents]:
        if (cand / ".git").exists():
            return cand
    return cur


def state_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / ".partner"


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_roster(sd: Path) -> dict:
    f = sd / "roster.json"
    data = {}
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    data.setdefault("partners", {})
    data.setdefault("self", None)     # which id belongs to the agent running this
    data.setdefault("baton", None)
    return data


def me_id(roster: dict) -> str:
    """The id of whichever agent is running this script.

    There is no privileged participant. Every agent -- including the one that
    spawned the others -- is an ordinary entry in `partners`, and this is only
    a pointer saying which entry is looking in the mirror.
    """
    return roster.get("self") or "p1"


def baton_of(roster: dict) -> str:
    """Who may write. Falls back to you if the stored value names no one.

    A baton pointing at a stopped, removed or mistyped id would otherwise leave
    nobody able to edit, which looks like the tool hanging rather than failing.
    """
    held = roster.get("baton")
    if held and held in roster.get("partners", {}):
        return held
    return me_id(roster)


def save_roster(sd: Path, roster: dict) -> None:
    sd.mkdir(parents=True, exist_ok=True)
    tmp = sd / "roster.json.tmp"
    tmp.write_text(json.dumps(roster, indent=2), encoding="utf-8")
    tmp.replace(sd / "roster.json")


def git_exclude(root: Path) -> None:
    """Ignore .partner/ locally, without dirtying a tracked .gitignore."""
    dot_git = root / ".git"
    if not dot_git.exists():
        return
    if dot_git.is_dir():
        git_dir = dot_git
    else:
        # Worktree/submodule: .git is a file pointing at the real gitdir.
        # info/exclude lives in the *common* dir shared by all worktrees,
        # not the per-worktree gitdir, so ask git to resolve it.
        common = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if not common:
            return
        git_dir = Path(common)
        if not git_dir.exists():
            return
    info = git_dir / "info"
    info.mkdir(parents=True, exist_ok=True)
    ex = info / "exclude"
    body = ex.read_text(encoding="utf-8") if ex.exists() else ""
    if ".partner/" not in body:
        sep = "" if body.endswith(NL) or not body else NL
        ex.write_text(f"{body}{sep}.partner/{NL}", encoding="utf-8")


def _git(root: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=15)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _clip(text: str, limit: int, what: str) -> str:
    rows = text.splitlines()
    if len(rows) <= limit:
        return text
    return NL.join(rows[:limit]) + f"{NL}... (+{len(rows) - limit} more {what})"


def repo_snapshot(root: Path) -> str:
    """What the working tree looks like right now.

    A partner spawned mid-task needs to see the work in progress, not just
    committed history -- the uncommitted diff is usually the very thing being
    argued about. Collected automatically so that even a hurried spawn hands
    the partner something real to react to.
    """
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if not branch:
        return ""
    out = [f"Repo: {root.name}   Branch: {branch}"]
    log = _git(root, "log", "--oneline", "-5")
    if log:
        out.append(f"{NL}Recent commits:{NL}{log}")
    status = _git(root, "status", "--short")
    if not status:
        out.append(f"{NL}Working tree is clean.")
        return NL.join(out)
    out.append(f"{NL}Uncommitted changes:{NL}{_clip(status, 40, 'files')}")
    stat = _git(root, "diff", "--stat", "HEAD")
    if stat:
        out.append(f"{NL}Diff against HEAD:{NL}{_clip(stat, 40, 'files')}")
    return NL.join(out)


def cmd_snapshot(args) -> int:
    snap = repo_snapshot(repo_root()) or "(not a git repository)"
    emit(args, {"snapshot": snap}, snap)
    return 0


def write_wrappers(sd: Path, script: Path) -> str:
    """Write .partner/p.cmd and .partner/p.sh, and return the short form to use.

    Every agent command otherwise carries an absolute path to this script --
    ~90 characters repeated a dozen times through the briefing, which is noise
    the model has to read past on every line. A shell variable would be the
    usual fix, but agent harnesses generally run each command in a fresh shell,
    so nothing persists. A wrapper file does.
    """
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "p.cmd").write_text(
        '@echo off\r\n\"{}" \"{}" %*\r\n'.format(sys.executable, script),
        encoding="utf-8")
    sh = sd / "p.sh"
    sh.write_text('#!/usr/bin/env bash{}exec \"{}" \"{}" \"$@\"{}'.format(
        NL, sys.executable, script, NL), encoding="utf-8")
    try:
        sh.chmod(0o755)
    except OSError:
        pass
    return ".partner\\p.cmd" if os.name == "nt" else ".partner/p.sh"


def cmd_init(args) -> int:
    root = repo_root()
    sd = state_dir(root)
    sd.mkdir(parents=True, exist_ok=True)
    chat = sd / "chat.md"
    if not chat.exists():
        chat.write_text(
            "# Partner debate transcript" + NL * 2
            + "Shared by every agent working on this repo. "
            + "Append only; never rewrite history." + NL * 2,
            encoding="utf-8")
    roster = load_roster(sd)
    me = roster.get("self") or getattr(args, "me_name", None) or "p1"
    if me not in roster["partners"]:
        roster["partners"][me] = {
            "id": me,
            "provider": getattr(args, "me_provider", None) or "claude",
            "model": getattr(args, "me_model", None),
            "effort": None, "cmd": None, "auto": None,
            "status": "running", "started": utcnow(),
            "tab": "this session",
        }
    roster["self"] = me
    if roster.get("baton") not in roster["partners"]:
        roster["baton"] = me
    save_roster(sd, roster)
    write_wrappers(sd, Path(__file__).resolve())
    git_exclude(root)
    emit(args, {"state_dir": str(sd), "repo_root": str(root), "self": me},
         f"initialized {sd} (you are {me})")
    return 0


# --------------------------------------------------------------------------
# transcript
# --------------------------------------------------------------------------

def append_msg(sd: Path, sender: str, to: str, body: str, baton: str) -> None:
    chat = sd / "chat.md"
    chat.parent.mkdir(parents=True, exist_ok=True)
    if not chat.exists():
        chat.write_text("# Partner debate transcript" + NL * 2, encoding="utf-8")
    entry = (f"{NL}### {utcnow()} | from:{sender} | to:{to} | baton:{baton}{NL * 2}"
             f"{body.strip()}{NL * 2}{MSG_DELIM}{NL}")
    with chat.open("a", encoding="utf-8") as fh:
        fh.write(entry)


HEADER_RE = re.compile(
    r"^###\s+(?P<ts>\S+)\s*\|\s*from:(?P<from>\S+)\s*\|\s*to:(?P<to>\S+)\s*\|\s*baton:(?P<baton>\S+)\s*$",
    re.MULTILINE,
)


def parse_msgs(text: str) -> list[dict]:
    out = []
    for chunk in text.split(MSG_DELIM):
        m = HEADER_RE.search(chunk)
        if not m:
            continue
        out.append({
            "ts": m.group("ts"), "from": m.group("from"),
            "to": m.group("to"), "baton": m.group("baton"),
            "body": chunk[m.end():].strip(),
        })
    return out


def read_new(sd: Path, who: str, advance: bool = True) -> list[dict]:
    """Messages addressed to `who` (or @all) that `who` has not yet consumed."""
    chat = sd / "chat.md"
    if not chat.exists():
        return []
    raw = chat.read_bytes()
    cur_f = sd / who / "cursor"
    start = 0
    if cur_f.exists():
        try:
            start = min(int(cur_f.read_text(encoding="utf-8").strip() or 0), len(raw))
        except ValueError:
            start = 0
    fresh = raw[start:].decode("utf-8", errors="replace")
    msgs = [m for m in parse_msgs(fresh)
            if m["from"] != who and m["to"] in (who, "@all", "all")]
    if advance:
        cur_f.parent.mkdir(parents=True, exist_ok=True)
        cur_f.write_text(str(len(raw)), encoding="utf-8")
    return msgs


def tail_msgs(sd: Path, n: int) -> list[dict]:
    chat = sd / "chat.md"
    if not chat.exists():
        return []
    return parse_msgs(chat.read_text(encoding="utf-8", errors="replace"))[-n:]


def render(msgs: list[dict]) -> str:
    return (NL * 2).join(
        f"### {m['ts']} | {m['from']} -> {m['to']} | baton:{m['baton']}{NL * 2}{m['body']}"
        for m in msgs)


def baton_banner(roster: dict, who: str) -> str:
    """A one-line reminder of write permission, printed with every inbox read.

    The reminder lands at exactly the moment it is needed -- an agent reads its
    messages immediately before deciding what to do about them. Stating it every
    time costs one line and removes any excuse for editing out of turn.
    """
    holder = baton_of(roster)
    if holder == who:
        return f"[baton: yours -- you are the one who edits files right now]"
    return (f"[baton: {holder} -- do NOT edit files. Argue and propose instead. "
            f"If the human just told YOU to make a change, run `claim` first.]")


def cmd_wait(args) -> int:
    """Block until somebody addresses this agent.

    This is what lets an ordinary interactive session take part on its own: the
    agent runs this, the command sits there until a message arrives, and the
    agent then has something to answer. It always returns within --timeout so
    the tab never looks wedged and the human can interrupt and type instead.
    """
    sd = state_dir()
    who = args.who or me_id(load_roster(sd))
    deadline = time.time() + args.timeout
    while True:
        msgs = read_new(sd, who)
        if msgs:
            roster = load_roster(sd)
            if args.json:
                print(json.dumps({"baton": baton_of(roster), "you": who,
                                  "messages": msgs}, indent=2))
            else:
                print(baton_banner(roster, who) + NL * 2 + render(msgs))
            return 0
        if time.time() >= deadline:
            emit(args, {"messages": []},
                 f"(nothing addressed to {who} in {args.timeout}s "
                 f"-- run wait again to keep listening)")
            return 0
        time.sleep(args.poll)


def cmd_claim(args) -> int:
    """Take the write baton, because the human just gave you an instruction.

    The baton is meant to follow the human's attention: whoever they are
    talking to is the one who edits. Nothing outside the tabs can observe that
    -- only the agent being typed at knows it is being typed at -- so claiming
    is how that fact gets into shared state where the others can see it.
    """
    sd = state_dir()
    roster = load_roster(sd)
    who = args.who or me_id(roster)
    if who not in roster["partners"]:
        known = ", ".join(roster["partners"]) or "none"
        emit(args, {"error": "unknown"}, f"no agent named {who}; known: {known}")
        return 1
    prev = baton_of(roster)
    if prev == who:
        emit(args, {"baton": who, "changed": False},
             f"baton: already yours ({who}) -- go ahead")
        return 0
    roster["baton"] = who
    save_roster(sd, roster)
    append_msg(sd, "system", "@all",
               f"**{who}** was given an instruction directly by the human and has "
               f"taken the write baton from **{prev}**. {who} edits files from now "
               f"on; everyone else advises until the human turns to them.", who)
    emit(args, {"baton": who, "previous": prev, "changed": True},
         f"baton: {prev} -> {who} (you may edit now)")
    return 0


# --------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------
# Each provider needs one thing: how to start its normal interactive session
# with a starting prompt and without stopping to ask permission for routine
# work. Nothing is run headlessly, so there are no session ids to track and no
# output formats to parse -- which is why an unlisted CLI needs only a template.
#
# AUTO LEVELS
#   ask    leave the CLI's own prompting alone
#   edits  auto-accept file edits, still sandboxed        (default)
#   full   no prompts and no sandbox
#
# Flags verified against claude 2.x and codex 0.5x. They drift between
# releases; this table is the single place to correct them.

EFFORT_HINT = {
    "low": "Answer directly and keep reasoning brief.",
    "medium": "Think things through before answering.",
    "high": "Think hard. Consider at least two alternatives before answering.",
    "max": "Ultrathink. Stress-test your own position before you put it forward.",
}

AUTO_LEVELS = ("ask", "edits", "full")


def _claude_tui(c: dict) -> list[str]:
    argv = ["claude"]
    if c["model"]:
        argv += ["--model", c["model"]]
    mode = {"edits": "acceptEdits", "full": "bypassPermissions"}.get(c["auto"])
    if mode:
        argv += ["--permission-mode", mode]
    return argv + [c["prompt"]]


def _codex_tui(c: dict) -> list[str]:
    argv = ["codex"]
    if c["model"]:
        argv += ["-m", c["model"]]
    if c["effort"]:
        argv += ["-c", f'model_reasoning_effort="{c["effort"]}"']
    if c["auto"] == "edits":
        argv += ["-a", "never", "-s", "workspace-write"]
    elif c["auto"] == "full":
        argv += ["--dangerously-bypass-approvals-and-sandbox"]
    return argv + [c["prompt"]]


def _gemini_tui(c: dict) -> list[str]:
    argv = ["gemini"]
    if c["model"]:
        argv += ["-m", c["model"]]
    mode = {"edits": "auto_edit", "full": "yolo"}.get(c["auto"])
    if mode:
        argv += ["--approval-mode", mode]
    return argv + ["-i", c["prompt"]]


PROVIDERS: dict[str, dict] = {
    "claude": {"bin": "claude", "tui": _claude_tui, "effort": "prompt",
               "efforts": set(EFFORT_HINT),
               "models": ["claude-opus-5", "claude-sonnet-5",
                          "claude-haiku-4-5-20251001"],
               "install": "npm install -g @anthropic-ai/claude-code",
               "login": "claude  (then /login)"},
    "codex": {"bin": "codex", "tui": _codex_tui, "effort": "flag",
              # A real flag, so only the values the API accepts work here.
              # "max" is ours, not OpenAI's -- it maps onto "high".
              "efforts": {"low", "medium", "high"},
              "models": ["gpt-5-codex", "gpt-5"],
              "install": "npm install -g @openai/codex",
              "login": "codex login"},
    "gemini": {"bin": "gemini", "tui": _gemini_tui, "effort": "prompt",
               "efforts": set(EFFORT_HINT),
               "models": ["gemini-2.5-pro", "gemini-2.5-flash"],
               "install": "npm install -g @google/gemini-cli",
               "login": "gemini  (then follow the browser prompt)"},
}


def cli_runs(binary: str) -> bool:
    """Does the binary actually start? Catches broken or half-finished installs."""
    try:
        r = subprocess.run([binary, "--version"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=30)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def validate(provider: str, model: str | None, effort: str | None,
             cmd: str | None, force: bool = False) -> list[str]:
    """Check a partner's configuration, returning problems with their fixes.

    Failing here costs a second; failing at launch costs a terminal tab that
    flashes an error and disappears, which is much harder to diagnose. Each
    problem carries the command that resolves it rather than only the fact.
    """
    problems = []
    if provider == "custom":
        if not cmd:
            problems.append("--provider custom needs --cmd '<template containing {prompt}>'")
        else:
            binary = shlex.split(cmd)[0] if shlex.split(cmd) else ""
            if binary and not _has(binary):
                problems.append(
                    f"'{binary}' is not on PATH. Install it, or correct the --cmd template.")
        return problems

    spec = PROVIDERS.get(provider)
    if not spec:
        problems.append(f"unknown provider '{provider}'. "
                        f"Known: {', '.join(PROVIDERS)}, custom. "
                        f"For anything else use: --provider custom --cmd '...'")
        return problems

    binary = spec["bin"]
    if not _has(binary):
        problems.append(f"'{binary}' is not installed or not on PATH. "
                        f"Install it with:  {spec['install']}")
    elif not cli_runs(binary):
        problems.append(f"'{binary}' is on PATH but `{binary} --version` failed. "
                        f"The install looks broken -- try:  {spec['install']}")

    if effort and effort not in spec["efforts"]:
        valid = ", ".join(sorted(spec["efforts"]))
        extra = ""
        if spec["effort"] == "flag" and effort == "max":
            extra = " ('max' is this skill's own level; use 'high' for a real flag.)"
        problems.append(f"effort '{effort}' is not supported by {provider}. "
                        f"Use one of: {valid}.{extra}")

    if model and model not in spec["models"] and not force:
        problems.append(
            f"model '{model}' is not in the known list for {provider}: "
            f"{', '.join(spec['models'])}. If it is a new or aliased model the "
            f"list has not caught up with, re-run with --force to use it anyway.")
    return problems


def cmd_check(args) -> int:
    problems = validate(args.provider, args.model, args.effort, args.cmd, args.force)
    if not problems:
        bits = [args.provider]
        if args.model:
            bits.append(args.model)
        if args.effort:
            bits.append(f"effort={args.effort}")
        emit(args, {"ok": True, "problems": []}, "ok: " + " / ".join(bits))
        return 0
    emit(args, {"ok": False, "problems": problems},
         "cannot start this partner:" + NL
         + NL.join(f"  - {p}" for p in problems))
    return 1


def _custom_tui(template: str, c: dict) -> list[str]:
    """Any CLI, described by a template string.

    Placeholders: {prompt} {model} {effort} {auto}
    e.g. --cmd 'aider --model {model} --yes --message {prompt}'
    A template without {prompt} gets the starting prompt appended last.
    """
    out, saw = [], False
    for part in shlex.split(template):
        if "{prompt}" in part:
            saw = True
        out.append(part.replace("{prompt}", c["prompt"])
                       .replace("{model}", c["model"] or "")
                       .replace("{effort}", c["effort"] or "")
                       .replace("{auto}", c["auto"] or ""))
    out = [x for x in out if x != ""]
    if not saw:
        out.append(c["prompt"])
    return out


def build_tui(p: dict, prompt: str) -> list[str]:
    c = {"prompt": prompt, "model": p.get("model") or "",
         "effort": p.get("effort") or "", "auto": p.get("auto") or "edits"}
    if p["provider"] == "custom":
        return _custom_tui(p.get("cmd") or "", c)
    spec = PROVIDERS.get(p["provider"])
    if not spec:
        raise SystemExit(f"unknown provider {p['provider']!r}")
    return spec["tui"](c)


def provider_spec(prov: str) -> dict:
    return PROVIDERS.get(prov, {"effort": "prompt", "bin": None, "models": []})


# --------------------------------------------------------------------------
# terminal tabs
# --------------------------------------------------------------------------
# Quoting a nested agent command through wt.exe / osascript / gnome-terminal is
# where cross-platform launchers normally rot. We sidestep it entirely: write
# the real command into a per-agent run script, then every terminal only ever
# has to run one plain file path.

def write_runner(pdir: Path, argv: list[str], cwd: Path) -> Path:
    if os.name == "nt":
        r = pdir / "run.cmd"
        r.write_text("@echo off\r\ncd /d \"{}\"\r\n{}\r\n".format(
            cwd, subprocess.list2cmdline(argv)), encoding="utf-8")
    else:
        r = pdir / "run.sh"
        r.write_text("#!/usr/bin/env bash{}cd {}{}exec {}{}".format(
            NL, shlex.quote(str(cwd)), NL, shlex.join(argv), NL), encoding="utf-8")
        r.chmod(0o755)
    return r


def _has(binary: str) -> bool:
    return shutil.which(binary) is not None


def orca_bin() -> str | None:
    """The Orca CLI, but only when this session is running inside Orca.

    Orca manages its own terminal tabs, so opening a partner in a detached
    OS terminal there would strand it outside the workspace the user is
    actually looking at. The env markers are set by Orca for processes it
    launches; without them we are somewhere else and take the normal path.
    """
    if not (os.environ.get("ORCA_WORKTREE_ID") or os.environ.get("ORCA_TERMINAL_HANDLE")
            or os.environ.get("ORCA_TAB_ID")):
        return None
    found = shutil.which("orca")
    if found:
        return found
    # Orca points at its own binary here; use it if PATH does not carry it.
    fallback = os.environ.get("ORCA_CODEX_LAUNCH_PREFLIGHT", "")
    return fallback if fallback and Path(fallback).exists() else None


def open_orca_tab(title: str, runner: Path, cwd: Path) -> str:
    """Open the partner as a tab in the current Orca worktree."""
    orca = orca_bin()
    if not orca:
        return ""
    cmd = (f'cmd /c "{runner}"' if os.name == "nt" else f'bash "{runner}"')
    argv = [orca, "terminal", "create", "--worktree", f"path:{cwd}",
            "--title", title, "--command", cmd, "--json"]
    try:
        r = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if r.returncode != 0:
        return ""
    try:
        if json.loads(r.stdout).get("ok") is False:
            return ""
    except json.JSONDecodeError:
        pass
    return "Orca tab"


def tab_command(title: str, runner: Path, cwd: Path) -> tuple[list[str], str] | None:
    """Pick the best available way to open a new tab. Returns (argv, label)."""
    r, c = str(runner), str(cwd)

    # Multiplexers first: if the user is already in one, a real tab is free.
    if os.environ.get("TMUX") and _has("tmux"):
        return ["tmux", "new-window", "-n", title, "-c", c, f"bash {r}"], "tmux tab"
    if os.environ.get("ZELLIJ") and _has("zellij"):
        return ["zellij", "run", "--name", title, "--cwd", c, "--", "bash", r], "zellij pane"

    # Cross-platform terminals with a control CLI.
    if _has("wezterm"):
        return ["wezterm", "cli", "spawn", "--cwd", c, "--", "bash", r], "WezTerm tab"
    if _has("kitty") and os.environ.get("KITTY_LISTEN_ON"):
        return ["kitty", "@", "launch", "--type=tab", "--tab-title", title,
                "--cwd", c, "bash", r], "kitty tab"

    if os.name == "nt":
        if _has("wt.exe") or _has("wt"):
            return [shutil.which("wt.exe") or "wt", "-w", "0", "nt",
                    "--title", title, "-d", c, "cmd.exe", "/k", r], "Windows Terminal tab"
        return ["cmd.exe", "/c", "start", title, "cmd.exe", "/k", r], "cmd window"

    if sys.platform == "darwin":
        if Path("/Applications/iTerm.app").exists():
            script = ('tell application "iTerm2" to if it is running then' + NL
                      + '  tell current window to create tab with default profile '
                      + f'command "bash {r}"' + NL + 'end if')
            return ["osascript", "-e", script], "iTerm2 tab"
        return ["osascript", "-e",
                f'tell application "Terminal" to do script "bash {r}"',
                "-e", 'tell application "Terminal" to activate'], "Terminal.app tab"

    for argv, label in (
        (["gnome-terminal", "--tab", f"--title={title}", "--", "bash", r], "GNOME Terminal tab"),
        (["konsole", "--new-tab", "-e", "bash", r], "Konsole tab"),
        (["xfce4-terminal", "--tab", f"--title={title}", "-e", f"bash {r}"], "Xfce Terminal tab"),
        (["terminator", "-e", f"bash {r}"], "Terminator window"),
        (["alacritty", "-e", "bash", r], "Alacritty window"),
        (["xterm", "-T", title, "-e", "bash", r], "xterm window"),
    ):
        if _has(argv[0]):
            return argv, label
    return None


def open_tab(title: str, runner: Path, cwd: Path) -> str:
    # Orca first: inside it, a detached OS terminal would put the partner
    # outside the workspace the user is looking at.
    label = open_orca_tab(title, runner, cwd)
    if label:
        return label
    picked = tab_command(title, runner, cwd)
    if not picked:
        return ""
    argv, label = picked
    try:
        subprocess.Popen(argv, cwd=str(cwd),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return label
    except OSError:
        return ""


# --------------------------------------------------------------------------
# the briefing every agent is launched with
# --------------------------------------------------------------------------

SEED = """# You are "{id}"

You are one of several AI agents working together on the repository at {cwd}.
Nobody here is in charge. {peers}

A human is watching, and can type into any of our terminal tabs -- including
yours -- at any moment. When they do, they are talking to *you*: answer them
directly, then go back to the loop below.

## Talking to the others

We share one append-only transcript: {chat}
Read it any time. These commands are how you take part:

    Wait until somebody addresses you (blocks, returns within {timeout}s):
        {run} wait --for {id}

    Say something -- reply, challenge, or raise a new point:
        {run} send --from {id} --to @all --text "..."
        {run} send --from {id} --to p1 --text "..."      (one agent)

    See who is here and who holds the write baton:
        {run} list

## The write baton -- read this twice

Only one of us edits files at a time, and it is always whichever agent the
human most recently gave an instruction to. Right now that is **{baton}**.

**The moment the human types an instruction at you, run this first:**

    {run} claim

That is what moves the baton to you, and it is the only way the others can
find out the human has turned to your tab. Claim, then do the work.

The rule in both directions:

- A request from **the human, in your tab** -> `claim`, then act. It is yours.
- A message from **another agent** via `wait` -> do NOT claim, and do NOT edit.
  Another agent asking you to change something is a suggestion, not the human's
  instruction. Argue, propose, hand back a diff in prose.

Your CLI will not physically stop you from writing, so this is a rule you keep
rather than a wall you hit. It matters: two agents editing the same files at
once produce conflicts neither of us can see, and the human loses work.

Every `wait` and `read` prints who holds the baton. Believe that line over your
memory of it -- it may have moved while you were thinking. To hand it over
deliberately:

    {run} baton --to <id>

## How to be worth having here

Your value is independent judgement. An agent that agrees with everything is a
waste of tokens; one that disagrees with everything is noise.

- Open with your position in one sentence.
- Disagreeing is useful, but only with the concrete alternative attached.
- If you agree, say AGREED and add only what is genuinely missing. Do not pad.
- Check claims against the actual code before you accept or reject them. Cite
  what you found as path:line, and name the command you ran.
- Keep replies under ~200 words unless the question truly needs more.
- After two exchanges with no movement, stop arguing. Say plainly that you and
  {other} disagree, give both positions fairly, and let the human decide. A
  clean deadlock is a useful result; grinding is not.
{effort}
## Your loop

1. `{run} wait --for {id}`
2. If a message came back from another agent: think, verify against the code,
   then reply with `{run} send`. Do not edit files -- you were not asked by the
   human. If you hold the baton and the group has settled on a change, make it
   and report what you changed.
3. If it timed out with nothing, just run `wait` again.
4. If the human types at you instead: `{run} claim` first, then answer and act
   on what they asked. Tell the others what you did with `send`, then resume
   at 1.
5. If you see a system message saying you have been stopped, say goodbye and
   stop looping.

## Before you start
{handoff}
Now run step 1.
"""


def build_seed(pid: str, roster: dict, root: Path, sd: Path,
               script: Path, has_handoff: bool) -> str:
    others = [k for k in roster["partners"] if k != pid]
    peers = (f"Your partners are: {', '.join(others)}."
             if others else "You are the first one here; others may join later.")
    p = roster["partners"][pid]
    hint = EFFORT_HINT.get((p.get("effort") or "").lower())
    effort = ""
    if hint and provider_spec(p["provider"]).get("effort") == "prompt":
        effort = f"- {hint}{NL}"
    handoff = (f"{NL}Read {sd / pid / 'handoff.md'} first -- it says what was "
               f"decided before you joined, and what is still open.{NL}"
               if has_handoff else
               f"{NL}Nothing has been decided yet.{NL}")
    return SEED.format(
        id=pid, cwd=root, peers=peers, chat=sd / "chat.md",
        run=write_wrappers(sd, script), timeout=WAIT_TIMEOUT,
        baton=baton_of(roster), other=", ".join(others) or "the others",
        effort=effort, handoff=handoff)


# --------------------------------------------------------------------------
# spawning
# --------------------------------------------------------------------------

def cmd_spawn(args) -> int:
    root = repo_root()
    sd = state_dir(root)
    cmd_init(argparse.Namespace(json=False, quiet=True))

    roster = load_roster(sd)
    pid = args.name or f"p{len(roster['partners']) + 1}"
    if pid in roster["partners"] and not args.replace:
        emit(args, {"error": "exists"}, f"agent {pid!r} already exists; pass --replace")
        return 1

    # Validate before anything is written or a tab is opened. A bad flag caught
    # here is one message; caught at launch it is a tab that flashes an error
    # and vanishes.
    problems = validate(args.provider, args.model, args.effort, args.cmd, args.force)
    if problems:
        emit(args, {"error": "invalid", "problems": problems},
             f"cannot start {pid}:" + NL + NL.join(f"  - {p}" for p in problems))
        return 1

    pdir = sd / pid
    pdir.mkdir(parents=True, exist_ok=True)
    entry = {"id": pid, "provider": args.provider, "model": args.model,
             "effort": args.effort, "cmd": args.cmd, "auto": args.auto,
             "status": "running", "started": utcnow()}
    roster["partners"][pid] = entry

    # Each agent keeps its own briefing: spawning a third must not clobber what
    # the second was told, and they may be joining for different reasons.
    brief = []
    if args.context:
        ctx = Path(args.context)
        brief.append(ctx.read_text(encoding="utf-8", errors="replace")
                     if ctx.exists() else args.context)
    snap = repo_snapshot(root)
    if snap:
        brief.append("## Working tree at the moment you joined" + NL * 2 + snap)
    hist = tail_msgs(sd, 20)
    if hist:
        brief.append("## Debate you are joining, most recent last" + NL * 2
                     + render(hist))
    if brief:
        (pdir / "handoff.md").write_text((NL * 2).join(brief), encoding="utf-8")

    seed_f = pdir / "seed.md"
    seed_f.write_text(build_seed(pid, roster, root, sd,
                                 Path(__file__).resolve(), bool(brief)),
                      encoding="utf-8")

    # The starting prompt only points at the seed. Keeping it short avoids
    # pushing a multi-kilobyte argument through a terminal command line.
    boot = (f"Read {seed_f} and follow it exactly. It explains who you are, "
            f"who you are working with, and how to talk to them. Begin now.")

    argv = build_tui(entry, boot)
    runner = write_runner(pdir, argv, root)
    label = "" if args.no_tab else open_tab(f"partner:{pid}", runner, root)
    entry["tab"] = label
    entry["runner"] = str(runner)
    save_roster(sd, roster)

    manual = (f'cmd /c "{runner}"' if os.name == "nt" else f'bash "{runner}"')
    spec = (f"{args.provider}"
            f"{'/' + args.model if args.model else ''}"
            f"{'/' + args.effort if args.effort else ''}"
            f", auto={args.auto}")
    if label:
        msg = f"{pid} ({spec}) started in {label}"
    elif args.no_tab:
        msg = f"{pid} ({spec}) registered. Start it with:{NL}  {manual}"
    else:
        msg = (f"{pid} ({spec}) registered, but no terminal could be opened."
               f"{NL}Open a tab yourself and run:{NL}  {manual}")
    emit(args, {**entry, "manual_command": manual, "seed": str(seed_f)}, msg)
    return 0


# --------------------------------------------------------------------------
# messaging + roster
# --------------------------------------------------------------------------

def cmd_send(args) -> int:
    sd = state_dir()
    roster = load_roster(sd)
    args.sender = args.sender or me_id(roster)
    body = args.text
    if args.file:
        body = Path(args.file).read_text(encoding="utf-8")
    if not body:
        body = sys.stdin.read()

    chat = sd / "chat.md"
    before = chat.stat().st_size if chat.exists() else 0
    append_msg(sd, args.sender, args.to, body, baton_of(roster))

    if not args.wait:
        emit(args, {"sent": True, "to": args.to}, f"sent to {args.to}")
        return 0

    running = [k for k, v in roster["partners"].items()
               if v.get("status") == "running" and k != args.sender
               and k != me_id(roster)]
    expect = args.expect or (len(running) if args.to in ("@all", "all") else 1)
    deadline, seen = time.time() + args.wait, {}
    while time.time() < deadline:
        raw = chat.read_bytes()[before:].decode("utf-8", errors="replace")
        for m in parse_msgs(raw):
            if m["from"] != args.sender and m["to"] in (args.sender, "@all", "all"):
                seen[m["from"]] = m
        if len(seen) >= max(expect, 1):
            break
        time.sleep(1.5)

    replies = list(seen.values())
    text = render(replies) or (
        f"(no reply within {args.wait}s -- check the agent tabs; "
        f"they answer when they next run `wait`)")
    emit(args, {"replies": replies}, text)
    return 0


def cmd_read(args) -> int:
    sd = state_dir()
    roster = load_roster(sd)
    who = args.who or me_id(roster)
    msgs = read_new(sd, who, advance=not args.peek)
    if args.json:
        print(json.dumps({"baton": baton_of(roster), "you": who,
                          "messages": msgs}, indent=2))
    else:
        print(baton_banner(roster, who) + NL * 2
              + (render(msgs) or "(nothing new)"))
    return 0


def cmd_list(args) -> int:
    sd = state_dir()
    roster = load_roster(sd)
    if args.json:
        print(json.dumps(roster, indent=2))
        return 0
    holder = baton_of(roster)
    print(f"baton: {holder}   (only the baton holder edits files)")
    if not roster["partners"]:
        print("nobody here -- spawn someone with: partner.py spawn --provider codex")
        return 0
    me = me_id(roster)
    for pid, p in roster["partners"].items():
        mark = " <-- baton" if holder == pid else ""
        mark += "  (you)" if pid == me else ""
        print(f"  {pid:8} {p['provider']:8} {p.get('model') or '-':22} "
              f"effort={p.get('effort') or '-':6} auto={p.get('auto') or '-':6}"
              f"[{p.get('status')}] {p.get('tab') or ''}{mark}")
    return 0


def cmd_baton(args) -> int:
    sd = state_dir()
    roster = load_roster(sd)
    if not args.to:
        holder = baton_of(roster)
        emit(args, {"baton": holder}, f"baton: {holder}")
        return 0
    if args.to not in roster["partners"]:
        known = ", ".join(roster["partners"]) or "none"
        emit(args, {"error": "unknown"}, f"no agent named {args.to}; known: {known}")
        return 1
    prev = baton_of(roster)
    roster["baton"] = args.to
    save_roster(sd, roster)
    append_msg(sd, "system", "@all",
               f"Write baton moved from **{prev}** to **{args.to}**. "
               f"{args.to} edits files from now on; everyone else advises.", args.to)
    emit(args, {"baton": args.to, "previous": prev}, f"baton: {prev} -> {args.to}")
    return 0


def cmd_stop(args) -> int:
    sd = state_dir()
    roster = load_roster(sd)
    me = me_id(roster)
    if args.all:
        # --all means every agent you spawned. This session has no tab to close
        # and stopping it would leave the transcript with nobody reading it.
        targets = [k for k in roster["partners"] if k != me]
    else:
        targets = [args.id] if args.id else []
    if not targets:
        emit(args, {"error": "target"}, "pass --id <id> or --all")
        return 1
    for t in targets:
        if t in roster["partners"]:
            roster["partners"][t]["status"] = "stopped"
    if roster.get("baton") in targets:
        roster["baton"] = me
    save_roster(sd, roster)
    append_msg(sd, "system", "@all",
               f"Stopped: {', '.join(targets)}. Say goodbye and stop looping.",
               baton_of(roster))
    emit(args, {"stopped": targets},
         f"stopped {', '.join(targets)} -- they exit at their next `wait`; "
         f"close their tabs when they do")
    return 0


def cmd_providers(args) -> int:
    rows = [{"provider": n, "installed": _has(s["bin"]), "models": s["models"],
             "efforts": sorted(s["efforts"]), "install": s["install"],
             "effort": "native flag" if s["effort"] == "flag" else "prompt hint"}
            for n, s in PROVIDERS.items()]
    rows.append({"provider": "custom", "installed": None, "models": [],
                 "efforts": [], "install": "", "effort": "template"})
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    labels = {True: "installed", False: "NOT installed", None: "via --cmd"}
    for r in rows:
        print(f"  {r['provider']:8} {labels[r['installed']]:15} "
              f"effort: {r['effort']:12} models: {', '.join(r['models']) or '-'}")
        if r["efforts"]:
            print(f"           {'':15} accepts effort: {', '.join(r['efforts'])}")
        if r["installed"] is False:
            print(f"           {'':15} install with: {r['install']}")
    where = "Orca tab" if orca_bin() else "a new terminal tab"
    print(f"{NL}Partners will open in {where}.")
    return 0


def emit(args, data: dict, human: str) -> None:
    if getattr(args, "quiet", False):
        return
    print(json.dumps(data, indent=2) if getattr(args, "json", False) else human)


def main() -> int:
    ap = argparse.ArgumentParser(prog="partner.py",
                                 description="Equal AI partners on one repo.")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init")
    i.add_argument("--me-name", default=None, help="your own id (default p1)")
    i.add_argument("--me-provider", default=None)
    i.add_argument("--me-model", default=None)
    i.set_defaults(fn=cmd_init)

    sub.add_parser("providers").set_defaults(fn=cmd_providers)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    sub.add_parser("snapshot").set_defaults(fn=cmd_snapshot)

    sp = sub.add_parser("spawn", help="start an agent in a new terminal tab")
    sp.add_argument("--provider", required=True)
    sp.add_argument("--model", default=None)
    sp.add_argument("--effort", default=None, choices=["low", "medium", "high", "max"])
    sp.add_argument("--auto", default="edits", choices=list(AUTO_LEVELS),
                    help="permission prompting: ask | edits (default) | full")
    sp.add_argument("--name", default=None, help="agent id (default p2, p3, ...)")
    sp.add_argument("--cmd", default=None, help="command template for --provider custom")
    sp.add_argument("--context", default=None, help="handoff text, or path to a file")
    sp.add_argument("--no-tab", action="store_true")
    sp.add_argument("--replace", action="store_true")
    sp.add_argument("--force", action="store_true",
                    help="use a model the known list has not caught up with")
    sp.set_defaults(fn=cmd_spawn)

    ck = sub.add_parser("check", help="validate a config without starting anything")
    ck.add_argument("--provider", required=True)
    ck.add_argument("--model", default=None)
    ck.add_argument("--effort", default=None)
    ck.add_argument("--cmd", default=None)
    ck.add_argument("--force", action="store_true")
    ck.set_defaults(fn=cmd_check)

    w = sub.add_parser("wait", help="block until somebody addresses you")
    w.add_argument("--for", dest="who", default=None)
    w.add_argument("--timeout", type=int, default=WAIT_TIMEOUT)
    w.add_argument("--poll", type=float, default=WAIT_POLL)
    w.set_defaults(fn=cmd_wait)

    s = sub.add_parser("send")
    s.add_argument("--from", dest="sender", default=None,
                   help="sender id (default: whoever is running this)")
    s.add_argument("--to", default="@all")
    s.add_argument("--text", default=None)
    s.add_argument("--file", default=None)
    s.add_argument("--wait", type=int, default=0, help="block N seconds for replies")
    s.add_argument("--expect", type=int, default=0, help="how many repliers to wait for")
    s.set_defaults(fn=cmd_send)

    r = sub.add_parser("read")
    r.add_argument("--for", dest="who", default=None,
                   help="reader id (default: whoever is running this)")
    r.add_argument("--peek", action="store_true", help="do not advance the cursor")
    r.set_defaults(fn=cmd_read)

    b = sub.add_parser("baton")
    b.add_argument("--to", default=None)
    b.set_defaults(fn=cmd_baton)

    cl = sub.add_parser("claim", help="take the baton: the human just told YOU to act")
    cl.add_argument("--for", dest="who", default=None)
    cl.set_defaults(fn=cmd_claim)

    st = sub.add_parser("stop")
    st.add_argument("--id", default=None)
    st.add_argument("--all", action="store_true")
    st.set_defaults(fn=cmd_stop)

    args = ap.parse_args()
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
