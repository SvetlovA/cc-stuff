"""The .partner/ directory: where it is, the roster, and the wrapper scripts.

roster.json names every agent and who holds the write baton. Identity is not
read from it, though -- each agent runs through its own wrapper, which exports
PARTNER_ID, so the same shared roster resolves to a different "me" per process.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .timing import IDLE_PAUSE
from .util import NL, clip_lines, run_git


def repo_root(start: Path | None = None) -> Path:
    """Anchor state at the git root so every tab agrees on one .partner dir."""
    cur = (start or Path.cwd()).resolve()
    for cand in [cur, *cur.parents]:
        if (cand / ".git").exists():
            return cand
    return cur


def state_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / ".partner"


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


def session_host() -> str:
    """Detect the host of the calling shell; explicit --me-provider wins."""
    if os.environ.get("CODEX_THREAD_ID"):
        return "codex"
    if os.environ.get("CLAUDE_CODE_SESSION_ID"):
        return "claude"
    return ""


def host_session(host: str) -> str:
    """Return the id the host also supplies to hooks as session_id."""
    key = {"claude": "CLAUDE_CODE_SESSION_ID", "codex": "CODEX_THREAD_ID"}.get(host)
    return os.environ.get(key, "") if key else ""


def bind_session(entry: dict, host: str | None = None) -> None:
    host = host or session_host() or entry.get("provider") or ""
    entry["session_host"] = host
    entry["session_id"] = host_session(host)
    entry.pop("cc_session", None)  # legacy Claude-only binding


def is_bound_session(entry: dict, sid: str | None = None,
                     host: str | None = None) -> bool:
    """Is this process the session agent `entry` names?

    The session agent has no wrapper of its own, so `me_id` falls back to
    roster["self"] for *every* process in the repo without PARTNER_ID -- and
    the plugin's hooks fire in every enabled host session opened there. Without
    this check, an unrelated session typing a prompt counted as the human
    writing to the session agent: it lifted the pause, was then told by the
    Stop hook to run `wait`, and that `wait` recorded the unrelated tab as the
    session agent's -- so later wake-ups were typed into it. `init` and
    `resume` record which session owns the entry; nothing else may act as it.
    """
    bound_host = entry.get("session_host") or "claude"
    if host is not None and host != bound_host:
        return False
    sid = host_session(bound_host) if sid is None else sid
    bound_id = entry.get("session_id", entry.get("cc_session", ""))
    # An unbound session cannot claim a hook with an id. Preserve manual use
    # outside either host, where both ids are empty.
    return (bound_id or "") == sid


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
        common = run_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
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


def repo_snapshot(root: Path) -> str:
    """What the working tree looks like right now.

    A partner spawned mid-task needs to see the work in progress, not just
    committed history -- the uncommitted diff is usually the very thing being
    argued about. Collected automatically so that even a hurried spawn hands
    the partner something real to react to.
    """
    branch = run_git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if not branch:
        return ""
    out = [f"Repo: {root.name}   Branch: {branch}"]
    log = run_git(root, "log", "--oneline", "-5")
    if log:
        out.append(f"{NL}Recent commits:{NL}{log}")
    status = run_git(root, "status", "--short")
    if not status:
        out.append(f"{NL}Working tree is clean.")
        return NL.join(out)
    out.append(f"{NL}Uncommitted changes:{NL}{clip_lines(status, 40, 'files')}")
    stat = run_git(root, "diff", "--stat", "HEAD")
    if stat:
        out.append(f"{NL}Diff against HEAD:{NL}{clip_lines(stat, 40, 'files')}")
    return NL.join(out)


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


def pause_after(roster: dict) -> int:
    """The idle threshold for this session; 0 means never pause."""
    try:
        return max(0, int(roster.get("idle_pause", IDLE_PAUSE)))
    except (TypeError, ValueError):
        return IDLE_PAUSE


def read_pause(sd: Path) -> dict:
    try:
        return json.loads((sd / "paused.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# The entry script every wrapper, hook and briefing calls. Kept as the one
# stable path: `.partner/<id>/p.cmd` files written by older versions point
# at it, so the code behind it can move without breaking a live session.
SCRIPT = Path(__file__).parent.parent.resolve() / "partner.py"
