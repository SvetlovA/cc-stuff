"""Commands that find out what this machine can run: CLIs, models, terminals."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ..providers.binaries import find_binary
from ..providers.discovery import scan_clis
from ..providers.launch import build_tui
from ..providers.models import discover_models
from ..providers.probe import probe_cli
from ..providers.resolve import resolve_provider
from ..providers.validate import validate
from ..terminals.detect import terminal_available, terminal_choice, terminal_order
from ..util import NL, emit


def cmd_check(args) -> int:
    v = validate(args.provider, args.model, args.effort, args.cmd, args.force)
    problems, cautions = v["problems"], v["cautions"]
    bits = [args.provider]
    if args.model:
        bits.append(args.model)
    if args.effort:
        bits.append(f"effort={args.effort}")
    head = ("cannot start this partner:" if problems
            else "ok: " + " / ".join(bits))
    lines = [head]
    lines += [f"  - {p}" for p in problems]
    if cautions:
        # Printed on success too. These are the things worth knowing before the
        # tab opens, not reasons to stop.
        lines.append("  worth knowing:")
        lines += [f"  - {c}" for c in cautions]
    emit(args, {"ok": not problems, **v}, NL.join(lines))
    return 1 if problems else 0


def cmd_providers(args) -> int:
    """What this machine can actually run a partner in.

    A scan, not a menu. The recipes appear because their flags were verified by
    hand, not because they are the permitted set -- everything else found on
    the machine is listed beside them and spawns the same way.
    """
    rows = []
    for c in scan_clis(deep=args.deep):
        spec = resolve_provider(c["name"])
        pr = spec.get("probe") or (probe_cli(c["name"]) if c["installed"]
                                   and spec["kind"] == "recipe" else {})
        rows.append({**c,
                     "efforts": sorted(spec["efforts"]),
                     "effort": {"flag": "native flag", "prompt": "prompt hint"}
                               .get(spec.get("effort"), "template"),
                     "version": pr.get("version") if c["installed"] else None})
    if args.json:
        print(json.dumps({"clis": rows, "custom": {
            "usage": "--provider custom --cmd '<template with {prompt}>'"}},
            indent=2))
        return 0

    how = {"recipe": "verified flags", "your config": "your providers.json",
           "found by name": "found here", "found by behaviour": "found here"}
    for r in rows:
        state = "installed" if r["installed"] else "NOT installed"
        print(f"  {r['name']:14} {state:14} {how.get(r['how'], r['how']):20} "
              f"effort: {r['effort']}")
        if r["installed"]:
            print(f"  {'':14} {r['path']}")
            if r["version"]:
                print(f"  {'':14} {r['version']}")
            if r["how"].startswith("found"):
                print(f"  {'':14} no verified recipe -- flags are read from its "
                      f"--help at spawn time")
        elif r["install"]:
            print(f"  {'':14} install with: {r['install']}")
    if not args.deep:
        print(f"{NL}`providers --deep` also probes every binary in the user bin "
              f"directories, which finds an agent CLI whose name gives nothing "
              f"away.")
    print("Any CLI not listed still works: name it directly "
          "(`spawn --provider <bin>`, flags read from its --help), or pass the "
          "exact command with `--provider custom --cmd '<template>'`.")
    pick = terminal_choice()
    where = pick[2] if pick else ("no terminal this script can drive -- it will "
                                  "print the command to start each partner")
    print(f"Partners will open in: {where}. `terminals` lists the alternatives.")
    print("Run `models --provider <name>` for what to point one at.")
    return 0


def cmd_models(args) -> int:
    """What a CLI on this machine can be pointed at -- for the human to choose.

    Every id here was found on the machine: asked of the CLI, read out of its
    help or its bundle, or taken from the user's own config for it. Nothing is
    recited from a list in this file, because such a list is wrong within weeks
    and its wrongness is invisible -- the user simply never sees the model that
    shipped last month.

    Which is also this command's limit, and it is stated in the output rather
    than hidden: a model released after the installed CLI was built is
    mentioned nowhere locally. When this comes back thin or dated, the caller
    is expected to go and check the vendor's current lineup on the web.
    """
    names = [args.provider] if args.provider else \
        [c["name"] for c in scan_clis() if c["installed"]]
    if not names:
        emit(args, {"providers": []},
             "no agent CLI found on this machine. `providers` shows where it "
             "looked; install one, or name a binary directly.")
        return 1

    out = []
    for name in names:
        spec = resolve_provider(name)
        binary = find_binary(spec.get("bin") or name)
        models = [] if args.no_probe or not binary else \
            discover_models(name, binary, deep=args.deep)
        out.append({"provider": name, "installed": bool(binary),
                    "efforts": sorted(spec["efforts"]),
                    "effort_kind": {"flag": "native flag",
                                    "prompt": "prompt hint"}
                                   .get(spec.get("effort"), "template"),
                    "models": models})
    if args.json:
        print(json.dumps({"providers": out, "note":
              "Found on this machine, not from a vendor list. A model released "
              "after this CLI was built appears nowhere here -- check the web "
              "before telling the user this is everything."}, indent=2))
        return 0

    for p in out:
        print(f"{NL}{p['provider']}" + ("" if p["installed"] else "   (NOT installed)"))
        if not p["models"]:
            print("  nothing on this machine names a model for it. Its default "
                  "model works -- spawn without --model -- or look up the "
                  "current lineup and pass one with --model.")
        shown = 0
        for conf, header in (("named", None),
                             ("mentioned", "  only the shape of a model id, "
                                           "found in text -- some of these are "
                                           "not models:")):
            group = [m for m in p["models"] if m["confidence"] == conf]
            if group and header:
                print(header)
            for m in group[:12 if conf == "named" else 8]:
                print(f"  {m['id']:36} effort={m['effort']:7} [{m['tier']}]")
                print(f"  {'':36} {m['note']}")
                print(f"  {'':36} from {m['source']}")
                shown += 1
            if len(group) > (12 if conf == "named" else 8):
                print(f"  {'':36} ... and {len(group) - (12 if conf == 'named' else 8)} more")
        print(f"  effort is a {p['effort_kind']}; accepts: {', '.join(p['efforts'])}")
    print(f"{NL}Where each id came from is printed because it is the whole "
          f"caveat: a model list command is evidence, a string in a bundle is a "
          f"lead. The tier note is read off the *name* -- opus/pro/max reason "
          f"deeper and slower than flash/mini/lite -- not from having tried it.")
    print(f"{NL}This is what the machine knows, which is not what exists. If "
          f"the newest id here looks months old, check the provider's current "
          f"models on the web and offer those too -- they spawn with --model "
          f"whether or not they appear above.")
    print(f"{NL}Then put the options to the human and let them choose. A "
          f"partner they did not pick is one they will not believe when it "
          f"disagrees with them, which is the entire reason for running it.")
    return 0


def cmd_probe(args) -> int:
    """Show what was read out of a CLI's --help, and the command it produces.

    The debugging surface for driving a CLI nobody wrote a recipe for. When a
    partner's tab opens on a usage error, this says which flag was guessed
    wrong, and the argv line below is what to correct with `--cmd`.
    """
    name = args.provider
    path = name if os.sep in name or "/" in name else None
    if path:
        name = Path(path).stem
    pr = probe_cli(name, path, refresh=args.refresh)
    spec = resolve_provider(name)
    demo = None
    if pr["found"]:
        try:
            demo = subprocess.list2cmdline(build_tui(
                {"provider": name, "model": "<model>", "effort": "high",
                 "auto": "edits", "cmd": spec.get("cmd")}, "<starting prompt>"))
        except SystemExit:
            demo = None
    if args.json:
        print(json.dumps({"probe": {k: v for k, v in pr.items() if k != "help"},
                          "kind": spec["kind"], "example": demo}, indent=2))
        return 0
    if not pr["found"]:
        print(f"{name}: not on PATH. `providers` lists what is; a CLI installed "
              f"somewhere unusual can still be used with "
              f"--provider custom --cmd '<full path> ...'")
        return 1
    print(f"{name}  ({spec['kind']})")
    print(f"  binary        {pr['bin']}")
    print(f"  version       {pr['version'] or '-'}")
    print(f"  model flag    {pr['model_flag'] or '- (spawns on its default model)'}")
    print(f"  effort flag   {pr['effort_flag'] or '- (effort becomes a briefing line)'}"
          + (f"  accepts: {', '.join(pr['efforts'])}" if pr["efforts"] else ""))
    print("  auto          " + (", ".join(f"{k}={' '.join(v)}"
                                           for k, v in pr["auto"].items())
                                 or "- (it may stop and ask in its tab)"))
    print(f"  prompt        {pr['prompt_flag'] or 'positional (appended last)'}")
    print(f"  model list    {' '.join(pr['list_cmd']) if pr['list_cmd'] else '-'}")
    if demo:
        print(f"{NL}  a spawn would run:{NL}    {demo}")
    if spec["kind"] == "probed":
        print(f"{NL}Read from its own --help, not from a verified recipe. If "
              f"that command is wrong, correct it with:{NL}"
              f"  spawn --provider custom --cmd '<the right command with "
              f"{{prompt}}>'")
    return 0


def cmd_terminals(args) -> int:
    """What can open a partner's tab here, in the order it would be tried.

    The counterpart to `providers`: same question one level down, and the same
    answer -- what this machine actually has, not what the script was written
    knowing about. Whether an agent can be woken later is part of it, since
    that depends entirely on the terminal.
    """
    items = terminal_order(args.prefer, discover=not args.no_probe)
    rows, first = [], None
    for name, spec in items:
        ok, why = terminal_available(spec)
        row = {"name": name, "available": ok, "why": why,
               "label": spec.get("label") or f"{name} tab",
               "can_type": bool(spec.get("send")),
               "source": spec.get("source", "built-in")}
        if ok and first is None:
            first = row
        rows.append(row)
    data = {"terminals": rows, "chosen": first,
            "prefer": args.prefer or os.environ.get("PARTNER_TERMINAL") or ""}
    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    for r in rows:
        mark = "->" if r is first else "  "
        state = "available" if r["available"] else r["why"][:32]
        typing = "can be typed into" if r["can_type"] else "cannot be typed into"
        origin = "" if r["source"] == "built-in" else f"   [{r['source']}]"
        print(f"{mark} {r['name']:16} {state:34} {typing}{origin}")
    if first:
        print(f"{NL}A partner spawned now opens in: {first['label']}.")
        if not first["can_type"]:
            print("This terminal cannot be typed into from outside, so an agent "
                  "that stops looping has to be nudged by hand -- `nudge` will "
                  "print the command that restarts it.")
    else:
        print(f"{NL}Nothing here can open a tab. `spawn` still registers the "
              f"partner and prints the command to start it yourself.")
    print(f"{NL}Force one with `--terminal <name>` or PARTNER_TERMINAL. Add or "
          f"correct one in ~/.claude/partner-terminals.json (or "
          f".partner/terminals.json for this repo) -- an entry needs `open`, "
          f"and `send` if it can be typed into.")
    return 0
