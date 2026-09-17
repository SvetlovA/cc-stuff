#!/usr/bin/env bash
# Hook entry point for partner-agents: `hook.sh hook-stop` or `hook.sh hook-prompt`.
#
# Delegates to partner.py. `hook-stop` decides whether the agent running this
# session owes a reply in .partner/chat.md or has no `wait` in flight, and if
# so prints a block decision; otherwise it closes the agent's turn. `hook-prompt`
# opens the turn and resumes a paused session when the human writes. Both fail
# open: no working Python, no plugin script, or no partner session -> nothing
# happens.
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

exec "$py" "$script" "${1:-hook-stop}"
