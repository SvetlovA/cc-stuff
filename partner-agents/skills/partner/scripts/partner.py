#!/usr/bin/env python3
"""partner.py - run several AI agents as equal partners on one repository.

Every agent is an ordinary interactive session. The human can type at any of
them, and the one they address takes the write baton; the rest read, argue,
and keep their hands off the files. Between interruptions each agent watches a
shared transcript and answers the others on its own.

This file is only the entry point. Its path is what every wrapper, hook and
briefing calls, so it stays put; the code lives in the `partner_agents` package
beside it. Standard library only; runs on Windows, macOS and Linux.

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
parse it without screen-scraping. `partner_agents/cli.py` lists them all.
"""
import sys

from partner_agents.cli import main

if __name__ == "__main__":
    sys.exit(main())
