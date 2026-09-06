#!/usr/bin/env python3
"""partner.py - run several AI agents as equal partners on one repository.

Every agent is an ordinary interactive session. You can type at any of them,
and the one you address takes the write baton; the rest read, argue, and keep
their hands off the files. Between your interruptions each agent watches a
shared transcript and answers the others on its own.

The symmetry is structural, not described: `init` and `spawn` build roster
entries and briefings with the same code, so the agent that starts a session
cannot drift from the ones it starts. Any agent can spawn more, and any agent
can hold the baton.

One file, no third-party deps, runs on Windows / macOS / Linux.

State lives in <repo>/.partner/:
    roster.json        every agent, who holds the write baton
    chat.md            the single shared debate transcript
    p.cmd | p.sh       shared wrapper, for a human at a shell
    <id>/p.cmd|p.sh    that agent's wrapper -- exports its PARTNER_ID
    <id>/seed.md       its briefing, the same document for every agent
    <id>/handoff.md    what was decided before it joined
    <id>/cursor        byte offset of the last message it consumed
    <id>/run.cmd|.sh   the command its terminal tab runs

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

    Identity is per-process, not shared state: every agent runs through its own
    wrapper in .partner/<id>/, which exports PARTNER_ID. roster["self"] is only
    the fallback for the one agent that has no wrapper of its own -- the session
    someone typed the skill into. Reading identity from the shared roster
    instead would make every agent believe it was whoever ran `init`.
    """
    return os.environ.get("PARTNER_ID") or roster.get("self") or "p1"


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


def write_wrappers(sd: Path, script: Path, pid: str | None = None) -> str:
    """Write a wrapper for `pid` (or the shared one) and return how to call it.

    Two problems solved by one file. First, every command would otherwise carry
    an absolute path to this script -- ~90 characters repeated through the
    briefing, which is noise the model reads past on every line. Second, and
    more important, the wrapper exports PARTNER_ID, so an agent's identity
    comes from the process it is running in rather than from shared state that
    every agent would read identically.
    """
    target = (sd / pid) if pid else sd
    target.mkdir(parents=True, exist_ok=True)
    setid_cmd = f"set PARTNER_ID={pid}\r\n" if pid else ""
    setid_sh = f"export PARTNER_ID={pid}{NL}" if pid else ""

    (target / "p.cmd").write_text(
        "@echo off\r\n{}\"{}\" \"{}\" %*\r\n".format(setid_cmd, sys.executable, script),
        encoding="utf-8")
    sh = target / "p.sh"
    sh.write_text('#!/usr/bin/env bash{}{}exec "{}" "{}" "$@"{}'.format(
        NL, setid_sh, sys.executable, script, NL), encoding="utf-8")
    try:
        sh.chmod(0o755)
    except OSError:
        pass

    rel = f".partner/{pid}" if pid else ".partner"
    if os.name == "nt":
        return rel.replace("/", "\\") + "\\p.cmd"
    return rel + "/p.sh"


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
    fresh = me not in roster["partners"]
    if fresh:
        # Exactly the shape spawn produces. The agent that starts the session is
        # a participant like any other, not a stub with the others hanging off
        # it -- so it carries the same fields and gets the same briefing.
        roster["partners"][me] = {
            "id": me,
            "provider": getattr(args, "me_provider", None) or "claude",
            "model": getattr(args, "me_model", None),
            "effort": getattr(args, "me_effort", None),
            "auto": getattr(args, "me_auto", None) or "edits",
            "cmd": None, "kind": "session",
            "status": "running", "started": utcnow(),
            "tab": "this session",
        }
    roster["self"] = me
    if roster.get("baton") not in roster["partners"]:
        roster["baton"] = me
    save_roster(sd, roster)

    write_wrappers(sd, Path(__file__).resolve())            # shared, for humans
    write_wrappers(sd, Path(__file__).resolve(), me)        # this agent's own
    if fresh:
        write_briefing(sd, me, roster, root, Path(__file__).resolve(),
                       getattr(args, "context", None), kind="session")
    git_exclude(root)
    emit(args, {"state_dir": str(sd), "repo_root": str(root), "self": me},
         f"initialized {sd} (you are {me}; briefing at {sd / me / 'seed.md'})")
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


def pending_for(sd: Path, roster: dict, who: str) -> list[dict]:
    """Messages `who` still owes a reply to.

    Empty when `who` holds the baton (it drives the change, it is not waiting on
    anyone), when nothing is addressed to it, or when it has already spoken
    since the last message addressed to it. An agent's own messages never count
    -- it cannot owe itself a reply.
    """
    if baton_of(roster) == who:
        return []
    chat = sd / "chat.md"
    if not chat.exists():
        return []
    convo = [m for m in parse_msgs(chat.read_text(encoding="utf-8", errors="replace"))
             if m["from"] != "system"]
    my_last = max((i for i, m in enumerate(convo) if m["from"] == who), default=-1)
    return [m for i, m in enumerate(convo)
            if i > my_last and m["from"] != who
            and m["to"] in (who, "@all", "all")]


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
    # Hold <id>/waiting for as long as we poll, stamped with our own deadline.
    # This is what lets the Stop hook tell "listening right now" from "ran some
    # command recently" -- every other command touches lastseen too.
    mark = sd / who / "waiting"
    try:
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.write_text(str(deadline), encoding="utf-8")
    except OSError:
        pass
    try:
        while True:
            touch_seen(sd, who)          # still here, still listening
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
    finally:
        try:
            mark.unlink()
        except OSError:
            pass


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
    touch_seen(sd, who)
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
               f"on; everyone else advises until the human turns to them. "
               f"**{prev}**: you are an advisor again -- go back to `wait` so you "
               f"hear what follows.", who)
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


# --------------------------------------------------------------------------
# what this machine actually has
# --------------------------------------------------------------------------
# Nothing below is a closed list of blessed CLIs, and nothing below is a
# snapshot of anybody's model lineup. Both go stale the week they are written,
# and the cost is paid by the user: a partner they cannot start, or a model
# they are not offered because this file has not been edited since it shipped.
#
# So the sources of truth live outside this file:
#
#   * CLIs come from a scan of the directories this machine installs them into
#     -- PATH plus the per-user bin dirs npm/bun/cargo/pipx/winget use. Naming
#     a binary the scan never surfaced works too; the scan suggests, it does
#     not gate.
#   * Launch flags come from the CLI's own --help whenever there is no recipe
#     for it, so an unknown CLI is drivable the first time it is named.
#   * Model ids come from the CLI itself (a list command, its help, its own
#     bundle) and from the user's config for it -- never from a table here.
#   * The only claim this file still makes about a model is what its *name
#     shape* implies: an "opus"/"pro"/"max" tier reasons deeper and slower than
#     a "flash"/"mini"/"lite" one. That stays true for models not yet released,
#     which is the whole reason it is phrased as a shape and not as an id.
#
# RECIPES holds hand-verified launch flags for the CLIs whose flags were
# checked by hand. It is an accelerator, not a permission list: every code path
# below falls back to probing when a name is not in it.

RECIPES: dict[str, dict] = {
    "claude": {"bin": "claude", "tui": _claude_tui, "effort": "prompt",
               "efforts": set(EFFORT_HINT),
               "install": "npm install -g @anthropic-ai/claude-code",
               "login": "claude  (then /login)"},
    "codex": {"bin": "codex", "tui": _codex_tui, "effort": "flag",
              # A real API field, so only values the API accepts work here.
              # "max" is this skill's own level -- it maps onto "high".
              "efforts": {"low", "medium", "high"},
              "install": "npm install -g @openai/codex",
              "login": "codex login"},
    "gemini": {"bin": "gemini", "tui": _gemini_tui, "effort": "prompt",
               "efforts": set(EFFORT_HINT),
               "install": "npm install -g @google/gemini-cli",
               "login": "gemini  (then follow the browser prompt)"},
}

# Kept as an alias: older briefings and any external caller still say PROVIDERS.
PROVIDERS = RECIPES


# --------------------------------------------------------------------------
# where CLIs get installed

def _user_bin_dirs() -> list[Path]:
    r"""The per-user directories package managers drop CLI entry points into.

    PATH alone is not enough. A CLI installed by a vendor installer often lands
    somewhere PATH only picks up in a login shell -- codex under
    %LOCALAPPDATA%\Programs\OpenAI\Codex\bin is a real example -- and an agent
    inherits whatever environment the harness happened to have.
    """
    home = Path.home()
    d = [home / ".local" / "bin", home / "bin", home / ".bun" / "bin",
         home / ".cargo" / "bin", home / "go" / "bin", home / ".deno" / "bin",
         home / ".npm-global" / "bin", home / ".volta" / "bin",
         home / ".yarn" / "bin", home / ".pixi" / "bin"]
    if os.name == "nt":
        appdata, local = os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA")
        if appdata:
            d += [Path(appdata) / "npm", Path(appdata) / "Python" / "Scripts"]
        if local:
            lp = Path(local)
            d += [lp / "Microsoft" / "WinGet" / "Links", lp / "pnpm",
                  lp / "Yarn" / "bin"]
            progs = lp / "Programs"
            if progs.is_dir():
                # Vendor installers bury the entry point a level or two down.
                for pat in ("*/bin", "*/*/bin", "*/*"):
                    try:
                        d += [p for p in progs.glob(pat) if p.is_dir()]
                    except OSError:
                        pass
    else:
        d += [Path("/usr/local/bin"), Path("/opt/homebrew/bin"),
              home / ".local" / "share" / "pnpm"]
    return d


def _exe_exts() -> tuple[str, ...]:
    if os.name != "nt":
        return ("",)
    raw = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD;.PS1")
    return tuple(e.lower() for e in raw.split(os.pathsep) if e)


def search_dirs(skip_system: bool = True) -> list[Path]:
    r"""Every directory worth looking in, deduplicated, user dirs first.

    `skip_system` drops the operating system's own bin directories. Nobody
    installs an agent CLI into C:\Windows\System32 or /usr/sbin, and scanning
    them costs thousands of pointless probes -- plus a listing full of
    gpg-agent and MBR2GPT, which is worse than useless when the output is
    meant to be a short list of things to spawn a partner in.
    """
    sysish = ("\\windows\\", "system32", "syswow64", "/usr/bin", "/bin/",
              "/sbin", "/usr/sbin", "\\program files")
    out, seen = [], set()
    cands = [str(p) for p in _user_bin_dirs()]
    cands += (os.environ.get("PATH") or "").split(os.pathsep)
    for raw in cands:
        if not raw.strip():
            continue
        try:
            p = Path(raw).expanduser()
            key = str(p.resolve()).lower()
        except OSError:
            continue
        if key in seen or not p.is_dir():
            continue
        if skip_system and any(s in key + os.sep for s in sysish):
            continue
        seen.add(key)
        out.append(p)
    return out


# Name signals that make a binary worth surfacing in the *fast* scan. These are
# accelerators, not a whitelist: `--deep` classifies by behaviour instead, and
# naming any binary directly bypasses this regex entirely. Product names are in
# here only because they are the cheapest possible hit; a CLI called something
# nobody here predicted is still reachable by the other two routes.
LIKELY_AGENT_RE = re.compile(
    r"(?:^|[-_])(?:ai|llm|gpt|agent|agents|assistant|bot|chat|code|coder|"
    r"copilot|cli|pair)(?:$|[-_])"
    r"|(?:ai|gpt|llm|agent|assistant|coder|copilot|code)$"
    r"|^(?:claude|codex|gemini|aider|cursor|goose|amp|crush|qwen|grok|deepseek|"
    r"mistral|ollama|opencode|droid|cline|continue|kilo|windsurf|zed|openhands|"
    r"plandex|mentat|smol)",
    re.I)

# Names that match the heuristic but are never an agent CLI.
NOT_A_CLI = {"code", "code-insiders", "codesign", "clip", "clipboard",
             "devenv", "devcon", "aiff", "chattr", "codepage"}


def _stem(name: str) -> str:
    low = name.lower()
    for e in _exe_exts():
        if e and low.endswith(e):
            return low[: -len(e)]
    return low


def scan_clis(deep: bool = False, budget: float = 45.0) -> list[dict]:
    """Agent CLIs present on this machine, found rather than assumed.

    The name is only a prefilter; **behaviour decides**. Every candidate is
    asked for its --help, and it is listed only if that help reads like an
    agent CLI -- a model flag, a prompt, a session, a way to stop it asking
    permission. Name alone was tried first and it was useless in both
    directions: it let in gpg-agent and ssh-agent, and it would still have
    missed any CLI whose name gives nothing away.

    `deep` drops the name prefilter and probes every binary in those
    directories instead, which is the version that finds the CLI nobody here
    could have predicted the name of. It costs a subprocess per binary, so it
    is opt-in and bounded by `budget`.
    """
    found: dict[str, dict] = {}
    for name, spec in RECIPES.items():
        path = find_binary(spec["bin"])
        found[name] = {"name": name, "path": path, "installed": bool(path),
                       "how": "recipe", "install": spec["install"]}
    for name, ov in load_overrides().items():
        binary = (shlex.split(ov.get("cmd", ""))[:1] or [name])[0]
        path = find_binary(binary)
        found[name] = {"name": name, "path": path, "installed": bool(path),
                       "how": "your config", "install": ov.get("install", "")}

    exts = _exe_exts()
    seen: set[str] = set()
    deadline = time.time() + budget
    for d in search_dirs():
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for f in entries:
            if time.time() > deadline:
                break
            if exts != ("",) and not f.name.lower().endswith(exts):
                continue
            stem = _stem(f.name)
            if stem in found or stem in NOT_A_CLI or stem in seen:
                continue
            if "." in stem or len(stem) < 2:
                continue
            if not deep and not LIKELY_AGENT_RE.search(stem):
                continue
            try:
                if not f.is_file():
                    continue
            except OSError:
                continue
            seen.add(stem)
            if probe_cli(stem, str(f), timeout=8).get("agentic"):
                found[stem] = {"name": stem, "path": str(f), "installed": True,
                               "how": "found here", "install": ""}
    try:
        (cache_dir() / "clis.json").write_text(json.dumps(
            {k: v["path"] for k, v in found.items() if v["path"]}),
            encoding="utf-8")
    except OSError:
        pass
    return sorted(found.values(), key=lambda r: (not r["installed"], r["name"]))


# --------------------------------------------------------------------------
# reading a CLI's own help

def find_binary(name: str) -> str | None:
    """Resolve a CLI name to a path, PATH or not.

    PATH is not the boundary of what is installed. Codex ships into
    %LOCALAPPDATA%\\Programs\\OpenAI\\Codex\\bin, which a login shell picks up
    and an agent's inherited environment may not -- and the scan looks there
    regardless. Anything the scan resolved is remembered, so naming it later
    works even though `shutil.which` cannot see it.
    """
    if os.sep in name or "/" in name:
        return name if Path(name).exists() else None
    hit = shutil.which(name)
    if hit:
        return hit
    try:
        seen = json.loads((cache_dir() / "clis.json").read_text(encoding="utf-8"))
        path = seen.get(name)
        return path if path and Path(path).exists() else None
    except (OSError, ValueError, AttributeError):
        return None


def cache_dir() -> Path:
    """Probe results live beside the session when there is one, in the home
    directory otherwise -- `providers` and `models` are useful outside a repo."""
    try:
        root = repo_root()
        sd = state_dir(root)
        if sd.parent.exists():
            d = sd / "cache"
            d.mkdir(parents=True, exist_ok=True)
            # `providers` and `models` are useful before any session exists, and
            # they create this directory -- so exclude .partner/ here too rather
            # than only in `init`. Otherwise merely asking what CLIs are around
            # leaves an untracked directory in the user's `git status`.
            git_exclude(root)
            return d
    except OSError:
        pass
    d = Path.home() / ".partner-cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run(argv: list[str], timeout: int) -> tuple[int, str]:
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return 1, ""


# Flags a CLI offers for "stop asking me", grouped by how far they go and
# matched against the CLI's own help -- so an unknown CLI still gets launched
# without prompting, the thing that otherwise silently stalls a debate.
AUTO_FLAGS: dict[str, list[str]] = {
    "full": ["--dangerously-bypass-approvals-and-sandbox",
             "--dangerously-skip-permissions", "--yolo", "--allow-all",
             "--full-auto", "--no-sandbox", "--auto-approve-all"],
    "edits": ["--auto-edit", "--auto-approve", "--accept-edits",
              "--no-confirm", "--non-interactive", "--yes"],
}
# Valued flags: one flag, a different value per level.
AUTO_VALUED: list[tuple[str, str, str]] = [
    ("--permission-mode", "acceptEdits", "bypassPermissions"),
    ("--approval-mode", "auto_edit", "yolo"),
]
# Flags that carry the starting prompt. `-p`/`--print` are deliberately absent:
# on several CLIs they mean "run headless and exit", which would open a tab that
# finishes before anybody could type in it.
PROMPT_FLAGS = ["--prompt", "--message", "--task", "--input", "-i"]


def _usage_block(help_text: str) -> str:
    """The synopsis: the `usage:` line and its continuations, nothing after.

    A CLI's usage line says what its arguments *are*; the rest of a help page
    says what it can be asked to do. Only the first answers "can this be handed
    a task in words", which is the question that separates an agent from a tool
    an agent uses.
    """
    lines = help_text.splitlines()
    for i, ln in enumerate(lines):
        if re.match(r"\s*usage\s*:", ln, re.I):
            block = [ln]
            for nxt in lines[i + 1: i + 6]:
                if not nxt.strip():
                    break
                block.append(nxt)
            return NL.join(block)
    return help_text[:400]


def probe_cli(name: str, path: str | None = None, timeout: int = 20,
              refresh: bool = False) -> dict:
    """Derive how to drive a CLI from its own --help.

    Cached against the binary's path, size and mtime, so upgrading the CLI
    invalidates the entry by itself instead of waiting out a TTL.
    """
    binary = path or find_binary(name)
    out: dict = {"name": name, "bin": binary, "found": bool(binary),
                 "model_flag": None, "effort_flag": None, "efforts": [],
                 "auto": {}, "prompt_flag": None, "list_cmd": None,
                 "version": None, "agentic": False, "help": ""}
    if not binary:
        return out
    try:
        st = Path(binary).stat()
        key = f"{binary}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        key = binary
    cf = cache_dir() / ("probe-" + re.sub(r"[^a-z0-9]+", "_", name.lower()) + ".json")
    if not refresh and cf.exists():
        try:
            hit = json.loads(cf.read_text(encoding="utf-8"))
            if hit.get("key") == key:
                return hit["probe"]
        except (OSError, ValueError, KeyError):
            pass

    rc, help_text = _run([binary, "--help"], timeout)
    if rc != 0 or len(help_text) < 40:
        for alt in (["help"], ["-h"]):
            rc2, alt_text = _run([binary, *alt], timeout)
            if len(alt_text) > len(help_text):
                help_text = alt_text
            if rc2 == 0 and len(help_text) > 40:
                break
    out["help"] = help_text[:20000]
    low = help_text.lower()

    m = re.search(r"(--model(?:-name|-id)?)\b", help_text)
    out["model_flag"] = m.group(1) if m else ("-m" if re.search(
        r"(?<![\w-])-m\b[ ,=]*[<\[]?\s*model", low) else None)

    e = re.search(r"(--(?:model-)?(?:reasoning-effort|reasoning|effort|"
                  r"thinking(?:-budget|-level|-mode)?))\b", help_text)
    if e:
        out["effort_flag"] = e.group(1)
        tail = help_text[e.end(): e.end() + 240]
        c = re.search(r"[\[{(]\s*([a-z]+(?:\s*[|,/]\s*[a-z]+){1,5})\s*[\]})]",
                      tail, re.I)
        if c:
            out["efforts"] = [x.strip().lower()
                              for x in re.split(r"[|,/]", c.group(1)) if x.strip()]

    for level, flags in AUTO_FLAGS.items():
        for f in flags:
            if f in help_text:
                out["auto"][level] = [f]
                break
    for flag, v_edits, v_full in AUTO_VALUED:
        if flag in help_text:
            if v_edits in help_text:
                out["auto"].setdefault("edits", [flag, v_edits])
            if v_full in help_text:
                out["auto"].setdefault("full", [flag, v_full])
    # Deliberately no fallback from "edits" to the "full" flag. A CLI that only
    # advertises a sandbox-bypass flag gets nothing for `--auto edits`, and the
    # partner stops to ask in its tab -- annoying, and visible. Substituting
    # the bypass flag would silently turn a request to accept edits into a
    # request to remove the sandbox, on a CLI nobody wrote a recipe for.

    # Can this CLI be handed a task in words? A positional the usage line calls
    # a prompt (claude: `[prompt]`, codex: `[PROMPT]`) is the common shape; a
    # flag that carries one is the other. Nothing else can start a partner:
    # everything a partner does begins with being told, in a sentence, what the
    # argument is about.
    # Only the usage line counts. Searching the whole help finds `chat
    # <message>` buried among fifty other subcommands and concludes a browser
    # driver is an agent; in the usage line, a prompt positional means the
    # prompt is what the CLI is *for* -- `claude [options] [prompt]`,
    # `codex [OPTIONS] [PROMPT]`.
    positional = bool(re.search(
        r"[\[<](?:prompt|task|message|instruction|query|request)[\]>.]",
        _usage_block(low)))
    if not positional:
        for f in PROMPT_FLAGS:
            if re.search(re.escape(f) + r"\b[ ,=]*[<\[]", help_text):
                out["prompt_flag"] = f
                break
    out["takes_prompt"] = positional or bool(out["prompt_flag"])

    # Only a `models` line inside the CLI's own command list counts.
    # Matched anywhere in the help, the word "model(s)" in a flag
    # description is enough to invent a subcommand that does not exist --
    # and running it launches the agent, which then sits waiting for input
    # until the timeout expires.
    cmds = re.split(r"(?im)^\s*(?:sub)?commands\s*:", help_text)
    if len(cmds) > 1 and re.search(r"(?m)^\s+models?", cmds[-1]):
        out["list_cmd"] = [binary, "models", "list"]
    elif "--list-models" in help_text:
        out["list_cmd"] = [binary, "--list-models"]

    # Does this behave like an agent CLI at all? This is what the scan trusts
    # instead of the binary's name. An LLM word plus two of the operational
    # signals: one signal alone is met by half of /usr/bin (`ssh-agent` says
    # "agent", `gpg` says "prompt"), and all five would demand more uniformity
    # than these CLIs have.
    # "model" specifically, not any AI-adjacent word. Tools built *for* agents
    # rather than *as* one -- a browser driver, an MCP server -- talk about
    # prompts and tokens and agents all day and have no model to choose.
    llm = bool(out["model_flag"]) or "model" in low
    signals = sum(bool(s) for s in (
        out["model_flag"],
        "agent" in low or "chat" in low or "assistant" in low or "coding" in low,
        "session" in low or "conversation" in low or "resume" in low,
        bool(out["auto"]) or "approv" in low or "permission" in low,
        "mcp" in low or "tool" in low))
    # takes_prompt is the load-bearing one, and it is why a browser driver
    # built *for* agents does not qualify: it has --model, sessions and an
    # approve command, and every one of those signals fires -- but it is driven
    # by `open <url>` and `click <sel>`, so there is no way to tell it what the
    # argument is about. A partner that cannot be told is not a partner.
    out["agentic"] = bool(llm and out["takes_prompt"] and signals >= 3
                          and len(help_text) > 200)
    if out["agentic"]:
        # Only worth a subprocess for something being offered as a partner.
        _, ver = _run([binary, "--version"], timeout)
        out["version"] = (ver.strip().splitlines() or [""])[0][:80] or None
    try:
        cf.write_text(json.dumps({"key": key, "probe": out}), encoding="utf-8")
    except OSError:
        pass
    return out


# --------------------------------------------------------------------------
# CLIs the user described, kept between sessions

OVERRIDE_FILES = [
    Path.home() / ".claude" / "partner-providers.json",
    Path.home() / ".partner" / "providers.json",
]


def load_overrides() -> dict:
    """CLIs the user described once and wants to keep.

    Optional in every sense -- `--provider custom --cmd '...'` still works ad
    hoc and needs no file at all. This exists so a CLI used often does not have
    to be retyped, and so a repo can pin one for everybody working in it.
    Repo-level entries win over home-level ones.

    Each entry: {"cmd": "<template containing {prompt}>", "efforts": [...],
                 "install": "...", "note": "..."}
    """
    files = list(OVERRIDE_FILES)
    try:
        files.append(state_dir() / "providers.json")
    except OSError:
        pass
    out: dict = {}
    for f in files:
        try:
            if f.is_file():
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for k, v in data.items():
                        if isinstance(v, dict) and v.get("cmd"):
                            out[k] = {**v, "source": str(f)}
        except (OSError, ValueError):
            continue
    return out


# --------------------------------------------------------------------------
# models

# One shape, not one list per vendor. A model id is a lowercase family token
# followed by segments, at least one of which carries a version number --
# gpt-5.1, claude-opus-5, gemini-3-pro, llama3:8b, deepseek-v3, qwen2.5-coder.
# Anchoring on the shape rather than on a vendor prefix is what lets a model
# released after this file was written be found at all.
MODEL_ID_RE = re.compile(
    r"(?<![\w/.-])[a-z][a-z0-9]{1,19}(?:[-.][a-z0-9]+){1,5}(?::[a-z0-9.\-]+)?",
    re.I)

# Segments that mean tooling, packaging or plumbing named after a provider --
# claude-code, gemini-cli, gpt-4-config -- rather than a model.
NON_MODEL_SEGMENTS = {
    "code", "desktop", "plugins", "statusline", "setup", "cli", "agent", "md",
    "config", "settings", "json", "yaml", "yml", "toml", "exe", "dll", "cmd",
    "sh", "ps1", "node", "npm", "npx", "win32", "x64", "x86", "arm64", "utf",
    "sha256", "sha1", "md5", "http", "https", "www", "com", "org", "io",
    "log", "tmp", "cache", "bin", "lib", "src", "dist", "build", "py", "js",
    "ts", "css", "html", "svg", "png", "ttf", "woff", "woff2", "map", "lock",
}


# Prefixes and shapes that mean "this is a secret", not a model. Credential
# files are skipped outright (see _config_paths), but a token can turn up in a
# log line or a config comment too, and everything found here is printed into a
# transcript that every partner reads.
SECRET_RE = re.compile(r"^(sk|pk|rk|ghp|gho|ghs|github_pat|xox[abposr]|"
                       r"api|key|token|bearer|secret|aki)[-_]", re.I)


def _looks_like_model(raw: str) -> str | None:
    mid = raw.lower().strip(".,;:)]}\"'`")
    mid = re.sub(r"\.(cmd|sh|ps1|exe|json|md|ya?ml|toml|js|py|txt|lock)$", "", mid)
    if not 5 <= len(mid) <= 60:
        return None
    if not re.fullmatch(r"[a-z0-9._:-]+", mid) or SECRET_RE.match(mid):
        return None
    segs = re.split(r"[-.:]", mid)
    # No vendor names a model with a 20-character segment; every secret does.
    if any(len(s) > 20 for s in segs):
        return None
    if len(segs) < 2 or not segs[0] or not segs[0][0].isalpha():
        return None
    if re.fullmatch(r"v?\d[\d.]*", segs[0]):
        return None
    if any(s in NON_MODEL_SEGMENTS for s in segs):
        return None
    # Session ids, cache keys and UUIDs have exactly the shape of a model id --
    # a word, hyphens, digits -- and config directories are full of them. The
    # tell is a long run of pure hex, which no vendor has yet used to name
    # something a human is expected to type.
    if any(len(s) >= 8 and all(c in "0123456789abcdef" for c in s) for s in segs):
        return None
    # A version number somewhere past the family token is the load-bearing
    # rule: without it every hyphenated word in a help page is a "model".
    if not any(any(ch.isdigit() for ch in s) for s in segs[1:]):
        return None
    return mid


def _ids_in(text: str) -> set[str]:
    out = set()
    for m in MODEL_ID_RE.finditer(text):
        mid = _looks_like_model(m.group(0))
        if mid:
            out.add(mid)
    return out


def _config_paths(name: str) -> list[Path]:
    """Where a CLI called <name> conventionally keeps its settings."""
    home = Path.home()
    pats = ["config.toml", "config.json", "config.yaml", "config.yml",
            "settings.json", "config", "*.json", "*.toml"]
    out: list[Path] = []
    for base in (home / f".{name}", home / ".config" / name):
        if base.is_dir():
            for pat in pats:
                try:
                    out += [p for p in base.glob(pat) if p.is_file()]
                except OSError:
                    pass
    # Never open a credential store. Whatever is found here is printed into a
    # shared transcript, so a file whose whole purpose is holding a secret is
    # not somewhere to go looking for model names.
    out = [f for f in out if not re.search(
        r"credential|secret|token|auth|\bkeys?\b|password|cookie", f.name, re.I)]
    for f in (home / f".{name}.json", home / f".{name}rc",
              home / f".{name}" / "settings.local.json"):
        if f.is_file():
            out.append(f)
    seen, uniq = set(), []
    for p in out:
        k = str(p).lower()
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq[:12]


def _model_keyed_values(text: str) -> set[str]:
    """Values sitting under a key whose name contains "model".

    The strongest signal available offline: the user has already told the CLI
    which model to use, so whatever is written there certainly exists.
    """
    out = set()
    try:
        data = json.loads(text)

        def walk(node, key=""):
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, str(k))
            elif isinstance(node, list):
                for v in node:
                    walk(v, key)
            elif isinstance(node, str) and "model" in key.lower():
                mid = _looks_like_model(node)
                if mid:
                    out.add(mid)
        walk(data)
        return out
    except ValueError:
        pass
    for m in re.finditer(r"(?im)^[ \t]*[\"']?([\w.\-]*model[\w.\-]*)[\"']?"
                         r"\s*[:=]\s*[\"']?([^\"'\s,#\]]+)", text):
        mid = _looks_like_model(m.group(2))
        if mid:
            out.add(mid)
    return out


def _bundle_ids(binary: str, cap_mb: int = 48, deadline: float = 0.0) -> set[str]:
    """Model ids embedded in the CLI's own program files.

    Where a CLI ships its model list compiled in -- most of them do -- this is
    the closest thing available to asking it. Read in chunks against a byte and
    time budget, because these bundles run to hundreds of megabytes.
    """
    try:
        real = Path(binary).resolve()
    except OSError:
        return set()
    roots = [real.parent]
    parts = real.parts
    if "node_modules" in parts:
        i = len(parts) - 1 - parts[::-1].index("node_modules")
        roots.append(Path(*parts[: i + 2]))
    # Program text only -- never the compiled executable. Scanned as bytes, a
    # native binary yields hundreds of strings with the shape of a model id and
    # the meaning of none ("a8q.1", "about-seh1"), which buries the handful of
    # real ones. A CLI shipped as JavaScript is where this actually pays off.
    files: list[Path] = []
    for r in roots:
        for pat in ("*.js", "*.mjs", "*.cjs", "*.json"):
            try:
                files += [f for f in r.glob(pat) if f.is_file()]
            except OSError:
                pass
    budget = cap_mb * 1024 * 1024
    out: set[str] = set()
    for f in files[:16]:
        if budget <= 0 or (deadline and time.time() > deadline):
            break
        try:
            with f.open("rb") as fh:
                tail = ""
                while budget > 0:
                    chunk = fh.read(4 * 1024 * 1024)
                    if not chunk:
                        break
                    budget -= len(chunk)
                    text = tail + chunk.decode("utf-8", "replace")
                    out |= _ids_in(text)
                    tail = text[-200:]
                    if deadline and time.time() > deadline:
                        break
        except OSError:
            continue
    return out


def discover_models(provider: str, binary: str | None = None,
                    deep: bool = False, budget: float = 25.0) -> list[dict]:
    """Model ids this machine can actually name, with where each came from.

    Ordered newest-looking first. The source matters: a `models` subcommand or
    a key the user configured is evidence; a string in a help page or a bundle
    is a lead. None of it is checked against the vendor -- that is what the web
    is for, and the skill goes there when this comes back thin.
    """
    if binary is None:
        spec = resolve_provider(provider)
        binary = spec.get("bin") and find_binary(spec["bin"])
    deadline = time.time() + budget
    # id -> (confidence rank, where it came from). Rank 0 is the CLI or the
    # user naming a model outright; rank 1 is a string that merely has the
    # shape of one, found in a help page or a bundle. Both are worth showing
    # and they are not worth showing as if they were the same thing.
    hits: dict[str, tuple[int, str]] = {}

    def add(ids, source: str, rank: int = 1) -> None:
        for i in ids:
            if rank < hits.get(i, (9, ""))[0]:
                hits[i] = (rank, source)

    if binary:
        pr = probe_cli(provider, binary)
        if pr.get("list_cmd"):
            # Short leash. "models" in a help page is not proof of a `models`
            # subcommand: on a CLI that has no such command, this launches the
            # agent itself, which then sits waiting for input until the timeout.
            rc, txt = _run(pr["list_cmd"], 6)
            if rc == 0:
                add(_ids_in(txt), "the CLI's own model list", rank=0)
        add(_ids_in(pr.get("help") or ""), "the CLI's help")

    # Config files split into two treatments by size. Walking JSON for keys
    # named "model" is cheap and precise, so every file gets it; the broad
    # regex over the whole text is neither, so it is spent only on the small
    # files and within a byte budget. A 4 MB .claude.json scanned both ways
    # took twenty seconds and returned session ids.
    loose_budget = 4_000_000
    for f in _config_paths(provider):
        if time.time() > deadline:
            break
        try:
            size = f.stat().st_size
            if size > 8_000_000:
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        add(_model_keyed_values(text), f"a model setting in your {f.name}", rank=0)
        if size <= 512_000 and loose_budget > 0:
            loose_budget -= size
            add(_ids_in(text), f"text in your {f.name}")

    # The bundle scan is the expensive one, so it runs when the cheap sources
    # came back thin, or when it was asked for.
    # Only scan a bundle that belongs to this provider. Under a name from the
    # user's providers.json the binary is whatever their template wraps, and
    # reporting that CLI's ids as this provider's would be a plain lie.
    own = binary and Path(binary).stem.lower() == provider.lower()
    if own and (deep or len(hits) < 3) and time.time() < deadline:
        add(_bundle_ids(binary, deadline=deadline), "the CLI's own bundle")

    return [{"id": mid, "source": src, "confidence":
             "named" if rank == 0 else "mentioned", **tier_of(mid)}
            for mid, (rank, src) in sorted(
                hits.items(), key=lambda kv: (kv[1][0], model_rank(kv[0])))]


# What a model's *name* implies about arguing with it. Shapes, not ids, so a
# model released next month is described correctly the first time it appears.
# Advisory: it reports what each vendor's naming convention has meant so far.
TIER_RULES: list[tuple[re.Pattern, str, str, str]] = [
    (re.compile(r"(?:^|[-_.])(opus|ultra|max|large|xl|heavy)(?:$|[-_.\d])", re.I),
     "deep", "high",
     "top of its family: deepest reasoning, strongest challenger, slowest"),
    (re.compile(r"(?:^|[-_.])(haiku|flash|mini|lite|nano|small|tiny|instant|"
                r"fast|air|\d+b)(?:$|[-_.\d])", re.I),
     "fast", "low",
     "fast and cheap; concedes too easily to be much of an opponent"),
    (re.compile(r"(?:^|[-_.])(codex|coder|code)(?:$|[-_.\d])", re.I),
     "code", "high",
     "code-tuned: sharper on diffs than on open design questions"),
    (re.compile(r"(?:^|[-_.])(think|thinking|reason|reasoning|r1|o[1-9])(?:$|[-_.\d])",
                re.I),
     "reasoning", "high",
     "reasoning-tuned: slow, and hard to talk out of a position"),
    (re.compile(r"(?:^|[-_.])(sonnet|pro|medium|standard|turbo|plus)(?:$|[-_.\d])",
                re.I),
     "balanced", "high",
     "the balanced tier: fast enough to argue with in real time"),
]


def tier_of(mid: str) -> dict:
    for rx, tier, effort, note in TIER_RULES:
        if rx.search(mid):
            return {"tier": tier, "effort": effort, "note": note}
    return {"tier": "general", "effort": "high",
            "note": "no tier signal in the name -- try it and see"}


def model_rank(mid: str) -> tuple:
    """Newest-looking first: version numbers in order, then any date stamp.

    In order, not the largest -- claude-opus-4-7 has a 7 in it and is older
    than claude-opus-5. The leading number is the generation; the rest are
    point releases within it.
    """
    nums = [-float(n) for n in
            re.findall(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])", mid) if len(n) < 5]
    date = max([int(d) for d in re.findall(r"\b(20\d{6})\b", mid)] or [0])
    return (tuple(nums), -date, mid)


# --------------------------------------------------------------------------
# validation

def cli_runs(binary: str) -> bool:
    """Does the binary actually start? Catches broken or half-finished installs."""
    rc, _ = _run([find_binary(binary) or binary, "--version"], 30)
    return rc == 0


def resolve_provider(name: str, cmd: str | None = None) -> dict:
    """Everything needed to drive one CLI, from whichever source knows it.

    Recipe, then the user's own config, then the CLI's own help. That last
    fallback is why naming a CLI nobody here has heard of still produces a
    working tab instead of an error.
    """
    if name == "custom":
        return {"kind": "custom", "bin": (shlex.split(cmd or "")[:1] or [""])[0],
                "efforts": set(EFFORT_HINT), "effort": "template",
                "install": "", "cmd": cmd}
    ov = load_overrides().get(name)
    if ov:
        return {"kind": "config", "bin": (shlex.split(ov["cmd"])[:1] or [name])[0],
                "efforts": set(ov.get("efforts") or EFFORT_HINT),
                "effort": "template", "install": ov.get("install", ""),
                "cmd": ov["cmd"], "note": ov.get("note", ""),
                "source": ov.get("source", "")}
    if name in RECIPES:
        return {"kind": "recipe", **RECIPES[name]}
    pr = probe_cli(name)
    return {"kind": "probed", "bin": pr["bin"] or name, "probe": pr,
            "efforts": set(pr["efforts"]) if pr["efforts"] else set(EFFORT_HINT),
            "effort": "flag" if pr["effort_flag"] else "prompt",
            "install": "", "found": pr["found"]}


def validate(provider: str, model: str | None, effort: str | None,
             cmd: str | None, force: bool = False) -> dict:
    """Check a partner's configuration before a tab is opened.

    Two severities, and the split is the point. A **problem** is something no
    amount of insisting fixes -- a CLI that is not installed, an effort value
    the API will reject -- and it stops the spawn. A **caution** is this script
    not recognising something, which is not evidence of anything: model names
    move faster than any check here, so an unrecognised model is said out loud
    and then used. Refusing until --force meant a model released last week
    needed a flag to try.
    """
    problems: list[str] = []
    cautions: list[str] = []

    if provider == "custom" and not cmd:
        return {"problems": ["--provider custom needs --cmd "
                             "'<template containing {prompt}>'"],
                "cautions": cautions}

    spec = resolve_provider(provider, cmd)
    binary = spec.get("bin") or ""
    if not binary:
        problems.append(f"no binary to run for '{provider}'. "
                        f"Give one with --cmd '<template>'.")
    elif not find_binary(binary):
        fix = (f"Install it with:  {spec['install']}" if spec.get("install")
               else "Install it, or point --cmd at the right binary.")
        near = [c["name"] for c in scan_clis() if c["installed"]][:8]
        problems.append(f"'{binary}' is not installed or not on PATH. {fix}"
                        + (f"{NL}    Installed here: {', '.join(near)}" if near else ""))
    elif not cli_runs(binary):
        cautions.append(f"`{binary} --version` did not exit cleanly. It may still "
                        f"work, but a broken install shows up as a tab that "
                        f"opens and closes.")

    if effort:
        if effort not in spec["efforts"]:
            valid = ", ".join(sorted(spec["efforts"]))
            extra = ""
            if spec.get("effort") == "flag" and effort == "max":
                extra = " ('max' is this skill's own level; 'high' is the real flag.)"
            problems.append(f"effort '{effort}' is not accepted by {provider}. "
                            f"Use one of: {valid}.{extra}")
        elif spec.get("effort") == "prompt":
            cautions.append(f"{provider} exposes no reasoning-effort flag, so "
                            f"--effort {effort} goes into the briefing as an "
                            f"instruction rather than a setting.")

    if model and not force and binary and find_binary(binary):
        known = {m["id"] for m in discover_models(provider, find_binary(binary),
                                                  budget=12)}
        if known and model not in known:
            sample = ", ".join(sorted(known)[:6])
            cautions.append(
                f"'{model}' is not among the ids this machine names for "
                f"{provider} ({sample}...). Not proof it is wrong -- a new model "
                f"is mentioned nowhere locally until it is used once -- but "
                f"worth checking the spelling.")
        elif not known:
            cautions.append(f"nothing on this machine names a {provider} model, "
                            f"so '{model}' could not be cross-checked.")

    if spec.get("kind") == "probed" and binary:
        pr = spec.get("probe", {})
        got = [pr[k] for k in ("model_flag", "effort_flag") if pr.get(k)]
        auto = "/".join(pr.get("auto", {}))
        derived = ", ".join(got) if got else "no model or effort flag found"
        if auto:
            derived += "; auto: " + auto
        cautions.append(
            f"no hand-verified recipe for '{provider}': its flags were read from "
            f"`{binary} --help` ({derived}). If the tab misbehaves, pass the "
            f"exact command with --cmd instead.")
    return {"problems": problems, "cautions": cautions}


def cmd_check(args) -> int:
    v = validate(args.provider, args.model, args.effort, args.cmd, args.force)
    problems, cautions = v["problems"], v["cautions"]
    bits = [args.provider]
    if args.model:
        bits.append(args.model)
    if args.effort:
        bits.append(f"effort={args.effort}")
    head = ("cannot start this partner:" if problems
            else "ok: " + " / ".join(bits))
    lines = [head]
    lines += [f"  - {p}" for p in problems]
    if cautions:
        # Printed on success too. These are the things worth knowing before the
        # tab opens, not reasons to stop.
        lines.append("  worth knowing:")
        lines += [f"  - {c}" for c in cautions]
    emit(args, {"ok": not problems, **v}, NL.join(lines))
    return 1 if problems else 0


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


def _probed_tui(pr: dict, c: dict) -> list[str]:
    """Drive a CLI nobody wrote a recipe for, from what its --help admitted to.

    Conservative on purpose: a flag that was not found is a flag that is not
    passed. A partner launched with no model flag runs on the CLI's own default
    model, which is a working tab; a partner launched with a guessed flag is a
    tab that prints a usage error and closes.
    """
    argv = [pr["bin"] or pr["name"]]
    if c["model"] and pr.get("model_flag"):
        argv += [pr["model_flag"], c["model"]]
    if c["effort"] and pr.get("effort_flag"):
        argv += [pr["effort_flag"], c["effort"]]
    argv += pr.get("auto", {}).get(c["auto"], [])
    if pr.get("prompt_flag"):
        argv += [pr["prompt_flag"], c["prompt"]]
        return argv
    return argv + [c["prompt"]]


def build_tui(p: dict, prompt: str) -> list[str]:
    c = {"prompt": prompt, "model": p.get("model") or "",
         "effort": p.get("effort") or "", "auto": p.get("auto") or "edits"}
    prov = p["provider"]
    if prov == "custom":
        return _custom_tui(p.get("cmd") or "", c)
    spec = resolve_provider(prov, p.get("cmd"))
    if spec["kind"] == "recipe":
        return spec["tui"](c)
    if spec["kind"] == "config":
        # A CLI the user described in their own providers.json: same template
        # language as --cmd, so there is one substitution path, not two.
        return _custom_tui(spec["cmd"], c)
    if spec.get("found"):
        return _probed_tui(spec["probe"], c)
    raise SystemExit(f"{prov!r} is not installed and has no --cmd template")


def provider_spec(prov: str) -> dict:
    return resolve_provider(prov)


# --------------------------------------------------------------------------
# terminal tabs
# --------------------------------------------------------------------------
# Quoting a nested agent command through wt.exe / osascript / gnome-terminal is
# where cross-platform launchers normally rot. We sidestep it entirely: write
# the real command into a per-agent run script, then every terminal only ever
# has to run one plain file path.

def write_runner(pdir: Path, argv: list[str], cwd: Path) -> Path:
    # Export PARTNER_ID into the tab's own environment, not just the wrappers'.
    # A Claude Code tab agent and its hooks then resolve identity the same way
    # `me_id()` does -- without it the Stop hook there would fall back to
    # roster["self"] and treat every tab as the session agent.
    pid = pdir.name
    if os.name == "nt":
        r = pdir / "run.cmd"
        r.write_text("@echo off\r\ncd /d \"{}\"\r\nset PARTNER_ID={}\r\n{}\r\n".format(
            cwd, pid, subprocess.list2cmdline(argv)), encoding="utf-8")
    else:
        r = pdir / "run.sh"
        r.write_text("#!/usr/bin/env bash{}cd {}{}export PARTNER_ID={}{}exec {}{}".format(
            NL, shlex.quote(str(cwd)), NL, shlex.quote(pid), NL, shlex.join(argv), NL),
            encoding="utf-8")
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

You are one of several AI agents working on the repository at {cwd}, and we are
peers. Nobody coordinates, nobody reports to anybody, and every one of us --
including whoever started this session -- reads a briefing exactly like this
one. {peers}

The human can address any of us at any time, in whichever tab they are looking
at. {human_input} When they do, they are talking to *you*.

## Talking to the others

We share one append-only transcript: {chat}
These commands are yours; the wrapper already knows which agent you are, so
never pass an id:

    Wait until somebody addresses you (blocks, returns within {timeout}s;
    the session agent runs this in the background instead -- see "Your loop"):
        {run} wait

    Say something -- reply, challenge, or raise a new point:
        {run} send --to @all --text "..."
        {run} send --to {example_peer} --text "..."      (one agent)

    Re-read the transcript at any time (does not consume anything):
        {run} read --peek

`wait` and `read` never return your own messages -- raising a point cannot
trigger you to answer it yourself.

    See who is here and who holds the write baton:
        {run} list

    Bring in another partner, if a question needs an angle none of us has:
        {run} spawn --provider codex --model gpt-5-codex --effort high

Any of us can spawn a partner. Any of us can hold the baton. There is no role
here that only one agent has.

## The write baton -- read this twice

Only one of us edits files at a time -- whichever agent the human most recently
gave an instruction to. Right now that is **{baton}**. It can be any of us and it
moves whenever the human turns to someone else, so read the holder off the banner
on every `wait` and `read` rather than trusting your memory of it.

**The moment the human gives an instruction to you, run this first:**

    {run} claim

That moves the baton to you, and it is the only way the others learn the human
has turned to you. Claim, then do the work.

The rule in both directions:

- An instruction from **the human, to you** -> `claim`, then act. It is yours.
- A message from **another agent** -> do NOT claim, do NOT edit. What they are
  proposing is advice, not the human's instruction. Argue it, refine it, and
  when the group has a view, hand it to whoever holds the baton.

While the baton is not yours you are still in the debate, not on the bench:
thrash the question out with the other advisors directly -- `{run} send --to
<id>` any of them, not only `@all`. Several agents converging on a recommendation
and handing it to the baton holder is exactly how this is meant to work.

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
{loop}
## Before you start
{handoff}
{start}
"""

LOOP_TAB = """
This loop is the whole job. Never end your turn until you are stopped -- after
every reply, every timeout, every answer to the human, run `wait` again. A tab
that stops looping is dead to the others.

1. `{run} wait`. Read the banner it prints: it names the baton holder. What you
   do this turn depends on whether that is you.

2. YOU HOLD THE BATON -- you are the one who edits.
   a. The human just gave you an instruction: `{run} claim` if the banner does
      not already show you, then state your position in one line,
      `{run} send --to @all --wait 240`, weigh the replies against the code,
      rebut or converge in two exchanges (hand a real deadlock to the human).
      Make the change, report it, and `{run} send --to @all` one line on what
      changed. Skip the debate only for mechanical requests ("run the tests").
   b. A message came from another agent while you work: it is advice on what you
      are doing. Fold it in, or push back with `{run} send`. Act once the
      discussion settles -- you do not need unanimity.

3. YOU DO NOT HOLD THE BATON -- you advise, and you argue it out with the others.
   a. A message addressed you or @all: verify it against the code, cite
      path:line, then `{run} send` your answer to the sender or @all.
   b. Raise your own points too, to any agent, without being asked:
      `{run} send --to <id> "..."`. Several agents settling a question among
      themselves and handing the baton holder one recommendation is the design,
      not a detour.
   c. Never `{run} claim`, never edit. Only the human moves the baton.

4. Timed out with nothing: back to step 1.

5. System message saying you were stopped: say goodbye, exit the loop.

**Whatever happened, you end at step 1.** A discussion reaching its conclusion
is not an exit -- neither is the baton moving to somebody else. Those are the
two moments an agent is most tempted to call it done, and they are exactly when
the group is about to say the thing you need to hear. Losing the baton demotes
you to advisor; it does not excuse you from listening.

Unsure what was already said? `{run} read` or open the transcript before you
reply -- never from stale memory. You never receive your own messages, so you
cannot answer yourself.

A Stop hook holds you to both halves of that: it blocks the turn from ending
while a message to you or `@all` is unanswered, and again if no `wait` is in
flight for you. Answer, go back to `wait`, and it lets you go.
"""

LOOP_SESSION = """
You run inside Claude Code and reach the human through your own harness, not a
terminal tab. That changes only *how you wait*, not the loop.

Never run `wait` in the foreground -- it would block your harness and stop the
human talking to you. Run it as a BACKGROUND shell command instead (the Bash
tool with run_in_background: true). Claude Code re-invokes you when it returns --
someone spoke, or it timed out. Keep exactly ONE background `wait` in flight and
re-arm it at the end of every turn.

The human is often working in another agent's tab, not talking to you. Your
background `wait` is the only way you hear what is said there; without it you go
idle whenever the conversation moves to a tab. `wait` never returns your own
messages, so you will not answer yourself.

Every turn:

1. START: `{run} read`. Note the baton holder from the banner -- your role
   depends on whether it is you.

2. YOU HOLD THE BATON -- you are the one who edits.
   a. The human just gave you an instruction: cancel the background `wait` first
      (so it does not double-deliver), then `{run} claim`, state your position
      in one line, `{run} send --to @all --wait 240`, weigh the replies against
      the code, converge or hand a deadlock back. Make the change, report to the
      human, `{run} send --to @all` one line on what changed.
   b. A message came from another agent about what you are doing: fold it in or
      push back with `{run} send`. Act once the discussion settles.

3. YOU DO NOT HOLD THE BATON -- you advise and discuss like any other agent.
   a. Answer whatever addressed you: verify against the code, cite path:line,
      `{run} send` to the sender or @all.
   b. Open threads with other agents yourself when you have a point to make.
   c. Never `{run} claim`, never edit. Only the human moves the baton.
   d. If `read` shows nothing new, you already handled it.

4. END of every turn, always: start one `{run} wait --timeout 600` as a
   background command, then end your turn. When it returns, go to step 1.

Step 4 is not optional and has no exceptions. A discussion reaching its
conclusion is not an exit, and neither is the baton moving to somebody else --
those are the two moments you are most tempted to call it done, and exactly when
the group is about to say the thing you need to hear. Losing the baton demotes
you to advisor; it does not excuse you from listening.

Stop only on a system message saying you were stopped. A Stop hook holds you to
both halves of this: it blocks the turn from ending while a message to you or
`@all` is unanswered, and again if no `wait` is in flight for you.
"""


def build_seed(pid: str, roster: dict, root: Path, sd: Path,
               script: Path, has_handoff: bool, kind: str = "tab") -> str:
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
    run = write_wrappers(sd, script, pid)
    loop = (LOOP_SESSION if kind == "session" else LOOP_TAB).format(run=run)
    human_input = ("They type into your terminal tab."
                   if kind == "tab" else
                   "They reach you through your own Claude Code harness, not a "
                   "terminal tab -- and they may instead be typing in another "
                   "agent's tab, which is why your loop keeps a background `wait`.")
    start = ("Now run step 1." if kind == "tab"
             else "Run step 1 (`read`) now, then step 4 -- arm the background "
                  "`wait` and end your turn. You will be re-invoked when the "
                  "human or a partner needs you.")
    return SEED.format(
        id=pid, cwd=root, peers=peers, chat=sd / "chat.md", run=run,
        timeout=WAIT_TIMEOUT, baton=baton_of(roster),
        other=", ".join(others) or "the others",
        example_peer=others[0] if others else "p2",
        effort=effort, handoff=handoff, loop=loop,
        human_input=human_input, start=start)


def write_briefing(sd: Path, pid: str, roster: dict, root: Path, script: Path,
                   context: str | None, kind: str = "tab") -> Path:
    """Write one agent's handoff.md and seed.md.

    Shared by `init` and `spawn` on purpose: the moment the agent that starts
    the session is briefed by different code than the ones it spawns, the two
    drift and stop being peers.
    """
    pdir = sd / pid
    pdir.mkdir(parents=True, exist_ok=True)
    brief = []
    if context:
        ctx = Path(context)
        brief.append(ctx.read_text(encoding="utf-8", errors="replace")
                     if ctx.exists() else context)
    snap = repo_snapshot(root)
    if snap:
        brief.append("## Working tree at the moment you joined" + NL * 2 + snap)
    hist = tail_msgs(sd, 20)
    if hist:
        brief.append("## Debate you are joining, most recent last" + NL * 2
                     + render(hist))
    if brief:
        (pdir / "handoff.md").write_text((NL * 2).join(brief), encoding="utf-8")
    seed = pdir / "seed.md"
    seed.write_text(build_seed(pid, roster, root, sd, script, bool(brief), kind),
                    encoding="utf-8")
    return seed


# --------------------------------------------------------------------------
# spawning
# --------------------------------------------------------------------------

def cmd_spawn(args) -> int:
    root = repo_root()
    sd = state_dir(root)
    if getattr(args, "fresh", False):
        # A new arrangement starts clean, but nothing is thrown away: the old
        # session moves under sessions/ and can be resumed with its agents.
        archived = archive_current(sd)
        if archived and not getattr(args, "quiet", False):
            print(f"archived previous session as {archived['id']} "
                  f"({len(archived['agents'])} agents, "
                  f"{archived['messages']} messages)")
    # Register the caller as part of spawning, so "a partner is running" is one
    # command rather than two. Splitting them left sessions that registered
    # themselves, stopped, and never created anybody.
    cmd_init(argparse.Namespace(
        json=False, quiet=True,
        me_provider=args.me_provider, me_model=args.me_model,
        me_effort=args.me_effort, me_auto=args.me_auto, me_name=None,
        context=None))

    roster = load_roster(sd)
    pid = args.name or f"p{len(roster['partners']) + 1}"
    if pid in roster["partners"] and not args.replace:
        emit(args, {"error": "exists"}, f"agent {pid!r} already exists; pass --replace")
        return 1

    # Validate before anything is written or a tab is opened. A bad flag caught
    # here is one message; caught at launch it is a tab that flashes an error
    # and vanishes.
    v = validate(args.provider, args.model, args.effort, args.cmd, args.force)
    if v["problems"]:
        emit(args, {"error": "invalid", **v},
             f"cannot start {pid}:" + NL
             + NL.join(f"  - {p}" for p in v["problems"]))
        return 1
    if v["cautions"] and not getattr(args, "quiet", False):
        # Said, not enforced. Each one is this script failing to recognise
        # something rather than knowing it is wrong -- which is exactly the
        # kind of thing to put in front of the user instead of acting on.
        print(f"starting {pid} anyway, but note:")
        for c in v["cautions"]:
            print(f"  - {c}")

    pdir = sd / pid
    pdir.mkdir(parents=True, exist_ok=True)
    entry = {"id": pid, "provider": args.provider, "model": args.model,
             "effort": args.effort, "cmd": args.cmd, "auto": args.auto,
             "kind": "tab", "status": "running", "started": utcnow()}
    roster["partners"][pid] = entry

    # Same writer `init` uses for the agent that started the session. Briefing
    # them through different code is how peers quietly stop being peers.
    seed_f = write_briefing(sd, pid, roster, root, Path(__file__).resolve(),
                            args.context, kind="tab")

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

    # Tell the others somebody arrived. Without this a new agent is invisible
    # until it happens to speak, and the existing ones cannot address it.
    spec_bits = args.provider + (f"/{args.model}" if args.model else "")
    append_msg(sd, "system", "@all",
               f"**{pid}** ({spec_bits}) joined, started by **{me_id(roster)}**. "
               f"It has been briefed on the work so far. Address it as `{pid}`.",
               baton_of(roster))

    # Start the newcomer's cursor at the end of the transcript. Everything said
    # so far is already in its handoff.md; leaving the cursor at zero makes its
    # very first `wait` return the whole backlog at once, which it then tries to
    # answer instead of blocking for the question actually being put to it --
    # and usually ends its turn without looping back. `resume` does the same
    # thing for the same reason.
    chat_now = sd / "chat.md"
    (pdir / "cursor").write_text(
        str(chat_now.stat().st_size if chat_now.exists() else 0),
        encoding="utf-8")

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
    live = [k for k, v in roster["partners"].items() if v.get("status") == "running"]
    if label:
        msg += (f"{NL}{len(live)} agents now running: {', '.join(live)}. "
                f"You are {me_id(roster)}, and you hold the baton.")
    emit(args, {**entry, "manual_command": manual, "seed": str(seed_f),
                "running": live}, msg)
    return 0


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------
# A session is one arrangement of agents plus the transcript they produced.
# Starting a new one never destroys the old: it is moved under sessions/ whole,
# so it can be brought back with its agents and its argument intact.

def sessions_dir(sd: Path) -> Path:
    return sd / "sessions"


def agent_dirs(sd: Path) -> list[Path]:
    """Per-agent directories in the live session, skipping sessions/ itself."""
    return [d for d in sd.iterdir()
            if d.is_dir() and d.name != "sessions" and (d / "seed.md").exists()]


def session_label(sd: Path, roster: dict) -> str:
    """A human-recognisable name: the first real thing anybody said."""
    for m in tail_msgs(sd, 200):
        if m["from"] != "system":
            line = " ".join(m["body"].split())
            return (line[:70] + "...") if len(line) > 70 else line
    ids = ", ".join(roster.get("partners", {}))
    return f"no discussion ({ids})" if ids else "empty"


def session_has_substance(sd: Path, roster: dict) -> bool:
    """Whether the live session is one anybody would want back.

    A roster holding only this session -- no spawned partners -- with nothing
    said beyond system notices is not a session that happened. Archiving it
    just files an empty transcript away and forces a needless re-init on the
    next spawn. A bare `/partner` from a cold session leans on this: `--fresh`
    then archives nothing and simply adds the first partner.
    """
    partners = roster.get("partners", {})
    if any((p.get("kind") or "tab") != "session" for p in partners.values()):
        return True
    return any(m["from"] != "system" for m in tail_msgs(sd, 1000))


def archive_current(sd: Path, label: str | None = None) -> dict | None:
    """Move the live session under sessions/ and leave the slate clean."""
    roster = load_roster(sd)
    chat = sd / "chat.md"
    if not roster["partners"] and not chat.exists():
        return None
    if not session_has_substance(sd, roster):
        return None

    sid = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = sessions_dir(sd) / sid
    n = 2
    while dest.exists():
        dest = sessions_dir(sd) / f"{sid}-{n}"
        n += 1
    dest.mkdir(parents=True)

    msgs = tail_msgs(sd, 100000)
    meta = {
        "id": dest.name,
        "archived": utcnow(),
        "label": label or session_label(sd, roster),
        "messages": len(msgs),
        "agents": {pid: {k: p.get(k) for k in
                         ("provider", "model", "effort", "auto", "cmd", "kind")}
                   for pid, p in roster["partners"].items()},
    }
    (dest / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    for item in [sd / "roster.json", chat]:
        if item.exists():
            shutil.move(str(item), str(dest / item.name))
    for d in agent_dirs(sd):
        shutil.move(str(d), str(dest / d.name))
    return meta


def cmd_archive(args) -> int:
    sd = state_dir()
    meta = archive_current(sd, args.label)
    if not meta:
        emit(args, {"archived": None}, "nothing to archive")
        return 0
    emit(args, meta,
         f"archived as {meta['id']} "
         f"({len(meta['agents'])} agents, {meta['messages']} messages)"
         f"{NL}  {meta['label']}")
    return 0


def read_sessions(sd: Path) -> list[dict]:
    root = sessions_dir(sd)
    if not root.exists():
        return []
    out = []
    for d in sorted(root.iterdir(), reverse=True):
        f = d / "meta.json"
        if not f.exists():
            continue
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return out


def cmd_sessions(args) -> int:
    sd = state_dir()
    past = read_sessions(sd)
    roster = load_roster(sd)
    live = {"id": "current", "agents": roster.get("partners", {}),
            "messages": len(tail_msgs(sd, 100000)),
            "label": session_label(sd, roster) if roster.get("partners") else "empty"}
    if args.json:
        print(json.dumps({"current": live, "archived": past}, indent=2))
        return 0
    print(f"current   {len(live['agents'])} agents, {live['messages']} messages"
          f"   {live['label']}")
    if not past:
        print("no archived sessions")
        return 0
    for m in past:
        ids = ", ".join(m.get("agents", {})) or "-"
        print(f"{m['id']}   {len(m.get('agents', {}))} agents, "
              f"{m.get('messages', 0)} messages   [{ids}]")
        print(f"{'':17} {m.get('label', '')}")
    return 0


def relaunch(sd: Path, pid: str, roster: dict, root: Path, script: Path,
             no_tab: bool = False) -> tuple[str, Path]:
    """Start an agent from its roster entry rather than from CLI arguments.

    Resuming has to rebuild agents it did not create, so the launch path takes
    a roster entry as its input; `spawn` fills one in and calls the same code.
    """
    entry = roster["partners"][pid]
    pdir = sd / pid
    pdir.mkdir(parents=True, exist_ok=True)
    seed_f = write_briefing(sd, pid, roster, root, script, None,
                            kind=entry.get("kind") or "tab")
    boot = (f"Read {seed_f} and follow it exactly. It explains who you are, "
            f"who you are working with, and how to talk to them. Begin now.")
    argv = build_tui(entry, boot)
    runner = write_runner(pdir, argv, root)
    label = "" if no_tab else open_tab(f"partner:{pid}", runner, root)
    entry["tab"] = label
    entry["runner"] = str(runner)
    entry["status"] = "running"
    return label, runner


def cmd_resume(args) -> int:
    """Bring a session back, or restart the agents of the current one.

    Either way every agent is recreated and re-briefed from the transcript, so
    the argument continues where it stopped instead of starting over.
    """
    root = repo_root()
    sd = state_dir(root)
    script = Path(__file__).resolve()

    if args.session:
        src = sessions_dir(sd) / args.session
        if not src.exists():
            known = ", ".join(m["id"] for m in read_sessions(sd)) or "none"
            emit(args, {"error": "unknown"},
                 f"no session '{args.session}'. Archived: {known}")
            return 1
        archive_current(sd)                       # never lose what was live
        for item in src.iterdir():
            if item.name == "meta.json":
                continue
            target = sd / item.name
            if target.exists():
                shutil.rmtree(target) if target.is_dir() else target.unlink()
            shutil.copytree(str(item), str(target)) if item.is_dir() \
                else shutil.copy2(str(item), str(target))

    roster = load_roster(sd)
    if not roster["partners"]:
        emit(args, {"error": "empty"},
             "nothing to resume -- start one with: partner.py spawn --provider ...")
        return 1

    # Whoever is running this adopts the session-kind entry; a restored roster
    # would otherwise still name the agent that created it.
    me = next((p for p, v in roster["partners"].items()
               if (v.get("kind") or ("session" if v.get("tab") == "this session"
                                     else "tab")) == "session"), None)
    if me:
        roster["self"] = me
        roster["partners"][me]["kind"] = "session"
        roster["partners"][me]["status"] = "running"
        roster["partners"][me]["tab"] = "this session"
        write_wrappers(sd, script, me)
        write_briefing(sd, me, roster, root, script, None, kind="session")

    # History belongs in the briefing, not the inbox: an agent that finds 200
    # old messages waiting will try to answer all of them.
    end = (sd / "chat.md").stat().st_size if (sd / "chat.md").exists() else 0
    started = []
    for pid, entry in roster["partners"].items():
        if pid == me:
            continue
        entry.setdefault("kind", "tab")
        label, _ = relaunch(sd, pid, roster, root, script, args.no_tab)
        started.append(f"{pid} ({entry.get('provider')}"
                       f"{'/' + entry['model'] if entry.get('model') else ''})"
                       f"{' -> ' + label if label else ''}")
        (sd / pid / "cursor").write_text(str(end), encoding="utf-8")

    write_wrappers(sd, script)
    save_roster(sd, roster)
    what = f"session {args.session}" if args.session else "the current session"
    append_msg(sd, "system", "@all",
               f"{what.capitalize()} resumed by **{me or me_id(roster)}**. "
               f"Everyone has been re-briefed from the transcript above -- pick up "
               f"where we stopped rather than starting over.", baton_of(roster))
    emit(args, {"resumed": args.session or "current", "started": started,
                "self": me},
         f"resumed {what}" + NL + NL.join(f"  {x}" for x in started))
    return 0


# --------------------------------------------------------------------------
# liveness
# --------------------------------------------------------------------------
# roster.json says an agent is "running" because nothing has told it otherwise.
# Close the tab and the claim survives, so it cannot be trusted to decide
# whether to add a partner or start over. Liveness is evidence instead: every
# agent stamps <id>/lastseen each time it acts, so being alive means having
# done something recently rather than having been started once.

LIVE_WINDOW = 360          # seconds; `wait` returns every 120s and loops


def touch_seen(sd: Path, pid: str) -> None:
    try:
        d = sd / pid
        d.mkdir(parents=True, exist_ok=True)
        (d / "lastseen").write_text(utcnow(), encoding="utf-8")
    except OSError:
        pass                # a heartbeat is never worth failing a command over


def _nag_throttled(marker: Path, window: int) -> bool:
    """True when `marker` was stamped within `window` seconds.

    The Stop hook uses this for warnings that are not tied to a new message, so
    an agent that cannot act on one is told at a bounded rate rather than being
    walled in by a block it can never clear.
    """
    now = time.time()
    try:
        if marker.exists() and now - float(
                marker.read_text(encoding="utf-8").strip()) < window:
            return True
    except (OSError, ValueError):
        pass
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(now), encoding="utf-8")
    except OSError:
        pass
    return False


def is_listening(sd: Path, roster: dict, who: str) -> bool:
    """Is a `wait` actually in flight for this agent right now?

    `wait` holds <id>/waiting for as long as it polls, stamped with its own
    deadline, so this answers "listening" rather than "did something recently"
    -- which `send`, `read` and `claim` would all satisfy just as well. That
    distinction is the whole point: an agent that has just replied and is about
    to stop looks busy by every other measure.
    """
    mark = sd / who / "waiting"
    try:
        if mark.exists() and time.time() < float(
                mark.read_text(encoding="utf-8").strip()) + 30:
            return True
    except (OSError, ValueError):
        pass
    # The session agent backgrounds its `wait`, so the marker write can race a
    # Stop that fires immediately after. A lastseen inside the live window still
    # proves something is polling on its behalf.
    entry = roster["partners"].get(who) or {}
    if (entry.get("kind") or "tab") == "session":
        age = _age_seconds(last_seen(sd, who))
        return age is not None and age <= LIVE_WINDOW
    return False


def last_seen(sd: Path, pid: str) -> str | None:
    f = sd / pid / "lastseen"
    if not f.exists():
        return None
    try:
        return f.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _age_seconds(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        t = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - t).total_seconds()


def orca_tab_titles() -> set[str] | None:
    """Titles of live Orca terminals, or None when Orca cannot answer.

    Inside Orca this is better evidence than a heartbeat: it reports the tab
    itself rather than what the agent last did in it.
    """
    orca = orca_bin()
    if not orca:
        return None
    try:
        r = subprocess.run([orca, "terminal", "list", "--json"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
        if r.returncode != 0:
            return None
        res = json.loads(r.stdout).get("result", {})
        terms = res.get("terminals") or res.get("items") or []
        return {str(t.get("title", "")) for t in terms}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, AttributeError):
        return None


def liveness(sd: Path, roster: dict, window: int = LIVE_WINDOW) -> dict:
    """Classify every agent as live, stale or stopped, and say why."""
    me = me_id(roster)
    titles = orca_tab_titles()
    out = {}
    for pid, entry in roster.get("partners", {}).items():
        if entry.get("status") == "stopped":
            out[pid] = {"state": "stopped", "why": "stopped explicitly", "age": None}
            continue
        if pid == me or (entry.get("kind") == "session"):
            out[pid] = {"state": "live", "why": "this session", "age": 0}
            continue
        age = _age_seconds(last_seen(sd, pid))
        # A fresh heartbeat outranks everything else: the agent demonstrably
        # ran a command just now. Checking the tab list first would call it
        # dead whenever the tab was renamed, started outside Orca, or launched
        # with --no-tab -- a false negative that throws away a working partner.
        if age is not None and age <= window:
            out[pid] = {"state": "live", "age": age,
                        "why": f"acted {int(age)}s ago"}
        elif titles is not None and f"partner:{pid}" not in titles:
            out[pid] = {"state": "stale", "age": age,
                        "why": "no Orca tab, and no recent activity"}
        elif age is None:
            out[pid] = {"state": "stale", "age": None,
                        "why": "never checked in -- may not have started"}
        else:
            out[pid] = {"state": "stale", "age": age,
                        "why": f"last acted {int(age // 60)} min ago"}
    return out


def cmd_state(args) -> int:
    """What is actually going on, so the caller can pick the right next move.

    Deliberately one call: deciding between adding a partner, resuming and
    starting fresh needs roster, liveness and history together, and asking for
    them separately invites deciding on half the picture.
    """
    sd = state_dir()
    roster = load_roster(sd)
    live_map = liveness(sd, roster, args.window)
    me = me_id(roster)
    others = {p: v for p, v in live_map.items() if p != me}
    live = [p for p, v in others.items() if v["state"] == "live"]
    stale = [p for p, v in others.items() if v["state"] == "stale"]
    past = read_sessions(sd)

    if live:
        rec, why = "add", (f"{', '.join(live)} active -- `/partner` archives this "
                           f"session and starts one partner fresh; `/partner add` "
                           f"keeps it and joins")
    elif stale:
        rec, why = "ask", (f"{', '.join(stale)} in the roster but not responding -- "
                           f"`/partner` archives and starts fresh; `resume` rebuilds them")
    elif past:
        rec, why = "ask", ("no partners running -- `/partner` starts fresh, "
                           "`resume --session <id>` brings an archived one back")
    else:
        rec, why = "new", "nothing running and no history -- start a new session"

    data = {"self": me, "baton": baton_of(roster), "live": live, "stale": stale,
            "agents": live_map, "sessions": [
                {"id": m["id"], "label": m.get("label"),
                 "agents": list(m.get("agents", {})),
                 "messages": m.get("messages", 0)} for m in past],
            "messages": len(tail_msgs(sd, 100000)), "recommend": rec, "why": why}
    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    print(f"you are {me}; baton held by {data['baton']}")
    for pid, v in live_map.items():
        mark = {"live": "live   ", "stale": "STALE  ", "stopped": "stopped"}[v["state"]]
        print(f"  {pid:8} {mark} {v['why']}")
    if past:
        print(f"{NL}{len(past)} archived session(s):")
        for m in past[:5]:
            print(f"  {m['id']}   {m.get('label', '')}")
    print(f"{NL}suggested: {rec} -- {why}")
    return 0


# --------------------------------------------------------------------------
# messaging + roster
# --------------------------------------------------------------------------

def cmd_send(args) -> int:
    sd = state_dir()
    roster = load_roster(sd)
    args.sender = args.sender or me_id(roster)
    touch_seen(sd, args.sender)
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
    touch_seen(sd, who)
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
    live_map = liveness(sd, roster)
    for pid, p in roster["partners"].items():
        mark = " <-- baton" if holder == pid else ""
        mark += "  (you)" if pid == me else ""
        st = live_map.get(pid, {}).get("state", "?")
        print(f"  {pid:8} {p['provider']:8} {p.get('model') or '-':22} "
              f"effort={p.get('effort') or '-':6} auto={p.get('auto') or '-':6}"
              f"[{st}] {p.get('tab') or ''}{mark}")
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
               f"{args.to} edits files from now on; everyone else advises. "
               f"**{prev}**: you are an advisor again -- go back to `wait` so you "
               f"hear what follows.", args.to)
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
    """What this machine can actually run a partner in.

    A scan, not a menu. The recipes appear because their flags were verified by
    hand, not because they are the permitted set -- everything else found on
    the machine is listed beside them and spawns the same way.
    """
    rows = []
    for c in scan_clis(deep=args.deep):
        spec = resolve_provider(c["name"])
        pr = spec.get("probe") or (probe_cli(c["name"]) if c["installed"]
                                   and spec["kind"] == "recipe" else {})
        rows.append({**c,
                     "efforts": sorted(spec["efforts"]),
                     "effort": {"flag": "native flag", "prompt": "prompt hint"}
                               .get(spec.get("effort"), "template"),
                     "version": pr.get("version") if c["installed"] else None})
    if args.json:
        print(json.dumps({"clis": rows, "custom": {
            "usage": "--provider custom --cmd '<template with {prompt}>'"}},
            indent=2))
        return 0

    how = {"recipe": "verified flags", "your config": "your providers.json",
           "found by name": "found here", "found by behaviour": "found here"}
    for r in rows:
        state = "installed" if r["installed"] else "NOT installed"
        print(f"  {r['name']:14} {state:14} {how.get(r['how'], r['how']):20} "
              f"effort: {r['effort']}")
        if r["installed"]:
            print(f"  {'':14} {r['path']}")
            if r["version"]:
                print(f"  {'':14} {r['version']}")
            if r["how"].startswith("found"):
                print(f"  {'':14} no verified recipe -- flags are read from its "
                      f"--help at spawn time")
        elif r["install"]:
            print(f"  {'':14} install with: {r['install']}")
    if not args.deep:
        print(f"{NL}`providers --deep` also probes every binary in the user bin "
              f"directories, which finds an agent CLI whose name gives nothing "
              f"away.")
    print(f"Any CLI not listed still works: name it directly "
          f"(`spawn --provider <bin>`, flags read from its --help), or pass the "
          f"exact command with `--provider custom --cmd '<template>'`.")
    where = "an Orca tab" if orca_bin() else "a new terminal tab"
    print(f"Partners will open in {where}.")
    print("Run `models --provider <name>` for what to point one at.")
    return 0


def cmd_models(args) -> int:
    """What a CLI on this machine can be pointed at -- for the human to choose.

    Every id here was found on the machine: asked of the CLI, read out of its
    help or its bundle, or taken from the user's own config for it. Nothing is
    recited from a list in this file, because such a list is wrong within weeks
    and its wrongness is invisible -- the user simply never sees the model that
    shipped last month.

    Which is also this command's limit, and it is stated in the output rather
    than hidden: a model released after the installed CLI was built is
    mentioned nowhere locally. When this comes back thin or dated, the caller
    is expected to go and check the vendor's current lineup on the web.
    """
    names = [args.provider] if args.provider else \
        [c["name"] for c in scan_clis() if c["installed"]]
    if not names:
        emit(args, {"providers": []},
             "no agent CLI found on this machine. `providers` shows where it "
             "looked; install one, or name a binary directly.")
        return 1

    out = []
    for name in names:
        spec = resolve_provider(name)
        binary = find_binary(spec.get("bin") or name)
        models = [] if args.no_probe or not binary else \
            discover_models(name, binary, deep=args.deep)
        out.append({"provider": name, "installed": bool(binary),
                    "efforts": sorted(spec["efforts"]),
                    "effort_kind": {"flag": "native flag",
                                    "prompt": "prompt hint"}
                                   .get(spec.get("effort"), "template"),
                    "models": models})
    if args.json:
        print(json.dumps({"providers": out, "note":
              "Found on this machine, not from a vendor list. A model released "
              "after this CLI was built appears nowhere here -- check the web "
              "before telling the user this is everything."}, indent=2))
        return 0

    for p in out:
        print(f"{NL}{p['provider']}" + ("" if p["installed"] else "   (NOT installed)"))
        if not p["models"]:
            print("  nothing on this machine names a model for it. Its default "
                  "model works -- spawn without --model -- or look up the "
                  "current lineup and pass one with --model.")
        shown = 0
        for conf, header in (("named", None),
                             ("mentioned", "  only the shape of a model id, "
                                           "found in text -- some of these are "
                                           "not models:")):
            group = [m for m in p["models"] if m["confidence"] == conf]
            if group and header:
                print(header)
            for m in group[:12 if conf == "named" else 8]:
                print(f"  {m['id']:36} effort={m['effort']:7} [{m['tier']}]")
                print(f"  {'':36} {m['note']}")
                print(f"  {'':36} from {m['source']}")
                shown += 1
            if len(group) > (12 if conf == "named" else 8):
                print(f"  {'':36} ... and {len(group) - (12 if conf == 'named' else 8)} more")
        print(f"  effort is a {p['effort_kind']}; accepts: {', '.join(p['efforts'])}")
    print(f"{NL}Where each id came from is printed because it is the whole "
          f"caveat: a model list command is evidence, a string in a bundle is a "
          f"lead. The tier note is read off the *name* -- opus/pro/max reason "
          f"deeper and slower than flash/mini/lite -- not from having tried it.")
    print(f"{NL}This is what the machine knows, which is not what exists. If "
          f"the newest id here looks months old, check the provider's current "
          f"models on the web and offer those too -- they spawn with --model "
          f"whether or not they appear above.")
    print(f"{NL}Then put the options to the human and let them choose. A "
          f"partner they did not pick is one they will not believe when it "
          f"disagrees with them, which is the entire reason for running it.")
    return 0


def cmd_probe(args) -> int:
    """Show what was read out of a CLI's --help, and the command it produces.

    The debugging surface for driving a CLI nobody wrote a recipe for. When a
    partner's tab opens on a usage error, this says which flag was guessed
    wrong, and the argv line below is what to correct with `--cmd`.
    """
    name = args.provider
    path = name if os.sep in name or "/" in name else None
    if path:
        name = Path(path).stem
    pr = probe_cli(name, path, refresh=args.refresh)
    spec = resolve_provider(name)
    demo = None
    if pr["found"]:
        try:
            demo = subprocess.list2cmdline(build_tui(
                {"provider": name, "model": "<model>", "effort": "high",
                 "auto": "edits", "cmd": spec.get("cmd")}, "<starting prompt>"))
        except SystemExit:
            demo = None
    if args.json:
        print(json.dumps({"probe": {k: v for k, v in pr.items() if k != "help"},
                          "kind": spec["kind"], "example": demo}, indent=2))
        return 0
    if not pr["found"]:
        print(f"{name}: not on PATH. `providers` lists what is; a CLI installed "
              f"somewhere unusual can still be used with "
              f"--provider custom --cmd '<full path> ...'")
        return 1
    print(f"{name}  ({spec['kind']})")
    print(f"  binary        {pr['bin']}")
    print(f"  version       {pr['version'] or '-'}")
    print(f"  model flag    {pr['model_flag'] or '- (spawns on its default model)'}")
    print(f"  effort flag   {pr['effort_flag'] or '- (effort becomes a briefing line)'}"
          + (f"  accepts: {', '.join(pr['efforts'])}" if pr["efforts"] else ""))
    print(f"  auto          " + (", ".join(f"{k}={' '.join(v)}"
                                           for k, v in pr["auto"].items())
                                 or "- (it may stop and ask in its tab)"))
    print(f"  prompt        {pr['prompt_flag'] or 'positional (appended last)'}")
    print(f"  model list    {' '.join(pr['list_cmd']) if pr['list_cmd'] else '-'}")
    if demo:
        print(f"{NL}  a spawn would run:{NL}    {demo}")
    if spec["kind"] == "probed":
        print(f"{NL}Read from its own --help, not from a verified recipe. If "
              f"that command is wrong, correct it with:{NL}"
              f"  spawn --provider custom --cmd '<the right command with "
              f"{{prompt}}>'")
    return 0


def cmd_pending(args) -> int:
    """What this agent still owes a reply to. Read-only; the Stop hook runs the
    same check to keep an agent from going silent on the group."""
    sd = state_dir()
    roster = load_roster(sd)
    who = args.who or me_id(roster)
    pend = pending_for(sd, roster, who)
    if args.json:
        print(json.dumps({"pending": bool(pend), "you": who,
                          "baton": baton_of(roster), "messages": pend}, indent=2))
    else:
        print(f"{len(pend)} awaiting your reply:{NL * 2}{render(pend)}" if pend
              else "nothing pending -- you have answered everything addressed to you")
    return 0


def cmd_hook_stop(args) -> int:
    """Stop-hook entry point. Blocks the turn from ending while a partner
    message waits for this agent's reply.

    A tab agent blocked on `wait` never reaches a Stop; the session agent does,
    every turn, and nothing else re-invokes it once the human's attention moves
    to another tab. Reads the hook payload on stdin, prints a block decision on
    stdout when a reply is owed, stays silent (approve) otherwise. Fails open.
    """
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}
    cwd = payload.get("cwd")
    if cwd:
        try:
            os.chdir(cwd)
        except OSError:
            pass
    try:
        sd = state_dir()
    except OSError:
        return 0
    if not (sd / "roster.json").exists():
        return 0

    roster = load_roster(sd)
    who = me_id(roster)
    entry = roster["partners"].get(who)
    if not entry or entry.get("status") != "running":
        return 0

    rel = f".partner/{who}"
    run = rel.replace("/", "\\") + "\\p.cmd" if os.name == "nt" else rel + "/p.sh"

    # 1. Something is addressed to this agent and it has not answered.
    pend = pending_for(sd, roster, who)
    if pend:
        # Nag once per distinct transcript state: a genuinely stuck agent must
        # not be trapped in an unbreakable block loop.
        chat = sd / "chat.md"
        size = str(chat.stat().st_size if chat.exists() else 0)
        marker = sd / who / ".stop-nag"
        if marker.exists() and marker.read_text(encoding="utf-8").strip() == size:
            return 0
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(size, encoding="utf-8")
        reason = (
            f"{len(pend)} message(s) in the partner transcript are addressed to "
            f"you ({who}) and still unanswered:{NL * 2}{render(pend)}{NL * 2}"
            f"Do not stop. Run `{run} read`, then follow the debate protocol -- "
            f"verify against the code and `{run} send` your reply. You do not "
            f"hold the baton, so advise; do not edit files.")
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0

    # 2. Nothing is waiting for an answer -- but is this agent still listening?
    # A discussion ending, or the baton moving to somebody else, is exactly when
    # an agent decides it is done and stops; from that moment it is deaf, and
    # the next thing said to it lands in a transcript nobody is reading. Every
    # agent goes back to `wait` at the end of every turn, and this is what
    # enforces it.
    if is_listening(sd, roster, who):
        return 0
    if _nag_throttled(sd / who / ".stop-nag-live", 120):
        return 0
    holder = baton_of(roster)
    lost = f" The baton is {holder}'s, not yours." if holder != who else ""
    if (entry.get("kind") or "tab") == "session":
        how = (f"start `{run} wait --timeout 600` as a BACKGROUND command "
               f"(run_in_background: true) -- never in the foreground, it would "
               f"block the human out of this session")
    else:
        how = (f"run `{run} wait` -- it blocks until somebody addresses you, "
               f"which is how you stay in the debate")
    reason = (
        f"You are still a running partner ({who}) but nothing is listening on "
        f"your behalf: no `wait` is in flight.{lost} Whatever is said next -- by "
        f"the human in another tab, or by a partner -- you will not see.{NL * 2}"
        f"Before ending the turn: run `{run} read` and answer anything it shows, "
        f"then {how}.")
    print(json.dumps({"decision": "block", "reason": reason}))
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

    i = sub.add_parser("init", help="register yourself as a partner")
    i.add_argument("--me-name", default=None, help="your own id (default p1)")
    i.add_argument("--me-provider", default=None, help="the CLI you are running in")
    i.add_argument("--me-model", default=None, help="your own model id")
    i.add_argument("--me-effort", default=None,
                   choices=["low", "medium", "high", "max"])
    i.add_argument("--me-auto", default=None, choices=list(AUTO_LEVELS))
    i.add_argument("--context", default=None, help="handoff text, or path to a file")
    i.set_defaults(fn=cmd_init)

    pv = sub.add_parser("providers", help="agent CLIs found on this machine")
    pv.add_argument("--deep", action="store_true",
                    help="also probe unrecognised binaries in the user bin "
                         "dirs and keep whatever behaves like an agent CLI")
    pv.set_defaults(fn=cmd_providers)

    md = sub.add_parser("models", help="models found on this machine, and the "
                                       "effort that suits each")
    md.add_argument("--provider", default=None,
                    help="just this one; any CLI name, not only a known one")
    md.add_argument("--no-probe", action="store_true",
                    help="do not run or read the CLI at all")
    md.add_argument("--deep", action="store_true",
                    help="always scan the CLI's own bundle, not only when the "
                         "cheap sources came back thin")
    md.set_defaults(fn=cmd_models)

    pb = sub.add_parser("probe", help="what a CLI's --help says about driving it")
    pb.add_argument("--provider", required=True, help="CLI name or path")
    pb.add_argument("--refresh", action="store_true", help="ignore the cache")
    pb.set_defaults(fn=cmd_probe)
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
    sp.add_argument("--me-provider", default=None,
                    help="the CLI you yourself are running in")
    sp.add_argument("--me-model", default=None, help="your own model id")
    sp.add_argument("--me-effort", default=None,
                    choices=["low", "medium", "high", "max"])
    sp.add_argument("--me-auto", default=None, choices=list(AUTO_LEVELS))
    sp.add_argument("--fresh", action="store_true",
                    help="archive the current session and start a new one")
    sp.set_defaults(fn=cmd_spawn)

    ar = sub.add_parser("archive", help="file the current session away")
    ar.add_argument("--label", default=None, help="a name you will recognise later")
    ar.set_defaults(fn=cmd_archive)

    sub.add_parser("sessions", help="list the current and archived sessions"
                   ).set_defaults(fn=cmd_sessions)

    stt = sub.add_parser("state", help="who is actually alive, and what to do next")
    stt.add_argument("--window", type=int, default=LIVE_WINDOW,
                     help="seconds since an agent last acted before it counts as stale")
    stt.set_defaults(fn=cmd_state)

    rs = sub.add_parser("resume", help="restart the agents of a session")
    rs.add_argument("--session", default=None,
                    help="archived session id; omit to continue the current one")
    rs.add_argument("--no-tab", action="store_true")
    rs.set_defaults(fn=cmd_resume)

    ck = sub.add_parser("check", help="validate a config without starting anything")
    ck.add_argument("--provider", required=True)
    ck.add_argument("--model", default=None)
    ck.add_argument("--effort", default=None)
    ck.add_argument("--cmd", default=None)
    ck.add_argument("--force", action="store_true")
    ck.set_defaults(fn=cmd_check)

    w = sub.add_parser("wait", help="block until somebody addresses you")
    w.add_argument("--for", dest="who", default=None,
                   help="override identity (normally set by your wrapper)")
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

    pd = sub.add_parser("pending", help="messages you still owe a reply to")
    pd.add_argument("--for", dest="who", default=None)
    pd.set_defaults(fn=cmd_pending)

    sub.add_parser("hook-stop", help="internal: Stop-hook backstop"
                   ).set_defaults(fn=cmd_hook_stop)

    args = ap.parse_args()
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
