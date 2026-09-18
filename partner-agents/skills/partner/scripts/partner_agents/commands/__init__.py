"""One function per subcommand, grouped by what the caller is doing.

    session     init, spawn, resume, archive, sessions, stop, snapshot
    messaging   wait, send, kickoff, read, claim, baton, pending, nudge
    status      list, state, idle
    discovery   providers, models, probe, terminals, check
    hooks       hook-stop, hook-prompt (called by hooks/hook.sh)

Each takes the parsed argparse namespace and returns an exit code.
"""
