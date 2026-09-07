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
# Every agent runs `wait` through its CLI's own shell tool, and every such tool
# caps how long a command may run. A default at or above a cap turns every
# normal wait into a tool error, which is what teaches an agent that the
# command is broken. 90s sits under the caps seen so far (120s and 10s on the
# two CLIs measured) and still stamps a heartbeat well inside LIVE_WINDOW.
# Agents whose cap is shorter are told to raise it or re-run -- see
# GENERIC_WAIT_NOTE.
WAIT_TIMEOUT = 90
NUDGE_WINDOW = 120         # seconds between nudges to the same silent agent
# How long after launch a claim is treated as the agent misreading its own boot
# prompt for a human instruction. Long enough to cover reading the briefing and
# reaching the first `wait`; short enough that a human typing at a new partner
# is rarely caught by it, and `claim --force` covers them when they are.
CLAIM_GRACE = 90
# A message addressed to an agent that it has not answered is re-delivered by
# `wait` once it is this old -- long enough that a reply already in progress is
# never mistaken for a dropped message, short enough that a real drop costs one
# cycle rather than a partner noticing the silence.
REDELIVER_AFTER = 45
REDELIVER_WINDOW = 90      # seconds between re-deliveries of the same backlog
# A polling `wait` refreshes its marker every cycle, so "is anyone listening"
# is answered by a heartbeat rather than by the marker merely existing: a wait
# that was killed stops refreshing, and another can take over within a cycle
# or two instead of waiting out a deadline that will never arrive.
MARKER_STALE = 15


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
        set_floor(sd, me)          # owes nothing for what was said before it
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


def _offset(sd: Path, who: str, name: str, cap: int) -> int:
    f = sd / who / name
    if not f.exists():
        return 0
    try:
        return min(int(f.read_text(encoding="utf-8").strip() or 0), cap)
    except (OSError, ValueError):
        return 0


def read_new(sd: Path, who: str) -> tuple[list[dict], int]:
    """Messages addressed to `who` (or @all) that `who` has not yet consumed,
    plus the offset that consuming them would move the cursor to.

    The cursor is deliberately *not* moved here. Advancing it before the caller
    has printed anything means a command killed in between -- which is routine,
    since every CLI caps command runtime and some caps are shorter than a wait
    -- consumes the message and loses it: no later `wait` can return it,
    because the cursor is already past it. `commit_cursor` is called after the output is flushed, so
    a killed wait re-delivers instead of swallowing.
    """
    chat = sd / "chat.md"
    if not chat.exists():
        return [], 0
    raw = chat.read_bytes()
    start = _offset(sd, who, "cursor", len(raw))
    fresh = raw[start:].decode("utf-8", errors="replace")
    msgs = [m for m in parse_msgs(fresh)
            if m["from"] != who and m["to"] in (who, "@all", "all")]
    return msgs, len(raw)


def commit_cursor(sd: Path, who: str, end: int) -> None:
    """Mark everything up to `end` as delivered, and never un-mark anything.

    Only ever forward: a `read` in one process and a `wait` in another can each
    hold an offset from a different moment, and letting the older one win would
    rewind the cursor and re-deliver a stretch of transcript that was already
    answered.
    """
    try:
        cur_f = sd / who / "cursor"
        cur_f.parent.mkdir(parents=True, exist_ok=True)
        have = 0
        if cur_f.exists():
            try:
                have = int(cur_f.read_text(encoding="utf-8").strip() or 0)
            except ValueError:
                have = 0
        if end > have:
            cur_f.write_text(str(end), encoding="utf-8")
    except OSError:
        pass


def set_floor(sd: Path, who: str) -> None:
    """Record where this agent's responsibility starts.

    Everything before the floor is history it was briefed on rather than mail
    it owes an answer to. Without it, "unanswered" would mean the entire
    transcript for an agent that has not spoken yet, and a new partner would be
    handed the whole backlog to reply to.
    """
    chat = sd / "chat.md"
    try:
        (sd / who).mkdir(parents=True, exist_ok=True)
        (sd / who / "floor").write_text(
            str(chat.stat().st_size if chat.exists() else 0), encoding="utf-8")
    except OSError:
        pass


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
    raw = chat.read_bytes()
    # From this agent's floor, not from the top of the file: what was said
    # before it joined is briefing material, not mail it owes a reply to.
    text = raw[_offset(sd, who, "floor", len(raw)):].decode("utf-8", errors="replace")
    convo = [m for m in parse_msgs(text) if m["from"] != "system"]
    # Per sender, not one global "since I last spoke" line. With three agents
    # talking, a reply to p1 would otherwise clear the debt to p3 as well -- so
    # a message that arrived alongside another and was never answered became
    # invisible to every later check. What answers a sender is a message TO
    # that sender, or to @all.
    answered_at: dict[str, int] = {}
    for i, m in enumerate(convo):
        if m["from"] != who:
            continue
        if m["to"] in ("@all", "all"):
            for s in {x["from"] for x in convo if x["from"] != who}:
                answered_at[s] = i
            answered_at["@all"] = i
        else:
            answered_at[m["to"]] = i
    return [m for i, m in enumerate(convo)
            if m["from"] != who and m["to"] in (who, "@all", "all")
            and i > answered_at.get(m["from"], answered_at.get("@all", -1))]


def dropped_for(sd: Path, roster: dict, who: str,
                min_age: int = REDELIVER_AFTER) -> list[dict]:
    """Messages this agent was given and never answered.

    The cursor answers "has this been handed over", which stops being the right
    question the moment a handover fails: a `wait` killed mid-print, or an agent
    that read a message and then ended its turn, both leave the message consumed
    and unanswerable -- invisible to every later `wait`, recoverable only by
    another agent noticing the silence and pinging.

    So `wait` asks the transcript instead of the cursor. `min_age` keeps the
    normal cycle out of it: a message being replied to right now is not dropped,
    it is being worked on.
    """
    out = []
    for m in pending_for(sd, roster, who):
        age = _age_seconds(m.get("ts"))
        if age is None or age >= min_age:
            out.append(m)
    return out


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


def read_marker(sd: Path, who: str) -> dict:
    """Who is polling for this agent, and when they last proved it."""
    try:
        raw = (sd / who / "waiting").read_text(encoding="utf-8").strip()
    except OSError:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        # A marker written by an older version: a bare deadline, no heartbeat.
        try:
            return {"token": "", "deadline": float(raw), "beat": time.time()}
        except ValueError:
            return {}


def marker_live(mark: dict) -> bool:
    beat = mark.get("beat") or 0
    return bool(mark) and (time.time() - beat) < MARKER_STALE


def write_marker(sd: Path, who: str, token: str, deadline: float) -> None:
    try:
        d = sd / who
        d.mkdir(parents=True, exist_ok=True)
        (d / "waiting").write_text(json.dumps(
            {"token": token, "pid": os.getpid(), "deadline": deadline,
             "beat": time.time()}), encoding="utf-8")
    except OSError:
        pass


def cmd_wait(args) -> int:
    """Block until somebody addresses this agent.

    This is what lets an ordinary interactive session take part on its own: the
    agent runs this, the command sits there until a message arrives, and the
    agent then has something to answer. It always returns within --timeout so
    the tab never looks wedged and the human can interrupt and type instead.
    """
    sd = state_dir()
    roster = load_roster(sd)
    who = args.who or me_id(roster)
    deadline = time.time() + args.timeout
    # Say what this command is doing *before* blocking, and flush it. Every CLI
    # caps how long a shell command may run, and a capped wait that has printed
    # nothing looks to the agent like a command that does not work, which is how
    # partners talk themselves out of the loop. One flushed line means even a
    # killed wait carries its own instructions, whichever CLI killed it.
    if not args.json:
        print(f"[listening as {who} for up to {args.timeout}s. If this command "
              f"is cut short by your tool's own timeout, that is the timeout, "
              f"not an answer -- run `wait` again immediately. Never end your "
              f"turn without a wait in flight.]", flush=True)
    # <id>/waiting says who is polling for this agent. Only that one consumes
    # messages; a second `wait` for the same agent idles instead, because two
    # of them split the inbox -- each message goes to whichever polls first, and
    # the one whose output nobody reads takes its message with it.
    #
    # Duplicates are not hypothetical: they used to be manufactured here. The
    # marker was deleted by whichever wait finished first, so a second one still
    # polling looked like nothing listening at all -- which is exactly what the
    # Stop hook and `state` tell an agent to fix by starting another wait.
    #
    # So ownership is a token with a heartbeat, and a duplicate blocks quietly
    # rather than exiting: exiting immediately would return control to the agent,
    # which arms another wait, which exits immediately...
    token = f"{os.getpid()}-{time.time():.3f}"
    owner = False
    try:
        while True:
            mark = read_marker(sd, who)
            if mark.get("token") == token:
                owner = True
            elif marker_live(mark):
                owner = False            # somebody else is consuming; idle
            else:
                # Free, or the previous owner stopped proving it was alive.
                write_marker(sd, who, token, deadline)
                owner = True
            if not owner:
                if time.time() >= deadline:
                    emit(args, {"messages": [], "duplicate": True},
                         f"(another `wait` for {who} was already listening, so "
                         f"this one stayed out of the way -- keep exactly ONE "
                         f"in flight; a second splits the inbox and messages "
                         f"get read by whichever polls first)")
                    return 0
                time.sleep(args.poll)
                continue
            write_marker(sd, who, token, deadline)   # heartbeat
            touch_seen(sd, who)          # still here, still listening
            msgs, end = read_new(sd, who)
            if msgs:
                roster = load_roster(sd)
                if args.json:
                    print(json.dumps({"baton": baton_of(roster), "you": who,
                                      "messages": msgs}, indent=2))
                else:
                    print(baton_banner(roster, who) + NL * 2 + render(msgs)
                          + silence_notice(sd, roster, who))
                # Only now, once the messages are actually out: a wait killed
                # before this point re-delivers rather than losing them.
                sys.stdout.flush()
                commit_cursor(sd, who, end)
                return 0

            # Nothing new -- but the cursor only records what was handed over,
            # not what was answered. Anything addressed to this agent that it
            # never replied to comes back here, which is what stops a dropped
            # message from needing another agent to notice and ping.
            roster = load_roster(sd)
            dropped = dropped_for(sd, roster, who)
            if dropped and not _nag_throttled(sd / who / ".redeliver",
                                              REDELIVER_WINDOW):
                if args.json:
                    print(json.dumps({"baton": baton_of(roster), "you": who,
                                      "messages": dropped, "redelivered": True},
                                     indent=2))
                else:
                    print(baton_banner(roster, who) + NL * 2 + render(dropped)
                          + NL * 2
                          + "[re-delivered: addressed to you, still unanswered. "
                            "You were given this before and did not reply -- "
                            "answer it with `send` (even just to disagree or to "
                            "say it is settled). Replying is what clears it.]"
                          + silence_notice(sd, roster, who))
                sys.stdout.flush()
                commit_cursor(sd, who, end)
                return 0
            if time.time() >= deadline:
                # An idle cycle is the cheapest moment to notice that somebody
                # else went deaf, and the only routine one -- nobody is waiting
                # on a reply here.
                roster = load_roster(sd)
                notice = silence_notice(sd, roster, who, wake=True)
                emit(args, {"messages": []},
                     f"(nothing addressed to {who} in {args.timeout}s "
                     f"-- run wait again to keep listening){notice}")
                return 0
            time.sleep(args.poll)
    finally:
        # Only the owner clears the marker. Clearing somebody else's is what
        # made a live listener look dead and started the whole cycle.
        try:
            if read_marker(sd, who).get("token") == token:
                (sd / who / "waiting").unlink()
        except OSError:
            pass


def silence_notice(sd: Path, roster: dict, who: str, wake: bool = False) -> str:
    """One line about agents nothing is listening for, optionally waking them.

    Delivered through `wait` and `read` because that is where every agent looks
    constantly -- a partner that has gone deaf is otherwise invisible until
    somebody notices their question was never answered.
    """
    silent = silent_agents(sd, roster, who)
    if not silent:
        return ""
    if wake:
        woke = [r["id"] for r in
                (nudge_agent(sd, roster, p,
                             "you stopped looping -- nobody is listening for you.")
                 for p in silent) if r["nudged"]]
        if woke:
            return (f"{NL * 2}[{', '.join(woke)} had stopped listening; a wake-up "
                    f"was typed into their tabs.]")
    verb = "is" if len(silent) == 1 else "are"
    return (f"{NL * 2}[{', '.join(silent)} {verb} not listening -- no `wait` is "
            f"in flight. `nudge --id <id>` types a wake-up into the tab.]")


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
        touch_seen(sd, who)
        emit(args, {"baton": who, "changed": False},
             f"baton: already yours ({who}) -- go ahead")
        return 0

    # A claim from an agent that has just been launched and has never spoken is
    # almost always its own launch message being read as a human instruction:
    # the prompt lands where the human's typing lands, and the briefing says a
    # human addressing you means claim. It is intermittent -- a judgement call
    # each model makes differently -- and the cost is silent, so the guard is
    # here in shared state rather than only in the briefing. The human really
    # can address a partner seconds after it starts, so this refuses once and
    # says how to proceed rather than deciding it knows better.
    if not args.force:
        entry = roster["partners"][who]
        age = _age_seconds(entry.get("started"))
        spoke = any(m["from"] == who for m in tail_msgs(sd, 200))
        if age is not None and age < CLAIM_GRACE and not spoke:
            touch_seen(sd, who)
            emit(args, {"baton": prev, "changed": False, "refused": "launch"},
                 f"claim refused: you started {int(age)}s ago and have not "
                 f"spoken yet, so this is almost certainly your own launch "
                 f"message rather than the human. The baton stays with {prev}. "
                 f"Go to `wait` and take part as an advisor. If the human "
                 f"really did just type an instruction to you, run "
                 f"`claim --force`.")
            return 0
    touch_seen(sd, who)
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

# Every agent reaches `wait` through its CLI's own shell tool, and every one of
# those caps command runtime. When the cap is shorter than the wait, the call
# comes back early -- often with no output at all -- and an agent reading that
# as a broken command stops looping and goes deaf, which is the most common way
# a partner drops out of the debate.
#
# The rule is the same for every CLI, so it is stated once, for every CLI, and
# no partner depends on this file having heard of it. A specific cap is only
# ever an accelerator on top: a `wait_note` in a recipe or in the user's own
# providers.json replaces the generic text for that CLI, exactly as `install`
# and `efforts` do. Nothing here is a gate, and a CLI with no note is fully
# briefed by the generic one.
GENERIC_WAIT_NOTE = (
    "Find out your own limit once and work with it: if `wait` comes back early "
    "-- especially with no output at all -- that is your shell tool's own cap "
    "on how long a command may run, not a failure and not an answer. Raise the "
    "timeout you pass that tool if it takes one, and either way run `wait` "
    "again immediately. Never treat a short or silent `wait` as a reason to end "
    "your turn.")


def wait_note(provider: str) -> str:
    """The wait guidance for one CLI: its own, or the rule that holds for all."""
    if not provider or provider == "custom":
        return GENERIC_WAIT_NOTE
    try:
        return provider_spec(provider).get("wait_note") or GENERIC_WAIT_NOTE
    except (OSError, ValueError, KeyError, SystemExit):
        return GENERIC_WAIT_NOTE


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
               "login": "claude  (then /login)",
               "wait_note": (
                   "Your Bash tool times out at 120s by default (600s max), so "
                   "keep `wait` under that -- its own default is 90s -- or pass "
                   "a longer tool timeout. A wait that returns with nothing is "
                   "normal: run it again.")},
    "codex": {"bin": "codex", "tui": _codex_tui, "effort": "flag",
              # A real API field, so only values the API accepts work here.
              # "max" is this skill's own level -- it maps onto "high".
              "efforts": {"low", "medium", "high"},
              "install": "npm install -g @openai/codex",
              "login": "codex login",
              # Verified against codex 0.15x: the exec tool's yield_time_ms
              # defaults to 10s, which is shorter than any useful wait.
              "wait_note": (
                   "Your exec tool terminates a command after 10s by default, "
                   "which is shorter than a `wait`. Pass an explicit long "
                   "runtime every time you call it -- `timeout_ms` / "
                   "`yield_time_ms` of 600000, or a first-line "
                   "`// @exec: {\"yield_time_ms\": 600000}` pragma in code "
                   "mode. If it still returns early or empty, that is the cap "
                   "expiring, not an answer and not an error: run `wait` again "
                   "immediately, and never treat a short or silent wait as a "
                   "reason to end your turn.")},
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
                 "install": "...", "note": "...", "wait_note": "..."}

    `wait_note` is how a CLI whose command-runtime cap this plugin has never
    seen still briefs its partners correctly -- it replaces the generic wait
    guidance for that CLI.
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
                # A cap this plugin has never heard of is describable by the
                # person who has: the note goes into that CLI's briefings.
                "wait_note": ov.get("wait_note", ""),
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
# terminals
# --------------------------------------------------------------------------
# A terminal is described the same way a provider is: by data, in one place,
# with the user able to add or replace an entry without touching this file.
# There is no blessed set of terminals any more than there is a blessed set of
# CLIs -- a terminal released this year, or a fork nobody here has heard of, is
# nameable by whoever has it.
#
# Each entry needs at most three things:
#
#   open    the command that opens a tab running one script      (required)
#   send    the command that types a line into that tab, if it can be
#   handle  where the open command reports an id, when `send` needs one
#
# Templates use the same substitution as a provider `--cmd`: split into
# arguments first, then placeholders replaced inside each argument, so a path
# with spaces stays one argument and a Windows path is never mangled by shell
# quoting rules.
#
#   {bin}   the resolved binary          {cwd}     the repo root
#   {run}   the runner script path       {title}   partner:<id>
#   {shell} the command that runs the runner script through a shell
#   {text}  the line to type (send only) {handle}  the id from `handle`
#
# Detection is per entry, not a hardcoded order of ifs: `env` (any of these set),
# `bin` (on PATH), `path` (exists), `platform`. Two hints only affect order:
# an entry whose `env` is set is a host we are already inside, and an entry
# whose `term` matches $TERM / $TERM_PROGRAM is the terminal the user is
# actually looking at. Otherwise the table order stands -- multiplexers first,
# because a tab inside the window the user already has costs nothing; then
# terminals with a control CLI, since those are also the ones that can be typed
# into; then whatever else can open a window.

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


# --------------------------------------------------------------------------
# terminals this machine has that nothing here has heard of
# --------------------------------------------------------------------------
# The table above is an accelerator, exactly like RECIPES for providers: it
# holds entries whose flags were checked by hand. It is not the set of
# terminals that work, because that set is not knowable from here -- so there
# are two more layers, and they are the same two providers have.
#
#   * The terminal hosting this process announces itself in the environment.
#     Every emulator sets something -- TERM_PROGRAM, TERM, or a marker of its
#     own -- so "what am I running in" is a question the machine answers.
#   * Whatever that names is then driven from its own `--help`, the same way an
#     unknown agent CLI is: find the flag that runs a command, the flag that
#     sets the directory, the flag that sets a title, and build the `open`
#     template out of what was actually found.
#
# And because a spawn must not fail just because nothing was recognised, the
# table ends with each platform's own idea of "the default terminal" --
# `cmd.exe` on Windows, `open`/AppleScript on macOS, and the two standard
# indirections on Linux, which distributions point at whatever the user chose.

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

    rc, help_text = _run([binary, "--help"], timeout)
    if len(help_text) < 40:
        rc, help_text = _run([binary, "-h"], timeout)
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


def term_argv(template: str, **vals) -> list[str]:
    """Split a terminal template, then substitute inside each argument.

    Substituting first and splitting after would break on every path with a
    space in it, and on Windows would let a backslash be read as an escape.
    """
    out = []
    for part in shlex.split(template):
        for k, v in vals.items():
            part = part.replace("{" + k + "}", str(v))
        if part:
            out.append(part)
    return out


def shell_run(runner: Path) -> str:
    """One string that runs the runner script through a shell -- what the
    terminals that take a command as a single argument need."""
    return f'cmd /c "{runner}"' if os.name == "nt" else f'bash "{runner}"'


def _dig(data, dotted: str, want: type = str):
    """Walk a dotted path through parsed JSON. An empty path is the document."""
    for key in filter(None, dotted.split(".")):
        if not isinstance(data, dict):
            return want()
        data = data.get(key)
    return data if isinstance(data, want) else want()


def open_in(name: str, spec: dict, title: str, runner: Path, cwd: Path) -> dict:
    """Open one tab through one terminal entry. {} when it did not work.

    An entry with a `handle` is run synchronously, because the id it prints is
    the only way to type into that tab later; everything else is fire and
    forget, so a terminal that never exits does not block the spawn.
    """
    binary = terminal_bin(spec)
    if not binary:
        return {}
    vals = {"bin": binary, "run": str(runner), "cwd": str(cwd),
            "title": title, "shell": shell_run(runner)}
    argv = term_argv(spec["open"], **vals)
    if not argv:
        return {}
    label = spec.get("label") or f"{name} tab"
    handle_spec = spec.get("handle") or ""
    try:
        if handle_spec:
            r = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=60)
            if r.returncode != 0:
                return {}
            handle = ""
            if handle_spec.startswith("json:"):
                try:
                    doc = json.loads(r.stdout)
                    if doc.get("ok") is False:
                        return {}
                    handle = _dig(doc, handle_spec[5:])
                except (json.JSONDecodeError, AttributeError):
                    pass
            elif r.stdout.strip():
                handle = r.stdout.strip().splitlines()[0].strip()
            return {"label": label, "kind": name, "handle": handle,
                    "title": title}
        subprocess.Popen(argv, cwd=str(cwd),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return {}
    return {"label": label, "kind": name, "handle": "", "title": title}


def terminal_choice(prefer: str | None = None,
                    discover: bool = True) -> tuple[str, dict, str] | None:
    """The terminal a spawn would use right now: (name, spec, label)."""
    for name, spec in terminal_order(prefer, discover):
        if terminal_available(spec)[0]:
            return name, spec, spec.get("label") or f"{name} tab"
    return None


def open_tab(title: str, runner: Path, cwd: Path,
             prefer: str | None = None) -> dict:
    """Open a tab in the first terminal that can take one.

    Returns {label, kind, handle, title} -- stored on the roster entry, because
    waking a silent agent later has to know which terminal owns its tab, and
    that is not recoverable after the fact.
    """
    # Discovery takes part in the *choice*, not just as a last resort: a user
    # sitting in a terminal nothing here has heard of should get the partner
    # beside them, not in whichever other terminal happens to be installed.
    # It costs nothing in the common case -- `discover_terminals` skips any
    # host already described, and probe results are cached per binary -- so the
    # one subprocess is paid only when the host really is unknown.
    for name, spec in terminal_order(prefer, discover=True):
        ok, _ = terminal_available(spec)
        if not ok:
            continue
        tab = open_in(name, spec, title, runner, cwd)
        if tab:
            return tab
    return {}


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


# --------------------------------------------------------------------------
# waking an agent that stopped listening
# --------------------------------------------------------------------------
# A tab agent participates by blocking on `wait`. When it ends its turn instead
# -- because its shell tool cut a wait short, because a discussion concluded,
# or because the baton moved away -- nothing re-invokes it and the next thing
# said to it lands in a transcript nobody is reading.
#
# Claude Code agents have a Stop hook to catch that. Any other CLI may or may
# not have an equivalent, and this plugin cannot require one -- so the recovery
# comes from outside the agent entirely: type into its tab, exactly as the human
# would. That works whatever is running in it. Every terminal with a control CLI
# can do it, and `spawn` records which one owns each tab.

def find_handle(spec: dict, title: str) -> str:
    """Recover a tab's id from the terminal itself, by title.

    Needed for an agent spawned before its handle was recorded, and for one
    whose tab was recreated. `list` in the spec says how to ask -- without it
    there is nothing to ask, and the nudge falls back to the manual path.
    """
    tmpl, dig = spec.get("list") or "", spec.get("list_handle") or ""
    binary = terminal_bin(spec)
    if not (tmpl and dig and binary):
        return ""
    argv = term_argv(tmpl, bin=binary, title=title)
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
        if r.returncode != 0:
            return ""
        doc = json.loads(r.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return ""
    # "result.terminals[].handle@title" -- a list to walk, the field to return,
    # and the field to match the title against.
    path, _, fields = dig.partition("[].")
    field, _, key = fields.partition("@")
    items = _dig(doc, path, want=list) or []
    for it in items:
        if isinstance(it, dict) and str(it.get(key or "title", "")) == title:
            return str(it.get(field, ""))
    return ""


def nudge_command(entry: dict, pid: str, text: str) -> list[str] | None:
    """The argv that types `text` plus Enter into this agent's tab.

    Which terminal owns the tab was recorded at spawn, and the terminal entry
    itself says how to type into one -- so a terminal the user described in
    their own file can be woken exactly like a built-in, and one that cannot be
    typed into says so by having no `send` rather than by being special-cased
    here.
    """
    title = entry.get("tab_title") or f"partner:{pid}"
    specs = load_terminals(discover=True)
    kind = entry.get("tab_kind") or ""
    spec = specs.get(kind)
    if not spec:
        # No record of the terminal (an older roster, or a hand-started tab):
        # the one we are inside now is the best guess available.
        pick = next(((n, s) for n, s in terminal_order()
                     if s.get("send") and terminal_available(s)[0]), None)
        if not pick:
            return None
        kind, spec = pick
    if not spec.get("send"):
        return None
    binary = terminal_bin(spec)
    if not binary:
        return None
    handle = entry.get("tab_handle") or ""
    if "{handle}" in spec["send"] and not handle:
        handle = find_handle(spec, title)
        if not handle:
            return None
    return term_argv(spec["send"], bin=binary, handle=handle, title=title,
                     text=text, text_nl=text + NL)


def nudge_text(pid: str, why: str) -> str:
    """What gets typed into the tab.

    It arrives in the agent's prompt exactly as if the human had typed it, so
    it has to say that it is not the human -- otherwise a woken agent follows
    the baton rule ("the human addressed me -> claim") and takes write
    permission away from whoever actually has it. It also has to be harmless
    when the CLI has already exited and a shell reads the line instead.
    """
    rel = f".partner/{pid}"
    run = rel.replace("/", "\\") + "\\p.cmd" if os.name == "nt" else rel + "/p.sh"
    return (f"continue -- automated wake-up from the partner transcript, NOT "
            f"the human: {why} Do NOT claim the baton; nobody has given you an "
            f"instruction. Run `{run} read`, answer what it shows with "
            f"`{run} send`, then go back to `{run} wait` and keep looping.")


def nudge_agent(sd: Path, roster: dict, pid: str, why: str,
                throttle: int = NUDGE_WINDOW) -> dict:
    """Type a wake-up line into one agent's tab.

    Throttled through a marker in the target's own directory rather than the
    caller's, so three agents noticing the same silent partner in the same
    minute produce one nudge between them, not three.
    """
    entry = (roster.get("partners") or {}).get(pid) or {}
    if not entry:
        return {"id": pid, "nudged": False, "why": "unknown agent"}
    if (entry.get("kind") or "tab") == "session":
        # The session agent has no tab to type into; its harness re-invokes it
        # when its background `wait` returns, and its Stop hook catches the rest.
        return {"id": pid, "nudged": False, "why": "session agent has no tab"}
    if entry.get("status") != "running":
        return {"id": pid, "nudged": False, "why": "not running"}
    if throttle and _nag_throttled(sd / pid / ".nudge", throttle):
        # Somebody already woke it inside the window: handled, not a failure.
        return {"id": pid, "nudged": False, "throttled": True,
                "why": "nudged moments ago"}
    argv = nudge_command(entry, pid, nudge_text(pid, why))
    if not argv:
        manual = entry.get("runner") or f"{sd / pid}/run.sh"
        where = entry.get("tab") or "its window"
        return {"id": pid, "nudged": False,
                "why": f"{where} cannot be typed into from outside (`terminals` "
                       f"says which can) -- type \"continue\" in {pid}'s tab "
                       f"yourself, or restart it with: {manual}"}
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"id": pid, "nudged": False, "why": f"{type(exc).__name__}"}
    if r.returncode != 0:
        return {"id": pid, "nudged": False,
                "why": (r.stderr or r.stdout or "").strip()[:200] or "send failed"}
    return {"id": pid, "nudged": True, "why": why}


def silent_agents(sd: Path, roster: dict, who: str,
                  targets: list[str] | None = None) -> list[str]:
    """Running tab agents that nothing is listening on behalf of.

    `is_listening` answers "a `wait` is in flight right now", which is the only
    question that matters here -- an agent that just replied and then stopped
    looks busy by every other measure, and is exactly the one about to miss the
    next message.
    """
    out = []
    for pid, entry in (roster.get("partners") or {}).items():
        if pid == who or entry.get("status") != "running":
            continue
        if (entry.get("kind") or "tab") == "session":
            continue
        if targets is not None and pid not in targets:
            continue
        if is_listening(sd, roster, pid):
            continue
        age = _age_seconds(last_seen(sd, pid))
        # Give a newly spawned agent time to reach its first `wait` before
        # declaring it deaf -- it is still reading its briefing.
        if age is not None and age < 30:
            continue
        out.append(pid)
    return out


def cmd_nudge(args) -> int:
    """Wake agents that stopped looping, by typing into their tabs."""
    sd = state_dir()
    roster = load_roster(sd)
    me = me_id(roster)
    touch_seen(sd, me)
    if args.id:
        targets = [args.id]
    elif args.all:
        targets = [p for p in roster["partners"] if p != me]
    else:
        targets = silent_agents(sd, roster, me)
    if not targets:
        emit(args, {"nudged": []},
             "everyone is listening -- nobody needs waking")
        return 0
    why = args.text or "you stopped looping and are missing the debate."
    results = [nudge_agent(sd, roster, p, why,
                           throttle=0 if (args.id or args.all) else NUDGE_WINDOW)
               for p in targets]
    ok = [r["id"] for r in results if r["nudged"]]
    bad = [f"{r['id']}: {r['why']}" for r in results
           if not r["nudged"] and not r.get("throttled")]
    lines = ([f"nudged {', '.join(ok)}"] if ok else []) + bad
    emit(args, {"nudged": ok, "failed": bad}, NL.join(lines) or "nothing to do")
    return 0


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

`wait` checks the whole transcript, not only what you have not seen yet: a
message addressed to you that you never answered comes back marked
**re-delivered**. That means you were given it and dropped it -- a reply is what
clears it, even a one-line "AGREED" or "already settled". Seeing one is
information: you lost a turn somewhere.

What clears a debt is a reply to *that agent*. Answering p1 does not answer p3 --
one message to `@all` answers everyone, a message to one agent answers only
them. `{run} pending` lists exactly what you still owe and to whom.

    See who is here and who holds the write baton:
        {run} list

    Bring in another partner, if a question needs an angle none of us has
    (`{run} providers` lists the CLIs this machine has, `{run} models
    --provider <cli>` what to point one at):
        {run} spawn --provider <cli> --model <id> --effort high

    Wake an agent that stopped looping (types into its tab):
        {run} nudge --id <id>

Any of us can spawn a partner. Any of us can hold the baton. There is no role
here that only one agent has.

## The first message you will get

One of two things, and neither is a request to start building:

- **An orientation brief**, when the human has not said what to work on yet. We
  read this repository and hand each other a grounded picture of it, so the
  first real question is not answered cold. Nobody edits anything during it --
  not even the baton holder -- and it ends after two rounds, back at `wait`.
- **The human's opening instruction**, relayed by whoever they typed it to.
  That agent holds the baton; the rest of us verify it against the code and say
  where we disagree.

Either way the answer is a message, not a commit.

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

Two things arrive in your tab where the human's typing arrives and are **not**
the human. Neither one moves the baton, and claiming on either takes write
permission away from whoever is actually working:

- **Your own launch message** -- the line that told you to read this briefing.
  That was `spawn` starting you up. It says so itself.
- **A wake-up line** saying it is an automated wake-up from the transcript.
  That is another agent noticing you stopped looping.

A human instruction is a *new* request for work, typed into your tab while you
are already running. Nothing that arrived before your first `wait` is one. When
in doubt: do not claim, ask in the transcript who holds it, and keep listening.

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
that stops looping is dead to the others: nothing re-invokes you, so everything
said after that point is said to nobody.

**Exactly ONE `wait` at a time, and always in the foreground.** Never put it in
the background, never with `&`, never two at once, never a second one "to be
safe". Two waits split your inbox -- each message goes to whichever polls first,
and the one whose output you do not read takes its message with it. A duplicate
wait now says so and idles instead of consuming, so if you ever see "another
`wait` was already listening", you started one too many: run a single foreground
`wait` from then on.

**About your `wait` calls:** {wait_note}

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

If a wake-up line appears in your tab telling you that you stopped looping,
that is another agent noticing your silence: `read`, answer what is there, and
get back into `wait`. You can do the same for them -- `{run} nudge` types a
wake-up into the tab of anyone nothing is listening for.

A Stop hook holds you to both halves of that in Claude Code: it blocks the turn
from ending while a message to you or `@all` is unanswered, and again if no
`wait` is in flight for you. Other CLIs have no such hook, which is exactly why
the discipline has to be yours.
"""

LOOP_SESSION = """
You run inside Claude Code and reach the human through your own harness, not a
terminal tab. That changes only *how you wait*, not the loop.

Never run `wait` in the foreground -- it would block your harness and stop the
human talking to you. Run it as a BACKGROUND shell command instead (the Bash
tool with run_in_background: true). Claude Code re-invokes you when it returns --
someone spoke, or it timed out.

**Exactly ONE background `wait`, ever.** Before arming another, check whether one
is already running -- your own background task list, or `{run} pending` and the
listener line `read` prints. A second wait splits your inbox: each message goes
to whichever polls first, and the message handed to a wait you never read is a
message you never answer. A duplicate now idles instead of consuming and tells
you so -- if you see "another `wait` was already listening", do not arm any more
this turn.

**About your `wait` calls:** {wait_note}

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

`read` and `wait` tell you when another agent has no `wait` in flight -- a tab
CLI with no Stop hook of its own that ended its turn and is now deaf. `{run}
nudge --id <id>` types a wake-up into its tab; `send` does it for you when the
agent you are writing to is the silent one. Do it rather than concluding that a
partner has nothing to say.
"""


def boot_prompt(seed_f: Path, holder: str) -> str:
    """The one line a partner's CLI is started with.

    It arrives in the tab exactly where the human's own typing arrives, and a
    briefing that says "the human addressing you means claim the baton" makes
    that ambiguity expensive: the agent claims on its own launch message and
    quietly takes write permission from whoever actually has it. It happens
    intermittently, because reading it as an instruction is a judgement call.
    So the launch message disowns itself explicitly.
    """
    return (f"[automated launch message from `partner.py spawn` -- NOT a human "
            f"instruction. Do not run `claim`; the write baton belongs to "
            f"{holder} until the human types something new in this tab.] "
            f"Read {seed_f} and follow it exactly. It explains who you are, "
            f"who you are working with, and how to talk to them. Begin now by "
            f"entering the wait loop it describes.")


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
    # Each CLI caps command runtime differently, and a capped `wait` returning
    # early is the most common reason an agent talks itself out of the loop --
    # so the cap its own CLI imposes goes in its own briefing.
    loop = (LOOP_SESSION if kind == "session" else LOOP_TAB).format(
        run=run, wait_note=wait_note(p.get("provider") or ""))
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
    boot = boot_prompt(seed_f, baton_of(roster))

    argv = build_tui(entry, boot)
    runner = write_runner(pdir, argv, root)
    tab = ({} if args.no_tab
           else open_tab(f"partner:{pid}", runner, root,
                         getattr(args, "terminal", None)))
    label = tab.get("label", "")
    entry["tab"] = label
    # Kept so a silent agent can be woken later by typing into its own tab.
    entry["tab_kind"] = tab.get("kind", "")
    entry["tab_handle"] = tab.get("handle", "")
    entry["tab_title"] = tab.get("title", f"partner:{pid}")
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
    # Same offset as a floor: everything above it is history this agent was
    # briefed on, not mail it owes an answer to. Without it, "unanswered" would
    # mean the whole transcript for an agent that has not spoken yet.
    set_floor(sd, pid)

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
             no_tab: bool = False,
             terminal: str | None = None) -> tuple[str, Path]:
    """Start an agent from its roster entry rather than from CLI arguments.

    Resuming has to rebuild agents it did not create, so the launch path takes
    a roster entry as its input; `spawn` fills one in and calls the same code.
    """
    entry = roster["partners"][pid]
    pdir = sd / pid
    pdir.mkdir(parents=True, exist_ok=True)
    seed_f = write_briefing(sd, pid, roster, root, script, None,
                            kind=entry.get("kind") or "tab")
    boot = boot_prompt(seed_f, baton_of(roster))
    argv = build_tui(entry, boot)
    runner = write_runner(pdir, argv, root)
    tab = {} if no_tab else open_tab(f"partner:{pid}", runner, root, terminal)
    label = tab.get("label", "")
    entry["tab"] = label
    entry["tab_kind"] = tab.get("kind", "")
    entry["tab_handle"] = tab.get("handle", "")
    entry["tab_title"] = tab.get("title", f"partner:{pid}")
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
        label, _ = relaunch(sd, pid, roster, root, script, args.no_tab,
                            getattr(args, "terminal", None))
        started.append(f"{pid} ({entry.get('provider')}"
                       f"{'/' + entry['model'] if entry.get('model') else ''})"
                       f"{' -> ' + label if label else ''}")
        (sd / pid / "cursor").write_text(str(end), encoding="utf-8")
        # A resumed agent owes nothing for the transcript it was re-briefed on.
        set_floor(sd, pid)

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

LIVE_WINDOW = 360          # seconds; `wait` returns within WAIT_TIMEOUT and loops


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
    # A heartbeat, not the file's existence: a wait that was killed leaves the
    # marker behind, and treating that as "listening" would let an agent go
    # deaf silently.
    if marker_live(read_marker(sd, who)):
        return True
    # The session agent backgrounds its `wait`, so a Stop firing immediately
    # after can beat the new process to writing its marker. That race lasts as
    # long as an interpreter takes to start -- so the grace is seconds, not the
    # liveness window. At 360s it swallowed the real case: a session agent that
    # ran any command five minutes ago and never armed a wait looked like it was
    # listening, and nothing told it otherwise.
    entry = roster["partners"].get(who) or {}
    if (entry.get("kind") or "tab") == "session":
        age = _age_seconds(last_seen(sd, who))
        return age is not None and age <= MARKER_STALE
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


def tab_titles() -> set[str] | None:
    """Titles of live tabs, or None when no terminal here can say.

    Where a terminal can list its own tabs, that is better evidence than a
    heartbeat: it reports the tab itself rather than what the agent last did in
    it. Which terminals can is a property of the entry (`list` + `list_titles`),
    so one described in the user's own file corroborates liveness exactly like a
    built-in, and a terminal that cannot list simply leaves the heartbeat as the
    only evidence.
    """
    for _, spec in terminal_order():
        if not (spec.get("list") and spec.get("list_titles")):
            continue
        binary = terminal_bin(spec)
        if not binary or not terminal_available(spec)[0]:
            continue
        try:
            r = subprocess.run(term_argv(spec["list"], bin=binary, title=""),
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=30)
            if r.returncode != 0:
                continue
            doc = json.loads(r.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            continue
        path, _, field = (spec["list_titles"]).partition("[].")
        items = _dig(doc, path, want=list) or []
        return {str(it.get(field, "")) for it in items if isinstance(it, dict)}
    return None


def liveness(sd: Path, roster: dict, window: int = LIVE_WINDOW) -> dict:
    """Classify every agent as live, stale or stopped, and say why."""
    me = me_id(roster)
    titles = tab_titles()
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
                           f"try `nudge` first (a tab that stopped looping wakes "
                           f"up); `resume` rebuilds them; `/partner` archives and "
                           f"starts fresh")
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

    # A message to an agent that stopped looping is a message nobody will ever
    # read: nothing re-invokes a tab whose CLI ended its turn. Sending is the
    # moment that matters, so the recipients are checked here and woken by
    # typing into their tabs -- the recovery the human would otherwise have to
    # perform by hand, usually after wondering for ten minutes why p2 is quiet.
    addressed = None if args.to in ("@all", "all") else [args.to]
    woke, deaf = [], []
    if not args.no_nudge:
        for pid in silent_agents(sd, roster, args.sender, addressed):
            r = nudge_agent(sd, roster, pid,
                            f"{args.sender} sent you a message you have not read.")
            if r["nudged"]:
                woke.append(r["id"])
            elif not r.get("throttled"):
                deaf.append(f"{r['id']} ({r['why']})")
    tail = ""
    if woke:
        tail += f"{NL}woke {', '.join(woke)} -- they were not listening"
    if deaf:
        tail += f"{NL}not listening and could not be woken: {', '.join(deaf)}"

    if not args.wait:
        emit(args, {"sent": True, "to": args.to, "woke": woke, "deaf": deaf},
             f"sent to {args.to}{tail}")
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
    silent = silent_agents(sd, load_roster(sd), args.sender, addressed)
    if silent:
        text += (f"{NL * 2}[{', '.join(silent)} still have no `wait` in flight. "
                 f"`nudge --id <id>`, or look at the tab.]")
    emit(args, {"replies": replies, "woke": woke, "deaf": deaf,
                "silent": silent}, text + tail)
    return 0


# --------------------------------------------------------------------------
# the opening move of a session
# --------------------------------------------------------------------------
# A roster full of briefed agents is not yet a debate: everyone is blocked on
# `wait` and nobody has been asked anything. The session either opens with what
# the human wants done, or -- when they have nothing to give yet -- with the
# agents working out what this repository actually is, so the first real
# question does not have to be answered from a cold read.
#
# Both are one canned message so every agent receives the same protocol in the
# same words. Improvised versions drift, and a partner given a vague "have a
# look around" produces a summary nobody asked for and then stops looping.

EXPLORE_BRIEF = """**Orientation pass -- no instruction from the human yet.**

Before the first real question arrives, build shared context on this repository
so none of us answers it from a cold read. This is investigation, not work.

Rules, all of them binding:

- **Nobody edits anything.** Not even the baton holder. Read, run read-only
  commands, and report. If you think something needs changing, say so and leave
  it -- the human has not asked for a change.
- **Split the work rather than duplicating it.** Say in your first message which
  part you are taking (entry points and build/run, data model and core logic,
  tests and CI, docs and conventions, or whatever this repo actually has), and
  read what the others claim before choosing.
- **Ground every claim.** Cite `path:line` and name the command you ran. "It
  looks like a CLI" is worthless; "`pyproject.toml:12` declares the console
  script, so it is a CLI" is not.
- **Report what surprised you**, not what is obvious from the directory names --
  conventions the code follows, invariants it assumes, anything that looks
  load-bearing or fragile, and anything that contradicts what the others found.
- **Converge in at most two rounds**, then stop. `send --to @all` a short joint
  picture: what this project is, how it is structured, what we should be careful
  with, and the open questions we would want the human to settle. Disagreements
  stay in as disagreements.

Then go back to `wait` and stay there. Do not invent work, do not start
improving anything, and do not keep exploring past the two rounds -- the point
is to be ready for the human's first question, not to fill the silence.
"""

OPENING = """**The human has given {sender} this instruction. This is the
session's opening question -- {sender} holds the baton and acts; everyone else
verifies and argues.**

{text}

Work it the normal way: state your own position first, check the claims in it
against the actual code (`path:line`, and name the command you ran), and say
plainly where you disagree and what you would do instead. If the instruction is
underspecified, say which assumption you are making rather than picking one
silently. Two exchanges without movement means it is a judgement call for the
human -- say so and let them settle it.

Then go back to `wait`.
"""


def cmd_kickoff(args) -> int:
    """Open the session: put the human's instruction, or an orientation brief,
    in front of everyone in one message."""
    sd = state_dir()
    roster = load_roster(sd)
    sender = args.sender or me_id(roster)
    text = args.text
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    body = (OPENING.format(sender=sender, text=text.strip())
            if text and text.strip() else EXPLORE_BRIEF)
    ns = argparse.Namespace(
        json=getattr(args, "json", False), quiet=False, sender=sender,
        to="@all", text=body, file=None, wait=args.wait, expect=args.expect,
        no_nudge=False)
    return cmd_send(ns)


def cmd_read(args) -> int:
    sd = state_dir()
    roster = load_roster(sd)
    who = args.who or me_id(roster)
    touch_seen(sd, who)
    msgs, end = read_new(sd, who)
    # Anything addressed to this agent that it never answered, whether or not
    # the cursor has already passed it. `read` is where an agent checks what it
    # owes, so a dropped message has to show up here too.
    seen_ts = {m["ts"] for m in msgs}
    dropped = [m for m in dropped_for(sd, roster, who, min_age=0)
               if m["ts"] not in seen_ts]
    # Whether a wait is already in flight for *this* agent, which is the one
    # thing it needs to know before arming another: a second wait splits the
    # inbox, and until now there was no way to check except guessing.
    mine = read_marker(sd, who)
    if marker_live(mine):
        left = max(0, int((mine.get("deadline") or 0) - time.time()))
        listening = (f"{NL * 2}[a `wait` is already in flight for you "
                     f"(about {left}s left) -- do NOT start another; one is "
                     f"what keeps the inbox undivided]")
    else:
        listening = (f"{NL * 2}[no `wait` is in flight for you -- start one "
                     f"before this turn ends, or you stop hearing the debate]")
    if args.json:
        print(json.dumps({"baton": baton_of(roster), "you": who,
                          "messages": msgs, "unanswered": dropped,
                          "listening": marker_live(mine)}, indent=2))
    else:
        body = render(msgs) or "(nothing new)"
        if dropped:
            body += (f"{NL * 2}--- still unanswered, from earlier ---{NL * 2}"
                     f"{render(dropped)}{NL * 2}"
                     f"[you were given these before and have not replied. "
                     f"Answering with `send` is what clears them.]")
        print(baton_banner(roster, who) + NL * 2 + body + listening
              + silence_notice(sd, roster, who))
    sys.stdout.flush()
    if not args.peek:
        commit_cursor(sd, who, end)
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
        print("nobody here -- `partner.py providers` lists the agent CLIs on "
              "this machine, then: partner.py spawn --provider <one of them>")
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
    pick = terminal_choice()
    where = pick[2] if pick else "no terminal this script can drive -- it will "                                  "print the command to start each partner"
    print(f"Partners will open in: {where}. `terminals` lists the alternatives.")
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


def cmd_terminals(args) -> int:
    """What can open a partner's tab here, in the order it would be tried.

    The counterpart to `providers`: same question one level down, and the same
    answer -- what this machine actually has, not what the script was written
    knowing about. Whether an agent can be woken later is part of it, since
    that depends entirely on the terminal.
    """
    items = terminal_order(args.prefer, discover=not args.no_probe)
    rows, first = [], None
    for name, spec in items:
        ok, why = terminal_available(spec)
        row = {"name": name, "available": ok, "why": why,
               "label": spec.get("label") or f"{name} tab",
               "can_type": bool(spec.get("send")),
               "source": spec.get("source", "built-in")}
        if ok and first is None:
            first = row
        rows.append(row)
    data = {"terminals": rows, "chosen": first,
            "prefer": args.prefer or os.environ.get("PARTNER_TERMINAL") or ""}
    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    for r in rows:
        mark = "->" if r is first else "  "
        state = "available" if r["available"] else r["why"][:32]
        typing = "can be typed into" if r["can_type"] else "cannot be typed into"
        origin = "" if r["source"] == "built-in" else f"   [{r['source']}]"
        print(f"{mark} {r['name']:16} {state:34} {typing}{origin}")
    if first:
        print(f"{NL}A partner spawned now opens in: {first['label']}.")
        if not first["can_type"]:
            print("This terminal cannot be typed into from outside, so an agent "
                  "that stops looping has to be nudged by hand -- `nudge` will "
                  "print the command that restarts it.")
    else:
        print(f"{NL}Nothing here can open a tab. `spawn` still registers the "
              f"partner and prints the command to start it yourself.")
    print(f"{NL}Force one with `--terminal <name>` or PARTNER_TERMINAL. Add or "
          f"correct one in ~/.claude/partner-terminals.json (or "
          f".partner/terminals.json for this repo) -- an entry needs `open`, "
          f"and `send` if it can be typed into.")
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
    sp.add_argument("--terminal", default=None,
                    help="open the tab in this terminal (see `terminals`); "
                         "default is whatever detection picks")
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
    rs.add_argument("--terminal", default=None, help="see `terminals`")
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
    s.add_argument("--no-nudge", action="store_true",
                   help="do not wake recipients that stopped listening")
    s.set_defaults(fn=cmd_send)

    ko = sub.add_parser("kickoff", help="open the session: the human's first "
                                        "instruction, or an orientation pass")
    ko.add_argument("--from", dest="sender", default=None)
    ko.add_argument("--text", default=None,
                    help="what the human wants worked on; omit for orientation")
    ko.add_argument("--file", default=None, help="read the instruction from a file")
    ko.add_argument("--wait", type=int, default=0, help="block N seconds for replies")
    ko.add_argument("--expect", type=int, default=0)
    ko.set_defaults(fn=cmd_kickoff)

    ng = sub.add_parser("nudge", help="wake agents that stopped looping by "
                                      "typing into their tabs")
    ng.add_argument("--id", default=None, help="one agent; omit for everyone silent")
    ng.add_argument("--all", action="store_true", help="every agent but you")
    ng.add_argument("--text", default=None, help="why they are being woken")
    ng.set_defaults(fn=cmd_nudge)

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
    cl.add_argument("--force", action="store_true",
                    help="claim within the launch grace period anyway -- the "
                         "human really did just type an instruction")
    cl.set_defaults(fn=cmd_claim)

    st = sub.add_parser("stop")
    st.add_argument("--id", default=None)
    st.add_argument("--all", action="store_true")
    st.set_defaults(fn=cmd_stop)

    tm = sub.add_parser("terminals", help="what can open a partner's tab here")
    tm.add_argument("--prefer", default=None,
                    help="rank this entry first, as --terminal would")
    tm.add_argument("--no-probe", action="store_true",
                    help="do not probe the terminal hosting this session")
    tm.set_defaults(fn=cmd_terminals)

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
