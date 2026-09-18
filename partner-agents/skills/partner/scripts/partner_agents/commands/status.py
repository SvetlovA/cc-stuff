"""Commands that report on the session: list, state, idle."""
from __future__ import annotations

import json

from ..idle import agent_busy, idle_verdict
from ..presence import liveness
from ..sessions import read_sessions
from ..state import (
    baton_of, load_roster, me_id, pause_after, read_pause, save_roster, state_dir,
)
from ..transcript import tail_msgs
from ..util import NL


def cmd_idle(args) -> int:
    """Why the session is or is not paused, and the threshold that decides it."""
    sd = state_dir()
    roster = load_roster(sd)
    if args.after is not None:
        roster["idle_pause"] = max(0, args.after)
        save_roster(sd, roster)
    paused = read_pause(sd)
    agents = {p: agent_busy(sd, roster, p) or "finished"
              for p, e in roster["partners"].items() if e.get("status") == "running"}
    verdict = None if paused else idle_verdict(sd, roster)
    data = {"idle_pause": pause_after(roster), "paused": paused or None,
            "agents": agents, "would_pause": verdict[0] if verdict else None,
            "why": paused.get("why") if paused else verdict[1]}
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    limit = pause_after(roster)
    print(f"pauses once every agent has been finished for {limit}s" if limit
          else "idle pause off -- loops never stop on their own")
    print(f"PAUSED since {paused.get('since')} -- the next message resumes"
          if paused else f"not paused: {verdict[1]}")
    for p, why in agents.items():
        print(f"  {p:8} {why}")
    return 0


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
    paused = read_pause(sd)
    past = read_sessions(sd)

    if paused:
        rec, why = "add", (f"session paused since {paused.get('since')} -- "
                           f"every agent finished its work. The partners are "
                           f"idle, not gone: the next `send` or `claim` wakes "
                           f"them all")
    elif live:
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

    idle = None if paused else idle_verdict(sd, roster)[1]
    data = {"self": me, "baton": baton_of(roster), "live": live, "stale": stale,
            "paused": paused or None, "idle": idle, "agents": live_map, "sessions": [
                {"id": m["id"], "label": m.get("label"),
                 "agents": list(m.get("agents", {})),
                 "messages": m.get("messages", 0)} for m in past],
            "messages": len(tail_msgs(sd, 100000)), "recommend": rec, "why": why}
    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    print(f"you are {me}; baton held by {data['baton']}")
    for pid, v in live_map.items():
        mark = {"live": "live   ", "stale": "STALE  ", "stopped": "stopped",
                "paused": "paused "}[v["state"]]
        print(f"  {pid:8} {mark} {v['why']}")
    if idle:
        print(f"idle pause: {idle}")
    if past:
        print(f"{NL}{len(past)} archived session(s):")
        for m in past[:5]:
            print(f"  {m['id']}   {m.get('label', '')}")
    print(f"{NL}suggested: {rec} -- {why}")
    return 0


def cmd_list(args) -> int:
    sd = state_dir()
    roster = load_roster(sd)
    if args.json:
        print(json.dumps(roster, indent=2))
        return 0
    holder = baton_of(roster)
    print(f"baton: {holder}   (only the baton holder edits files)")
    paused = read_pause(sd)
    if paused:
        print(f"PAUSED since {paused.get('since')} ({paused.get('why')}) -- "
              f"every agent finished; the next message resumes")
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
