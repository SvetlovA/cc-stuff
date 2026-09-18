"""The briefing every agent is launched with: seed.md and handoff.md.

`init` and `spawn` both brief through `write_briefing`, which is what keeps the
agent that starts a session a peer of the ones it starts rather than a
differently-described lead.
"""
from __future__ import annotations

from pathlib import Path

from .prompts import EFFORT_HINT, LOOP_SESSION, LOOP_TAB, SEED
from .providers.resolve import provider_spec, wait_note
from .state import baton_of, repo_snapshot, write_wrappers
from .timing import WAIT_TIMEOUT
from .transcript import render, tail_msgs
from .util import NL


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
