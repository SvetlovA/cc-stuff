"""Pausing every agent's loop once all of them have finished their work.

An idle `wait` that times out hands control back to its model, which spends a
turn deciding to run `wait` again. One cycle is cheap; three agents cycling
every 90s through a lunch break is most of the bill for the session, and none
of it buys anything. So once every agent has finished its work, the session
pauses: tab agents end their turn instead of re-arming, and the session
agent's background `wait` sleeps without returning -- a process polling a
file costs nothing, a model turn does.

"Finished" has to be proven per agent, because pausing an agent mid-work
drops whatever it was about to say. Going back to `wait` is how an agent
declares it is done -- its briefing says so -- and the evidence is checked
rather than trusted: no unanswered message, no turn in progress, and a
continuous IDLE_PAUSE in `wait` since the last time `wait` handed it work.

Resuming needs nobody: the first message to any agent -- `send`, `kickoff`,
`claim`, `spawn`, or the human writing to a Claude agent -- lifts the pause
and types a wake-up into every tab. That is also why a session never pauses
on its own while one of its tabs sits in a terminal that cannot be typed into.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .presence import is_listening
from .prompts import PAUSE_NOTICE
from .state import baton_of, pause_after
from .timing import REARM_GAP, TURN_STALE
from .transcript import append_msg, pending_for, tail_msgs
from .util import (
    NL, age_seconds, nag_throttled, read_float, unlink_quietly, utcnow, write_float,
)
from .waking import nudge_agent, nudge_command


_WAKEABLE: dict[str, bool] = {}


def note_idle(sd: Path, who: str) -> None:
    """Called by a polling `wait`: extend this agent's idle streak, or start one."""
    now = time.time()
    since = read_float(sd / who / "idle_since")
    last = read_float(sd / who / "lastwait") or 0.0
    if since is None or now - last > REARM_GAP:
        write_float(sd / who / "idle_since", now)
    write_float(sd / who / "lastwait", now)


def break_idle(sd: Path, who: str) -> None:
    """This agent has work: whatever idle streak it had is over."""
    unlink_quietly(sd / who / "idle_since")


def idle_for(sd: Path, pid: str) -> float | None:
    since = read_float(sd / pid / "idle_since")
    return None if since is None else time.time() - since


def turn_open(sd: Path, pid: str) -> bool:
    """Is a Claude Code session agent in the middle of a turn?

    Its background `wait` stays in flight while it works on what the human
    asked, so `is_listening` cannot tell a session agent that is done from one
    that is busy. The prompt hook opens the turn, the Stop hook closes it.
    """
    opened = read_float(sd / pid / "turn")
    return opened is not None and time.time() - opened < TURN_STALE


def session_done_without_wait(sd: Path, roster: dict, pid: str) -> bool:
    """A session agent with no `wait` armed: does that still count as finished?

    For a tab agent, "not in `wait`" means a model turn is running. A session
    agent says the same thing with its turn marker, and with that marker
    closed it is simply sitting at the prompt, and the human's next message
    wakes it through the prompt hook. Treating that state as "still working"
    blocks every pause for good: this is how p2 kept looping for ten minutes
    after p1 stopped re-arming its wait.
    """
    entry = roster["partners"].get(pid) or {}
    if (entry.get("kind") or "tab") != "session":
        return False
    # TODO(you): return whether a session agent whose turn is closed but who
    # has no `wait` in flight should count as finished for the pause verdict.
    return False


def agent_busy(sd: Path, roster: dict, pid: str) -> str:
    """Why this agent has not finished its work -- or "" when it has."""
    if turn_open(sd, pid):
        return "mid-turn"
    if (not is_listening(sd, roster, pid)
            and not session_done_without_wait(sd, roster, pid)):
        return "not in `wait` -- still working"
    owed = pending_for(sd, roster, pid)
    if owed:
        return f"owes a reply to {', '.join(sorted({m['from'] for m in owed}))}"
    held, need = idle_for(sd, pid), pause_after(roster)
    if held is None or held < need:
        return f"finished {int(held or 0)}s ago, needs {need}s"
    return ""


def wakeable(roster: dict, pid: str) -> bool:
    """Can this agent be brought back from a pause without the human?"""
    entry = roster["partners"].get(pid) or {}
    if (entry.get("kind") or "tab") == "session":
        return True              # its wait sleeps through the pause instead
    if pid not in _WAKEABLE:
        # Cached per process: resolving a terminal may probe it, and a session
        # that cannot pause re-asks this every IDLE_CHECK.
        _WAKEABLE[pid] = nudge_command(entry, pid, "wake") is not None
    return _WAKEABLE[pid]


def last_activity(sd: Path, roster: dict) -> float:
    """When anybody last did something other than wait, as epoch seconds.

    `lastseen` cannot answer this -- every polling `wait` refreshes it. Commands
    that are work stamp `activity`; the last real message and the newest agent's
    start cover transcripts written before that stamp existed.
    """
    stamps = [read_float(sd / "activity") or 0.0]
    now = time.time()
    for m in reversed(tail_msgs(sd, 100000)):
        if m["from"] != "system":
            age = age_seconds(m.get("ts"))
            if age is not None:
                stamps.append(now - age)
            break
    for entry in roster.get("partners", {}).values():
        age = age_seconds(entry.get("started"))
        if age is not None:
            stamps.append(now - age)
    return max(stamps)


def idle_verdict(sd: Path, roster: dict) -> tuple[bool, str]:
    """Whether the session should pause now -- and, when not, why not."""
    limit = pause_after(roster)
    if not limit:
        return False, "idle pause is off"
    quiet = time.time() - last_activity(sd, roster)
    if quiet < limit:
        return False, f"last message {int(quiet)}s ago, needs {limit}s"
    running = [p for p, e in roster["partners"].items()
               if e.get("status") == "running"]
    busy = [f"{p} {why}" for p in running
            for why in [agent_busy(sd, roster, p)] if why]
    if busy:
        return False, "; ".join(busy)
    # Checked last: it is the only expensive question, and a pause that strands
    # a tab nobody can wake saves tokens by losing the partner.
    stuck = [p for p in running if not wakeable(roster, p)]
    if stuck:
        return False, (f"{', '.join(stuck)} could not be woken again by a "
                       f"message, so the loops keep running")
    return True, f"every agent finished its work and stayed idle for {limit}s"


def pause_banner(paused: dict, sleeper: bool) -> str:
    since = paused.get("since", "?")
    if sleeper:
        return (f"[session paused since {since}: every agent finished. Your "
                f"background `wait` sleeps until the next message and costs "
                f"nothing -- keep exactly one armed. Your first `send` or "
                f"`claim` resumes everyone.]")
    return (f"[session paused since {since}: every agent finished. Do NOT run "
            f"`wait` again -- end your turn now. This is the one time leaving "
            f"the loop is right: the next message to any of us types a wake-up "
            f"into your tab. If the human writes to you meanwhile, `claim` "
            f"first -- that resumes everyone.]")


def is_pause_notice(m: dict) -> bool:
    return (m.get("from") == "system"
            and m.get("body", "").startswith(PAUSE_NOTICE.split("{why}")[0]))


def pause_session(sd: Path, roster: dict, by: str, why: str) -> bool:
    """Pause, once. Several waits reach the same verdict in the same second;
    creating the marker exclusively means exactly one of them posts the notice."""
    try:
        with (sd / "paused.json").open("x", encoding="utf-8") as fh:
            json.dump({"since": utcnow(), "by": by, "why": why}, fh)
    except OSError:
        return False
    append_msg(sd, "system", "@all", PAUSE_NOTICE.format(why=why), baton_of(roster))
    return True


def mark_active(sd: Path, roster: dict, who: str, why: str) -> dict:
    """Record that real work happened, and resume the session if it was paused.

    Called by every command that is a message or an instruction rather than
    listening. The stamp is what keeps a session that was just woken from
    pausing again on the next check.
    """
    write_float(sd / "activity", time.time())
    break_idle(sd, who)
    try:
        (sd / "paused.json").unlink()
    except OSError:
        return {"resumed": False, "woke": [], "failed": []}   # not paused, or lost the race
    append_msg(sd, "system", "@all",
               f"**Session resumed** -- {who} {why}. Everyone back into the "
               f"loop: `read`, then `wait`.", baton_of(roster))
    woke, failed = [], []
    for pid, entry in roster["partners"].items():
        if pid == who or entry.get("status") != "running":
            continue
        if (entry.get("kind") or "tab") == "session" or is_listening(sd, roster, pid):
            continue             # the post above already reaches a live wait
        age = age_seconds(entry.get("started"))
        if age is not None and age < 30:
            continue             # still booting; typing into it would garble that
        # Unthrottled: an unrelated nudge a minute ago must not swallow the one
        # line that ends this agent's pause. Stamped afterwards, so the caller's
        # own nudge pass (`send`) does not type a second one.
        r = nudge_agent(sd, roster, pid,
                        f"the session resumed after a pause ({who} {why}).",
                        throttle=0)
        if r["nudged"]:
            woke.append(pid)
            nag_throttled(sd / pid / ".nudge", 0)
        else:
            failed.append(f"{pid} ({r['why']})")
    return {"resumed": True, "woke": woke, "failed": failed}


def resume_note(res: dict) -> str:
    if not res.get("resumed"):
        return ""
    out = f"{NL}session was paused -- resumed"
    if res["woke"]:
        out += f", woke {', '.join(res['woke'])}"
    if res["failed"]:
        out += f"{NL}could not wake: {', '.join(res['failed'])}"
    return out
