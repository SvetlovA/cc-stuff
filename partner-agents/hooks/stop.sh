#!/usr/bin/env bash
# Stop-hook backstop for partner-agents.
#
# Delegates to partner.py, which decides whether the agent running this session
# owes a reply in .partner/chat.md and, if so, prints a block decision. Fails
# open: no working Python, no plugin script, or no partner session -> the stop
# proceeds.
set -euo pipefail

# Pick a Python that actually runs. `command -v python3` on Windows often
# resolves to the Microsoft Store alias, which is a stub that errors out -- so
# test each candidate instead of trusting the lookup.
py=""
for c in python python3 py; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c "import sys" >/dev/null 2>&1; then
    py=$c
    break
  fi
done
[ -n "$py" ] || exit 0

root=${CLAUDE_PLUGIN_ROOT:-"$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"}
script="$root/skills/partner/scripts/partner.py"
[ -f "$script" ] || exit 0

exec "$py" "$script" hook-stop
