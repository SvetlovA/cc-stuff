"""Commands agents use to talk: wait, send, read, and moving the baton."""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import time
from pathlib import Path

from ..idle import (
    break_idle, idle_verdict, is_pause_notice, mark_active, note_idle, pause_banner,
    pause_session, resume_note,
)
from ..presence import marker_live, read_marker, touch_seen, write_marker
from ..prompts import EXPLORE_BRIEF, OPENING
from ..state import baton_of, load_roster, me_id, read_pause, save_roster, state_dir
from ..timing import CLAIM_GRACE, IDLE_CHECK, NUDGE_WINDOW, REDELIVER_WINDOW
from ..transcript import (
    append_msg, baton_banner, commit_cursor, dropped_for, parse_msgs, pending_for,
    read_new, render, tail_msgs,
)
from ..util import (
    NL, age_seconds, emit, nag_throttled, process_cmdline, unlink_quietly,
)
from ..waker import ensure_waker
from ..waking import (
    adopt_own_tab, nudge_agent, silence_notice, silent_agents, sleeps_through_pause,
)


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
    # Whoever runs this is in its own tab right now; recording it is what lets a
    # pause stop this agent and a message type it awake again, like any other.
    adopt_own_tab(sd, roster, who)
    ensure_waker(sd)             # typing wake-ups for agents that cannot
    # Only an agent that cannot be typed awake keeps a wait through a pause --
    # see sleeps_through_pause. Everyone else stops, whoever started the session.
    sleeper = sleeps_through_pause(roster, who)
    next_idle = 0.0              # check at once: a 10s-capped wait never reaches 15
    announced = False            # a sleeper says once that it is sleeping
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
            if sleeper and msgs and all(is_pause_notice(m) for m in msgs):
                # The notice tells tab agents to stop. Handing it to the session
                # agent would complete its background wait, and the harness
                # re-invokes the model for that -- a turn spent being told to
                # sleep, which is the opposite of pausing. Swallow it and sleep.
                commit_cursor(sd, who, end)
                msgs = []
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
                break_idle(sd, who)      # handed work: not finished until it is
                return 0

            # Nothing new -- but the cursor only records what was handed over,
            # not what was answered. Anything addressed to this agent that it
            # never replied to comes back here, which is what stops a dropped
            # message from needing another agent to notice and ping.
            roster = load_roster(sd)
            dropped = dropped_for(sd, roster, who)
            if dropped and not nag_throttled(sd / who / ".redeliver",
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
                break_idle(sd, who)
                return 0

            # Nothing handed over on this poll: this agent is sitting finished.
            note_idle(sd, who)
            paused = read_pause(sd)
            if paused and not sleeper:
                emit(args, {"messages": [], "paused": paused},
                     pause_banner(paused, sleeper=False))
                return 0
            if not paused and time.time() >= next_idle:
                next_idle = time.time() + IDLE_CHECK
                ok, why = idle_verdict(sd, roster)
                if ok and pause_session(sd, roster, who, why):
                    continue     # tab agents get the notice like any message
            if paused:
                # Sleeping through the pause: no deadline, so no return, so no
                # model turn. Lifting the pause posts a message, which ends it.
                if not announced and not args.json:
                    print(pause_banner(paused, sleeper=True), flush=True)
                announced = True
                time.sleep(args.poll)
                continue
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
        res = mark_active(sd, roster, who, "took an instruction from the human")
        emit(args, {"baton": who, "changed": False, **res},
             f"baton: already yours ({who}) -- go ahead{resume_note(res)}")
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
        age = age_seconds(entry.get("started"))
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
    res = mark_active(sd, roster, who, "took an instruction from the human")
    roster["baton"] = who
    save_roster(sd, roster)
    append_msg(sd, "system", "@all",
               f"**{who}** was given an instruction directly by the human and has "
               f"taken the write baton from **{prev}**. {who} edits files from now "
               f"on; everyone else advises until the human turns to them. "
               f"**{prev}**: you are an advisor again -- go back to `wait` so you "
               f"hear what follows.", who)
    emit(args, {"baton": who, "previous": prev, "changed": True, **res},
         f"baton: {prev} -> {who} (you may edit now){resume_note(res)}")
    return 0


def cmd_unwait(args) -> int:
    """Stop this agent's own `wait`: the one process its marker names.

    Agents used to do this by killing every process whose command line matched
    *partner.py*wait* -- which also matched partner CLIs, whose launch prompt
    said the same words, and took them down. The marker records the pid, so
    nothing needs matching; and the pid is checked to still be a partner.py
    wait before anything is killed, since pids are reused.
    """
    sd = state_dir()
    roster = load_roster(sd)
    who = me_id(roster)
    mark = read_marker(sd, who)
    pid = mark.get("pid")
    if not pid:
        emit(args, {"stopped": None}, f"no `wait` is in flight for {who}")
        return 0
    cmdline = process_cmdline(pid)
    # A Python interpreter running partner.py with `wait` as its subcommand --
    # not merely a command line that mentions both, which is what went wrong.
    ours = bool(re.match(r'\s*"?[^"]*python[^"\s]*"?\s', cmdline, re.I)
                and re.search(r'partner\.py"?\s+wait(\s|$)', cmdline, re.I))
    if not ours:
        unlink_quietly(sd / who / "waiting")     # the pid is gone or reused
        emit(args, {"stopped": None},
             f"the `wait` recorded for {who} (pid {pid}) is not running any more"
             f" -- nothing killed")
        return 0
    try:
        os.kill(int(pid), signal.SIGTERM)
    except OSError as exc:
        emit(args, {"stopped": None, "error": str(exc)},
             f"could not stop pid {pid}: {exc}")
        return 1
    if read_marker(sd, who).get("pid") == pid:
        unlink_quietly(sd / who / "waiting")
    emit(args, {"stopped": pid}, f"stopped {who}'s `wait` (pid {pid})")
    return 0


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

    # Before the message, so the resume notice precedes it and the woken tabs
    # read the two in order.
    res = mark_active(sd, roster, args.sender, "sent a message")
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
    tail = resume_note(res)
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
    paused = read_pause(sd)
    sleeper = sleeps_through_pause(roster, who)
    if paused and not (sleeper and not marker_live(mine)):
        listening = NL * 2 + pause_banner(paused, sleeper)
    elif marker_live(mine):
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
                          "listening": marker_live(mine),
                          "paused": paused or None}, indent=2))
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
    mark_active(sd, roster, me_id(roster), f"handed the baton to {args.to}")
    roster["baton"] = args.to
    save_roster(sd, roster)
    append_msg(sd, "system", "@all",
               f"Write baton moved from **{prev}** to **{args.to}**. "
               f"{args.to} edits files from now on; everyone else advises. "
               f"**{prev}**: you are an advisor again -- go back to `wait` so you "
               f"hear what follows.", args.to)
    emit(args, {"baton": args.to, "previous": prev}, f"baton: {prev} -> {args.to}")
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
