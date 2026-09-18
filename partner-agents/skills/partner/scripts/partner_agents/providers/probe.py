"""Reading a CLI's own --help to learn how to drive it.

A CLI nobody wrote a recipe for is still launchable the first time it is named:
the flags for model, effort, auto-approval and the starting prompt are matched
against its help text, conservatively -- a flag that is not found is not
passed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..providers.binaries import find_binary
from ..state import cache_dir
from ..util import NL, run_command


# Flags a CLI offers for "stop asking me", grouped by how far they go and
# matched against the CLI's own help -- so an unknown CLI still gets launched
# without prompting, the thing that otherwise silently stalls a debate.
AUTO_FLAGS: dict[str, list[str]] = {
    "full": ["--dangerously-bypass-approvals-and-sandbox",
             "--dangerously-skip-permissions", "--yolo", "--allow-all",
             "--full-auto", "--no-sandbox", "--auto-approve-all"],
    "edits": ["--auto-edit", "--auto-approve", "--accept-edits",
              "--no-confirm", "--non-interactive", "--yes"],
}

# Valued flags: one flag, a different value per level.
AUTO_VALUED: list[tuple[str, str, str]] = [
    ("--permission-mode", "acceptEdits", "bypassPermissions"),
    ("--approval-mode", "auto_edit", "yolo"),
]

# Flags that carry the starting prompt. `-p`/`--print` are deliberately absent:
# on several CLIs they mean "run headless and exit", which would open a tab that
# finishes before anybody could type in it.
PROMPT_FLAGS = ["--prompt", "--message", "--task", "--input", "-i"]


def _usage_block(help_text: str) -> str:
    """The synopsis: the `usage:` line and its continuations, nothing after.

    A CLI's usage line says what its arguments *are*; the rest of a help page
    says what it can be asked to do. Only the first answers "can this be handed
    a task in words", which is the question that separates an agent from a tool
    an agent uses.
    """
    lines = help_text.splitlines()
    for i, ln in enumerate(lines):
        if re.match(r"\s*usage\s*:", ln, re.I):
            block = [ln]
            for nxt in lines[i + 1: i + 6]:
                if not nxt.strip():
                    break
                block.append(nxt)
            return NL.join(block)
    return help_text[:400]


def probe_cli(name: str, path: str | None = None, timeout: int = 20,
              refresh: bool = False) -> dict:
    """Derive how to drive a CLI from its own --help.

    Cached against the binary's path, size and mtime, so upgrading the CLI
    invalidates the entry by itself instead of waiting out a TTL.
    """
    binary = path or find_binary(name)
    out: dict = {"name": name, "bin": binary, "found": bool(binary),
                 "model_flag": None, "effort_flag": None, "efforts": [],
                 "auto": {}, "prompt_flag": None, "list_cmd": None,
                 "version": None, "agentic": False, "help": ""}
    if not binary:
        return out
    try:
        st = Path(binary).stat()
        key = f"{binary}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        key = binary
    cf = cache_dir() / ("probe-" + re.sub(r"[^a-z0-9]+", "_", name.lower()) + ".json")
    if not refresh and cf.exists():
        try:
            hit = json.loads(cf.read_text(encoding="utf-8"))
            if hit.get("key") == key:
                return hit["probe"]
        except (OSError, ValueError, KeyError):
            pass

    rc, help_text = run_command([binary, "--help"], timeout)
    if rc != 0 or len(help_text) < 40:
        for alt in (["help"], ["-h"]):
            rc2, alt_text = run_command([binary, *alt], timeout)
            if len(alt_text) > len(help_text):
                help_text = alt_text
            if rc2 == 0 and len(help_text) > 40:
                break
    out["help"] = help_text[:20000]
    low = help_text.lower()

    m = re.search(r"(--model(?:-name|-id)?)\b", help_text)
    out["model_flag"] = m.group(1) if m else ("-m" if re.search(
        r"(?<![\w-])-m\b[ ,=]*[<\[]?\s*model", low) else None)

    e = re.search(r"(--(?:model-)?(?:reasoning-effort|reasoning|effort|"
                  r"thinking(?:-budget|-level|-mode)?))\b", help_text)
    if e:
        out["effort_flag"] = e.group(1)
        tail = help_text[e.end(): e.end() + 240]
        c = re.search(r"[\[{(]\s*([a-z]+(?:\s*[|,/]\s*[a-z]+){1,5})\s*[\]})]",
                      tail, re.I)
        if c:
            out["efforts"] = [x.strip().lower()
                              for x in re.split(r"[|,/]", c.group(1)) if x.strip()]

    for level, flags in AUTO_FLAGS.items():
        for f in flags:
            if f in help_text:
                out["auto"][level] = [f]
                break
    for flag, v_edits, v_full in AUTO_VALUED:
        if flag in help_text:
            if v_edits in help_text:
                out["auto"].setdefault("edits", [flag, v_edits])
            if v_full in help_text:
                out["auto"].setdefault("full", [flag, v_full])
    # Deliberately no fallback from "edits" to the "full" flag. A CLI that only
    # advertises a sandbox-bypass flag gets nothing for `--auto edits`, and the
    # partner stops to ask in its tab -- annoying, and visible. Substituting
    # the bypass flag would silently turn a request to accept edits into a
    # request to remove the sandbox, on a CLI nobody wrote a recipe for.

    # Can this CLI be handed a task in words? A positional the usage line calls
    # a prompt (claude: `[prompt]`, codex: `[PROMPT]`) is the common shape; a
    # flag that carries one is the other. Nothing else can start a partner:
    # everything a partner does begins with being told, in a sentence, what the
    # argument is about.
    # Only the usage line counts. Searching the whole help finds `chat
    # <message>` buried among fifty other subcommands and concludes a browser
    # driver is an agent; in the usage line, a prompt positional means the
    # prompt is what the CLI is *for* -- `claude [options] [prompt]`,
    # `codex [OPTIONS] [PROMPT]`.
    positional = bool(re.search(
        r"[\[<](?:prompt|task|message|instruction|query|request)[\]>.]",
        _usage_block(low)))
    if not positional:
        for f in PROMPT_FLAGS:
            if re.search(re.escape(f) + r"\b[ ,=]*[<\[]", help_text):
                out["prompt_flag"] = f
                break
    out["takes_prompt"] = positional or bool(out["prompt_flag"])

    # Only a `models` line inside the CLI's own command list counts.
    # Matched anywhere in the help, the word "model(s)" in a flag
    # description is enough to invent a subcommand that does not exist --
    # and running it launches the agent, which then sits waiting for input
    # until the timeout expires.
    cmds = re.split(r"(?im)^\s*(?:sub)?commands\s*:", help_text)
    if len(cmds) > 1 and re.search(r"(?m)^\s+models?", cmds[-1]):
        out["list_cmd"] = [binary, "models", "list"]
    elif "--list-models" in help_text:
        out["list_cmd"] = [binary, "--list-models"]

    # Does this behave like an agent CLI at all? This is what the scan trusts
    # instead of the binary's name. An LLM word plus two of the operational
    # signals: one signal alone is met by half of /usr/bin (`ssh-agent` says
    # "agent", `gpg` says "prompt"), and all five would demand more uniformity
    # than these CLIs have.
    # "model" specifically, not any AI-adjacent word. Tools built *for* agents
    # rather than *as* one -- a browser driver, an MCP server -- talk about
    # prompts and tokens and agents all day and have no model to choose.
    llm = bool(out["model_flag"]) or "model" in low
    signals = sum(bool(s) for s in (
        out["model_flag"],
        "agent" in low or "chat" in low or "assistant" in low or "coding" in low,
        "session" in low or "conversation" in low or "resume" in low,
        bool(out["auto"]) or "approv" in low or "permission" in low,
        "mcp" in low or "tool" in low))
    # takes_prompt is the load-bearing one, and it is why a browser driver
    # built *for* agents does not qualify: it has --model, sessions and an
    # approve command, and every one of those signals fires -- but it is driven
    # by `open <url>` and `click <sel>`, so there is no way to tell it what the
    # argument is about. A partner that cannot be told is not a partner.
    out["agentic"] = bool(llm and out["takes_prompt"] and signals >= 3
                          and len(help_text) > 200)
    if out["agentic"]:
        # Only worth a subprocess for something being offered as a partner.
        _, ver = run_command([binary, "--version"], timeout)
        out["version"] = (ver.strip().splitlines() or [""])[0][:80] or None
    try:
        cf.write_text(json.dumps({"key": key, "probe": out}), encoding="utf-8")
    except OSError:
        pass
    return out
