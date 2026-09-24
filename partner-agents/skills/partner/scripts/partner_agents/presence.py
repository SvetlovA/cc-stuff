"""Who is alive, and who is listening right now.

roster.json says an agent is "running" because nothing has told it otherwise.
Close the tab and the claim survives, so it cannot be trusted to decide
whether to add a partner or start over. Liveness is evidence instead: every
agent stamps <id>/lastseen each time it acts, so being alive means having
done something recently rather than having been started once.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .state import me_id, read_pause
from .terminals.tabs import tab_titles
from .timing import LIVE_WINDOW, MARKER_STALE, TURN_STALE
from .util import age_seconds, read_float, utcnow


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


def touch_seen(sd: Path, pid: str) -> None:
    try:
        d = sd / pid
        d.mkdir(parents=True, exist_ok=True)
        (d / "lastseen").write_text(utcnow(), encoding="utf-8")
    except OSError:
        pass                # a heartbeat is never worth failing a command over


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
    # deaf silently. No grace on `lastseen` either -- every `send` refreshes it,
    # so a session agent that replied and stopped without arming a wait looked
    # like one whose wait was still starting. The Stop hook covers that race
    # with `listening_soon` instead.
    return marker_live(read_marker(sd, who))


def listening_soon(sd: Path, roster: dict, who: str, grace: float) -> bool:
    """Is a `wait` in flight, or about to prove it is within `grace` seconds?

    A background `wait` armed just before a Stop has not started its
    interpreter yet, so its marker does not exist. Looking again for a few
    seconds tells that apart from an agent that armed nothing.
    """
    end = time.time() + grace
    while True:
        if is_listening(sd, roster, who):
            return True
        if time.time() >= end:
            return False
        time.sleep(0.25)


def turn_open(sd: Path, pid: str) -> bool:
    """Is a Claude Code session agent in the middle of a turn?

    Its background `wait` stays in flight while it works on what the human
    asked, so `is_listening` cannot tell a session agent that is done from one
    that is busy. The prompt hook opens the turn, the Stop hook closes it.
    """
    opened = read_float(sd / pid / "turn")
    return opened is not None and time.time() - opened < TURN_STALE


def last_seen(sd: Path, pid: str) -> str | None:
    f = sd / pid / "lastseen"
    if not f.exists():
        return None
    try:
        return f.read_text(encoding="utf-8").strip() or None
    except OSError:
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
        age = age_seconds(last_seen(sd, pid))
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
        if out[pid]["state"] == "stale" and read_pause(sd):
            # Quiet because it was told to be, not because its tab died.
            out[pid] = {"state": "paused", "age": age,
                        "why": "paused, all work finished -- the next message wakes it"}
    return out
