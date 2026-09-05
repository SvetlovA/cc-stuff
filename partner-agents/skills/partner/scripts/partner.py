#!/usr/bin/env python3
"""partner.py - spawn and drive peer AI agents that debate with you.

One file, no third-party deps, runs on Windows / macOS / Linux.

State lives in <repo>/.partner/:
    roster.json        every agent, which one is you, who holds the write baton
    chat.md            the single shared debate transcript
    <id>/handoff.md    that agent's briefing, written when it is spawned
    <id>/cursor        byte offset of the last message that agent consumed
    <id>/session       provider session id, for resuming its context
    <id>/log           raw provider output from the watcher loop

Every agent is an equal participant, including the one that spawned the rest.
The only asymmetry is the write baton, and it moves.

Every subcommand prints either plain text or JSON (--json) so the calling
agent can parse it without screen-scraping.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

MSG_DELIM = "<!--/msg-->"
DEFAULT_POLL = 4.0


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
    info = root / ".git" / "info"
    if not info.parent.exists():
        return
    info.mkdir(parents=True, exist_ok=True)
    ex = info / "exclude"
    body = ex.read_text(encoding="utf-8") if ex.exists() else ""
    if ".partner/" not in body:
        sep = "" if body.endswith("\n") or not body else "\n"
        ex.write_text(f"{body}{sep}.partner/\n", encoding="utf-8")


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
    return "\n".join(rows[:limit]) + f"\n... (+{len(rows) - limit} more {what})"


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
        out.append(f"\nRecent commits:\n{log}")
    status = _git(root, "status", "--short")
    if not status:
        out.append("\nWorking tree is clean.")
        return "\n".join(out)
    out.append(f"\nUncommitted changes:\n{_clip(status, 40, 'files')}")
    stat = _git(root, "diff", "--stat", "HEAD")
    if stat:
        out.append(f"\nDiff against HEAD:\n{_clip(stat, 40, 'files')}")
    return "\n".join(out)


def cmd_snapshot(args) -> int:
    snap = repo_snapshot(repo_root()) or "(not a git repository)"
    emit(args, {"snapshot": snap}, snap)
    return 0


def cmd_init(args) -> int:
    root = repo_root()
    sd = state_dir(root)
    sd.mkdir(parents=True, exist_ok=True)
    chat = sd / "chat.md"
    if not chat.exists():
        chat.write_text(
            "# Partner debate transcript\n\n"
            "Shared by every agent in this session. Append only; never rewrite history.\n\n",
            encoding="utf-8",
        )
    roster = load_roster(sd)
    me = roster.get("self") or getattr(args, "me_name", None) or "p1"
    if me not in roster["partners"]:
        roster["partners"][me] = {
            "id": me,
            "provider": getattr(args, "me_provider", None) or "claude",
            "model": getattr(args, "me_model", None),
            "effort": None, "cmd": None, "mode": "session",
            "status": "running", "started": utcnow(), "greeted": True,
            "tab": "this session",
        }
    roster["self"] = me
    if roster.get("baton") not in roster["partners"]:
        roster["baton"] = me
    save_roster(sd, roster)
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
        chat.write_text("# Partner debate transcript\n\n", encoding="utf-8")
    entry = (
        f"\n### {utcnow()} | from:{sender} | to:{to} | baton:{baton}\n\n"
        f"{body.strip()}\n\n{MSG_DELIM}\n"
    )
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
        body = chunk[m.end():].strip()
        out.append({
            "ts": m.group("ts"), "from": m.group("from"),
            "to": m.group("to"), "baton": m.group("baton"), "body": body,
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


# --------------------------------------------------------------------------
# provider registry
# --------------------------------------------------------------------------
# Each provider describes how to run ONE debate round as a headless invocation.
#
# The transcript is the memory. A provider that can resume its own session
# (claude) gets continuity for free; everything else re-reads the last few
# transcript messages in its prompt and loses nothing important. That is why
# adding a new CLI here only requires knowing how to send it one prompt --
# no session plumbing, no streaming parser.
#
# `readonly` decides the sandbox flag for THIS round. It is derived from the
# baton, so write capability is symmetric between partners and moves with
# whoever the user is addressing.

EFFORT_HINT = {
    "low": "Answer directly; keep reasoning brief.",
    "medium": "Think it through before answering.",
    "high": "Think hard. Consider at least two alternatives before answering.",
    "max": "Ultrathink. Stress-test your own position before answering.",
}


def _claude_round(c: dict) -> list[str]:
    argv = ["claude", "-p", "--output-format", "json"]
    if c["model"]:
        argv += ["--model", c["model"]]
    if c["session"]:
        argv += ["--resume", c["session"]]
    argv += ["--permission-mode", "plan" if c["readonly"] else "acceptEdits"]
    return argv + [c["msg"]]


def _codex_round(c: dict) -> list[str]:
    argv = ["codex", "exec", "--skip-git-repo-check",
            "--sandbox", "read-only" if c["readonly"] else "workspace-write"]
    if c["model"]:
        argv += ["-m", c["model"]]
    if c["effort"]:
        argv += ["-c", f'model_reasoning_effort="{c["effort"]}"']
    return argv + [c["msg"]]


def _gemini_round(c: dict) -> list[str]:
    argv = ["gemini"]
    if c["model"]:
        argv += ["-m", c["model"]]
    if not c["readonly"]:
        argv += ["--approval-mode", "yolo"]
    return argv + ["-p", c["msg"]]


PROVIDERS: dict[str, dict] = {
    "claude": {"bin": "claude", "round": _claude_round, "resume": "id",
               "effort": "prompt",
               "models": ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"]},
    "codex":  {"bin": "codex", "round": _codex_round, "resume": "none",
               "effort": "flag", "models": ["gpt-5-codex", "gpt-5"]},
    "gemini": {"bin": "gemini", "round": _gemini_round, "resume": "none",
               "effort": "prompt", "models": ["gemini-2.5-pro", "gemini-2.5-flash"]},
}


def custom_round(template: str, c: dict) -> list[str]:
    """Any CLI, described by a template string.

    Placeholders: {msg} {model} {effort} {readonly}
    e.g.  --provider custom --cmd 'aider --model {model} --message {msg}'
    A template without {msg} gets the prompt appended as the last argument.
    """
    import shlex
    parts = shlex.split(template)
    out, saw_msg = [], False
    for p in parts:
        if "{msg}" in p:
            saw_msg = True
        out.append(
            p.replace("{msg}", c["msg"])
             .replace("{model}", c["model"] or "")
             .replace("{effort}", c["effort"] or "")
             .replace("{readonly}", "1" if c["readonly"] else "0")
        )
    out = [p for p in out if p != ""]
    if not saw_msg:
        out.append(c["msg"])
    return out


def build_round(p: dict, msg: str, readonly: bool, session: str | None) -> list[str]:
    c = {"msg": msg, "model": p.get("model") or "", "effort": p.get("effort") or "",
         "readonly": readonly, "session": session}
    prov = p["provider"]
    if prov == "custom":
        return custom_round(p.get("cmd") or "", c)
    spec = PROVIDERS.get(prov)
    if not spec:
        raise SystemExit(f"unknown provider {prov!r}; known: {', '.join(PROVIDERS)}, custom")
    return spec["round"](c)


def provider_spec(prov: str) -> dict:
    return PROVIDERS.get(prov, {"resume": "none", "effort": "prompt", "bin": None})


def _deep_find(obj, names: set[str]):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in names and isinstance(v, str) and v:
                return v
            found = _deep_find(v, names)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _deep_find(v, names)
            if found:
                return found
    return None


def parse_output(prov: str, stdout: str) -> tuple[str, str | None]:
    """Return (reply_text, session_id). Falls back to raw stdout for any CLI."""
    text, session = stdout.strip(), None
    if prov == "claude":
        try:
            data = json.loads(stdout)
            text = (data.get("result") or "").strip() or text
            session = _deep_find(data, {"session_id", "sessionId"})
        except json.JSONDecodeError:
            pass
    else:
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    session = session or _deep_find(
                        json.loads(line),
                        {"session_id", "conversation_id", "thread_id"})
                except json.JSONDecodeError:
                    pass
    return text, session


# --------------------------------------------------------------------------
# terminal tabs
# --------------------------------------------------------------------------
# Quoting a nested agent command through wt.exe / osascript / gnome-terminal
# is where cross-platform launchers normally rot. We sidestep it entirely:
# write the real command into a per-partner run script, then every terminal
# only ever has to run one plain file path.

def write_runner(pdir: Path, argv: list[str], cwd: Path) -> Path:
    import shlex
    if os.name == "nt":
        r = pdir / "run.cmd"
        body = "@echo off\r\ncd /d \"{}\"\r\n{}\r\n".format(
            cwd, subprocess.list2cmdline(argv))
        r.write_text(body, encoding="utf-8")
    else:
        r = pdir / "run.sh"
        r.write_text("#!/usr/bin/env bash\ncd {}\nexec {}\n".format(
            shlex.quote(str(cwd)), shlex.join(argv)), encoding="utf-8")
        r.chmod(0o755)
    return r


def _has(binary: str) -> bool:
    return shutil.which(binary) is not None


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
        script = (
            'tell application "iTerm2" to if it is running then\n'
            f'  tell current window to create tab with default profile command "bash {r}"\n'
            'end if'
        )
        if Path("/Applications/iTerm.app").exists():
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
# one debate round
# --------------------------------------------------------------------------

ROLE = """You are "{id}", a peer engineer working alongside "{peer}" on the repository at {cwd}.

You are an EQUAL PARTNER, not an assistant and not a reviewer waiting for input.
Your value is independent judgement: if you agree with everything you are a
waste of tokens, and if you disagree with everything you are noise. Say what
you actually think.

WRITE BATON: held by "{baton}".
{baton_rule}

How to reply:
- Open with your position in one sentence.
- If you disagree, say so plainly and give the concrete alternative, not just an objection.
- If you agree, say AGREED and add only what is genuinely missing. Do not pad.
- Point at real evidence: cite files as path:line, name the command you ran.
- Keep it under ~200 words unless the question genuinely needs more.
"""

BATON_HOLDER = ("You hold the baton, so you are the one who edits files this round. "
                "Make the change, then report what you changed.")
BATON_OTHER = ("You do NOT hold the baton. Read, run read-only commands, and argue. "
               "Do not modify files -- propose the change in prose or as a diff and "
               "let the baton holder apply it.")


def build_prompt(sd: Path, pid: str, p: dict, roster: dict, msgs: list[dict],
                 include_tail: bool) -> str:
    baton = baton_of(roster)
    peers = [k for k in roster.get("partners", {}) if k != pid] or ["nobody yet"]
    parts = [ROLE.format(id=pid, peer=", ".join(peers), cwd=repo_root(),
                         baton=baton,
                         baton_rule=BATON_HOLDER if baton == pid else BATON_OTHER)]

    hint = EFFORT_HINT.get((p.get("effort") or "").lower())
    if hint and provider_spec(p["provider"]).get("effort") == "prompt":
        parts.append(hint)

    first = not p.get("greeted")
    if first:
        handoff = sd / pid / "handoff.md"
        if handoff.exists():
            parts.append("## Context you are joining\n\n"
                         + handoff.read_text(encoding="utf-8", errors="replace"))

    # A partner can be spawned at any point, including into an argument that
    # is already underway. On its first round it always gets the history --
    # without it, it reopens questions the others already settled. After that,
    # only providers that cannot resume their own session need it replayed.
    if first or include_tail:
        tail = tail_msgs(sd, 20 if first else 8)
        # Exclude the new messages by identity, not by position: with several
        # partners replying at once the newest entries in the transcript are
        # not necessarily the ones addressed to this partner.
        new_keys = {(m["ts"], m["from"], m["body"]) for m in msgs}
        earlier = [m for m in tail
                   if (m["ts"], m["from"], m["body"]) not in new_keys]
        if earlier:
            heading = ("## Debate you are joining, most recent last" if first
                       else "## Conversation so far")
            parts.append(heading + "\n\n" + "\n\n".join(
                f"**{m['from']} -> {m['to']}:** {m['body']}" for m in earlier))

    parts.append("## New messages addressed to you\n\n" + "\n\n".join(
        f"**{m['from']}:** {m['body']}" for m in msgs))
    return "\n\n".join(parts)


def run_round(sd: Path, pid: str, roster: dict, msgs: list[dict], timeout: int) -> str:
    p = roster["partners"][pid]
    spec = provider_spec(p["provider"])
    sess_f = sd / pid / "session"
    session = sess_f.read_text(encoding="utf-8").strip() if (
        spec.get("resume") == "id" and sess_f.exists()) else None

    prompt = build_prompt(sd, pid, p, roster, msgs,
                          include_tail=(spec.get("resume") != "id" or not session))
    readonly = baton_of(roster) != pid
    argv = build_round(p, prompt, readonly, session)

    try:
        proc = subprocess.run(argv, cwd=str(repo_root()), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
    except FileNotFoundError:
        return f"[partner error] `{argv[0]}` is not installed or not on PATH."
    except subprocess.TimeoutExpired:
        return f"[partner error] no reply within {timeout}s."

    reply, new_sess = parse_output(p["provider"], proc.stdout)
    if new_sess and spec.get("resume") == "id":
        (sd / pid).mkdir(parents=True, exist_ok=True)
        sess_f.write_text(new_sess, encoding="utf-8")
    if not reply:
        err = (proc.stderr or "").strip()[-800:]
        reply = f"[partner error] empty reply (exit {proc.returncode}).\n{err}"
    return reply


# --- CONTRIBUTION POINT -----------------------------------------------------
# TODO(user): decide when a partner should stop arguing and let the work move on.
#
# Called after each reply the partner produces. Return True to keep the
# exchange open, False to end it (the partner falls silent until addressed
# again). `consecutive` counts replies this partner has made without the
# other side conceding or a new user question arriving.
#
# The trade-off: a low cap converges fast but lets whichever agent is more
# confident win by attrition; a high cap surfaces real disagreement but burns
# tokens and can deadlock two stubborn models on a point that does not matter.
def should_continue_debate(reply: str, consecutive: int, max_rounds: int) -> bool:
    if consecutive >= max_rounds:
        return False
    return "AGREED" not in reply.upper().split("\n")[0]


# --------------------------------------------------------------------------
# spawn / watch
# --------------------------------------------------------------------------

SEED = """You are partner "{id}" in a multi-agent debate on the repo at {cwd}.

Talk to the other agents through the shared transcript. From this directory:

  Read anything new addressed to you:
      python "{script}" read --for {id}
  Reply or raise a point:
      python "{script}" send --from {id} --to @all --text "..."
  See who is here and who holds the write baton:
      python "{script}" list

The baton decides who edits files. If you do not hold it, argue and propose --
do not write. Check `list` before you touch anything.

Start by reading your inbox, then respond.
"""


def tui_argv(p: dict, seed: str) -> list[str]:
    prov, model = p["provider"], p.get("model")
    if prov == "custom":
        return custom_round(p.get("cmd") or "", {
            "msg": seed, "model": model or "", "effort": p.get("effort") or "",
            "readonly": False, "session": None})
    if prov == "claude":
        return ["claude"] + (["--model", model] if model else []) + [seed]
    if prov == "codex":
        return ["codex"] + (["-m", model] if model else []) + [seed]
    if prov == "gemini":
        return ["gemini"] + (["-m", model] if model else []) + ["-i", seed]
    return [PROVIDERS[prov]["bin"], seed]


def cmd_spawn(args) -> int:
    root = repo_root()
    sd = state_dir(root)
    cmd_init(argparse.Namespace(json=False, quiet=True))

    roster = load_roster(sd)
    pid = args.name or f"p{len(roster['partners']) + 1}"
    if pid in roster["partners"] and not args.replace:
        emit(args, {"error": "exists"}, f"partner {pid!r} already exists; pass --replace")
        return 1
    if args.provider not in PROVIDERS and args.provider != "custom":
        emit(args, {"error": "provider"},
             f"unknown provider {args.provider!r}; known: {', '.join(PROVIDERS)}, custom")
        return 1
    if args.provider == "custom" and not args.cmd:
        emit(args, {"error": "cmd"}, "--provider custom requires --cmd '<template with {msg}>'")
        return 1

    pdir = sd / pid
    pdir.mkdir(parents=True, exist_ok=True)
    entry = {"id": pid, "provider": args.provider, "model": args.model,
             "effort": args.effort, "cmd": args.cmd, "mode": args.mode,
             "status": "running", "started": utcnow(), "greeted": False}
    roster["partners"][pid] = entry

    # Each partner keeps its own briefing: spawning p2 later must not clobber
    # what p1 was told, and they may be joining for different reasons.
    brief = []
    if args.context:
        ctx = Path(args.context)
        brief.append(ctx.read_text(encoding="utf-8", errors="replace")
                     if ctx.exists() else args.context)
    snap = repo_snapshot(root)
    if snap:
        brief.append("## Working tree at the moment you joined\n\n" + snap)
    if brief:
        (pdir / "handoff.md").write_text("\n\n".join(brief), encoding="utf-8")

    if args.mode == "tui":
        seed_f = pdir / "seed.md"
        seed_f.write_text(SEED.format(id=pid, cwd=root, script=Path(__file__).resolve()),
                          encoding="utf-8")
        argv = tui_argv(entry, seed_f.read_text(encoding="utf-8"))
    else:
        argv = [sys.executable, str(Path(__file__).resolve()), "watch",
                "--id", pid, "--poll", str(args.poll)]

    runner = write_runner(pdir, argv, root)
    label = "" if args.no_tab else open_tab(f"partner:{pid}", runner, root)
    entry["tab"] = label
    entry["runner"] = str(runner)
    save_roster(sd, roster)

    manual = (f'cmd /c "{runner}"' if os.name == "nt" else f'bash "{runner}"')
    spec = (f"{args.provider}"
            f"{'/' + args.model if args.model else ''}"
            f"{'/' + args.effort if args.effort else ''}")
    if label:
        msg = f"partner {pid} ({spec}) started in {label}"
    elif args.no_tab:
        msg = f"partner {pid} ({spec}) registered. Start it with:\n  {manual}"
    else:
        msg = (f"partner {pid} ({spec}) registered, but no terminal could be "
               f"opened.\nOpen a tab yourself and run:\n  {manual}")
    emit(args, {**entry, "manual_command": manual}, msg)
    return 0


def cmd_watch(args) -> int:
    sd = state_dir()
    pid = args.id
    consecutive = 0
    print(f"[partner {pid}] watching {sd / 'chat.md'} -- Ctrl-C to stop, "
          f"then run the provider directly for an interactive session.\n", flush=True)
    while True:
        roster = load_roster(sd)
        p = roster["partners"].get(pid)
        if not p or p.get("status") == "stopped":
            print(f"[partner {pid}] stopped.", flush=True)
            return 0
        msgs = read_new(sd, pid)
        if msgs:
            print(f"[partner {pid}] {len(msgs)} new message(s); thinking...", flush=True)
            reply = run_round(sd, pid, roster, msgs, args.timeout)
            append_msg(sd, pid, msgs[-1]["from"], reply, baton_of(roster))
            (sd / pid / "log").open("a", encoding="utf-8").write(
                f"\n--- {utcnow()} ---\n{reply}\n")
            print(reply + "\n", flush=True)
            roster = load_roster(sd)
            if pid in roster["partners"]:
                roster["partners"][pid]["greeted"] = True
                save_roster(sd, roster)
            consecutive += 1
            if not should_continue_debate(reply, consecutive, args.max_rounds):
                consecutive = 0
        else:
            consecutive = 0
        time.sleep(args.poll)


# --------------------------------------------------------------------------
# messaging + roster commands
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
               and v.get("mode") != "session"]
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
    text = "\n\n".join(f"### {m['from']}\n\n{m['body']}" for m in replies) or \
        f"(no reply within {args.wait}s -- check the partner tab)"
    emit(args, {"replies": replies}, text)
    return 0


def cmd_read(args) -> int:
    sd = state_dir()
    who = args.who or me_id(load_roster(sd))
    msgs = read_new(sd, who, advance=not args.peek)
    if args.json:
        print(json.dumps(msgs, indent=2))
    elif not msgs:
        print("(nothing new)")
    else:
        for m in msgs:
            print(f"### {m['ts']} {m['from']} -> {m['to']} (baton:{m['baton']})\n\n{m['body']}\n")
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
        print("no partners -- spawn one with: partner.py spawn --provider codex")
        return 0
    me = me_id(roster)
    for pid, p in roster["partners"].items():
        mark = " <-- baton" if holder == pid else ""
        mark += "  (you)" if pid == me else ""
        model = p.get("model") or "-"
        effort = p.get("effort") or "-"
        print(f"  {pid:8} {p['provider']:8} {model:22} effort={effort:6} "
              f"{p.get('mode')}  [{p.get('status')}] {p.get('tab') or ''}{mark}")
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
        # --all means every spawned agent. This session has no tab to close and
        # stopping it would leave the transcript with no reader.
        targets = [k for k, v in roster["partners"].items()
                   if k != me and v.get("mode") != "session"]
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
    append_msg(sd, "system", "@all", f"Stopped: {', '.join(targets)}.",
               baton_of(roster))
    emit(args, {"stopped": targets}, f"stopped {', '.join(targets)} (close their tabs)")
    return 0


def cmd_providers(args) -> int:
    rows = []
    for name, spec in PROVIDERS.items():
        rows.append({"provider": name, "installed": _has(spec["bin"]),
                     "models": spec["models"],
                     "effort": "native flag" if spec["effort"] == "flag" else "prompt hint",
                     "resume": spec["resume"]})
    rows.append({"provider": "custom", "installed": None,
                 "models": [], "effort": "template", "resume": "none"})
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    labels = {True: "installed", False: "NOT installed", None: "via --cmd"}
    for r in rows:
        print(f"  {r['provider']:8} {labels[r['installed']]:15} "
              f"effort: {r['effort']:12} models: {', '.join(r['models']) or '-'}")
    return 0


def emit(args, data: dict, human: str) -> None:
    if getattr(args, "quiet", False):
        return
    print(json.dumps(data, indent=2) if getattr(args, "json", False) else human)


def main() -> int:
    ap = argparse.ArgumentParser(prog="partner.py", description="Peer AI debate partners.")
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

    sp = sub.add_parser("spawn", help="start a partner in a new terminal tab")
    sp.add_argument("--provider", required=True)
    sp.add_argument("--model", default=None)
    sp.add_argument("--effort", default=None, choices=["low", "medium", "high", "max"])
    sp.add_argument("--name", default=None, help="partner id (default p1, p2, ...)")
    sp.add_argument("--mode", default="loop", choices=["loop", "tui"])
    sp.add_argument("--cmd", default=None, help="command template for --provider custom")
    sp.add_argument("--context", default=None, help="handoff text, or path to a file")
    sp.add_argument("--poll", type=float, default=DEFAULT_POLL)
    sp.add_argument("--no-tab", action="store_true")
    sp.add_argument("--replace", action="store_true")
    sp.set_defaults(fn=cmd_spawn)

    w = sub.add_parser("watch", help="internal: the loop that runs inside a tab")
    w.add_argument("--id", required=True)
    w.add_argument("--poll", type=float, default=DEFAULT_POLL)
    w.add_argument("--timeout", type=int, default=600)
    w.add_argument("--max-rounds", type=int, default=3)
    w.set_defaults(fn=cmd_watch)

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
