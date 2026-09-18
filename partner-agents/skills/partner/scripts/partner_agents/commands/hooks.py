"""The Claude Code hooks: Stop and UserPromptSubmit.

hooks/hook.sh calls `partner.py hook-stop` and `partner.py hook-prompt`. Both
read the hook payload on stdin and fail open: no .partner/, no roster entry, or
a stopped agent means nothing happens.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from ..idle import break_idle, mark_active
from ..presence import is_listening
from ..state import baton_of, load_roster, me_id, read_pause, state_dir
from ..transcript import pending_for, render
from ..util import NL, nag_throttled, unlink_quietly, write_float


def hook_prompt(sd: Path, roster: dict, who: str, prompt: str) -> None:
    """UserPromptSubmit: a turn starts, and a human message resumes the session.

    The boot prompt and a wake-up line land here too, and neither is the human
    writing -- both disown themselves in their first words.
    """
    entry = roster["partners"].get(who) or {}
    if (entry.get("kind") or "tab") == "session":
        write_float(sd / who / "turn", time.time())
        break_idle(sd, who)
    automated = prompt.lstrip().startswith(
        ("continue -- automated wake-up", "[automated launch message"))
    if not automated and read_pause(sd):
        mark_active(sd, roster, who, "was written to by the human")


def cmd_hook_stop(args) -> int:
    """Stop-hook entry point: block if a reply is owed or nobody is listening,
    and otherwise record that this agent's turn is over."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}
    ctx = _hook_context(payload)
    if not ctx:
        return 0
    sd, roster, who, entry = ctx
    reason = stop_block_reason(sd, roster, who, entry)
    if reason:
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0
    if (entry.get("kind") or "tab") == "session" and (sd / who / "turn").exists():
        # The turn really ends here. Its background `wait` kept polling through
        # it, so the idle streak that built up is not evidence of being
        # finished -- being finished starts now.
        unlink_quietly(sd / who / "turn")
        write_float(sd / who / "idle_since", time.time())
    return 0


def cmd_hook_prompt(args) -> int:
    """UserPromptSubmit entry point. Never blocks, prints nothing, fails open."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}
    ctx = _hook_context(payload)
    if ctx:
        sd, roster, who, _ = ctx
        try:
            hook_prompt(sd, roster, who, str(payload.get("prompt") or ""))
        except OSError:
            pass
    return 0


def _hook_context(payload: dict):
    """The running agent a hook fired for, or None when there is none."""
    cwd = payload.get("cwd")
    if cwd:
        try:
            os.chdir(cwd)
        except OSError:
            pass
    try:
        sd = state_dir()
    except OSError:
        return None
    if not (sd / "roster.json").exists():
        return None
    roster = load_roster(sd)
    who = me_id(roster)
    entry = roster["partners"].get(who)
    if not entry or entry.get("status") != "running":
        return None
    return sd, roster, who, entry


def stop_block_reason(sd: Path, roster: dict, who: str, entry: dict) -> str:
    """Why this agent may not end its turn yet, or "".

    A tab agent blocked on `wait` never reaches a Stop; the session agent does,
    every turn, and nothing else re-invokes it once the human's attention moves
    to another tab. An empty answer lets the stop through.
    """
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
            return ""
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(size, encoding="utf-8")
        reason = (
            f"{len(pend)} message(s) in the partner transcript are addressed to "
            f"you ({who}) and still unanswered:{NL * 2}{render(pend)}{NL * 2}"
            f"Do not stop. Run `{run} read`, then follow the debate protocol -- "
            f"verify against the code and `{run} send` your reply. You do not "
            f"hold the baton, so advise; do not edit files.")
        return reason

    # 2. Nothing is waiting for an answer -- but is this agent still listening?
    # A discussion ending, or the baton moving to somebody else, is exactly when
    # an agent decides it is done and stops; from that moment it is deaf, and
    # the next thing said to it lands in a transcript nobody is reading. Every
    # agent goes back to `wait` at the end of every turn, and this is what
    # enforces it.
    if is_listening(sd, roster, who):
        return ""
    if read_pause(sd) and (entry.get("kind") or "tab") != "session":
        # Paused: a tab agent leaving the loop is the point. The session agent
        # is still held to its one background wait -- it sleeps for free, and
        # it is how this session hears a resume that starts in a tab.
        return ""
    if nag_throttled(sd / who / ".stop-nag-live", 120):
        return ""
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
    return reason
