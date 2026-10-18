# Explanation Style Guide

The goal is an explanation the reader understands on the first read, without opening the code. The code links are there to check details, not because the text needs them. Every rule below serves that goal.

## Structure

Write the parts in this order:

1. **`#` title**, then the **core idea** in one or two plain sentences. If the subject replaces something older, start with a short before/after table.
2. **The big-picture diagram**, the whole flow on one screen, with its Note.
3. **One `##` section per important part.** Number the sections when they follow the flow (`## 1. How the services talk`). Each section has a diagram or a table, links to the code, and at most a few short sentences.
4. **Smaller changes that matter**, when explaining changes: a table of *change | why | where*.
5. **Open items**: one or two lines on what is unverified, unfinished or risky. Leave this out if there is nothing.

Do not add a preamble ("Let me explain…"), a closing recap, or a list of offers. Stop when the content ends.

## Sentences and Words

- Keep sentences short, with one idea each. Split any sentence that needs a semicolon.
- Use plain words first and the code name second: "the reducer (`ClusterReducer`) merges duplicate stories".
- Explain every term the first time it appears. That includes the project's own jargon, such as "soft deadline" or "map step".
- Use active voice with a clear actor: "C# validates the answer", not "the answer is validated".
- Do not use filler such as "basically", "essentially", "it's worth noting" or "comprehensive", and do not use emoji.
- Say "I don't know" or "not verified" rather than guessing.

## Diagrams

Use a diagram whenever there is a flow, an order of steps, a loop, a timeline, a structure, or a comparison of shapes. Draw it in a code block with **no language tag**, using box-drawing characters (`─ │ ┌ ┐ └ ┘ ├ ┤ ┬ ┴`) and arrows (`─▶ ◀─ ▼ ▲`). Keep it at most about 100 columns wide.

**Every diagram is followed directly by a Note.** Write `**Note:**` and then one or two plain sentences that say what the diagram shows and what to take from it. The Note does not repeat the labels. It gives the meaning.

Label boxes and arrows with real names, and add a link in the text around the diagram, not inside it. Put the key numbers or conditions on the arrows (`only 1 batch? → done`).

### Templates

Service or component flow:

```
Caller ──Request──▶ Service A ──────────────────────────▶ Service B
                    │ ① fetch(input)  ◀── data ──────────  Store
                    │ ② process
                    │ ③ save(result) ─────────────────────▶ persist, publish
```
**Note:** which side owns which step, numbered in order.

Pipeline with parallel branches:

```
start ─┬─▶ [step A]  produces X ──────────────────┐   (runs in parallel)
       │                                           │
       └─▶ load ─▶ transform ─▶ Y                  │
                                 ▼                 ▼
                            [step B] uses X and Y ─▶ result
```
**Note:** what runs at the same time and where the branches join.

Loop or rounds:

```
Round 1:  input ─▶ split ─▶ work on every part at once ─▶ output
             └─ only 1 part? ─▶ done
Round 2+: find candidates ─▶ work only on them ─▶ repeat
             ├─ nothing new ─────▶ done
             └─ limit reached ───▶ stop, keep what exists
```
**Note:** what one round does and every way the loop ends.

Retry and fallback:

```
for each provider (A, then B):
  for attempt 1..N:
     call ──timeout──▶ retry
       ▼
     validate ─ rejected ─▶ retry with feedback
       ▼ ok
     return
all failed ─▶ error
```
**Note:** what is retried, what moves to the next provider, and what fails.

Timeline or limits:

```
0 ───────────────── soft limit ───── reserve ───── hard limit
   main work            │          finishing          │
                  stop early,                     run fails
                  keep results
```
**Note:** what happens at each limit and why there are two.

Formula with a worked example:

```
tokens = ceil( chars / CharsPerToken × (1 + Margin) )
25,000 tokens  ≈  25,000 × 2.5 / 1.15  ≈  54,000 characters
```
**Note:** what the number means in practice.

Concrete case:

```
batch 1: "Alert in Haifa"   (channel A, 08:00)  ─┐
batch 2: "Sirens in Haifa"  (channel B, 08:05)  ─┴─▶ step 1 writes 2 paragraphs
                                                     step 2 merges them into 1
```
**Note:** why the mechanism exists, shown on one real input.

## Tables

Use a table instead of a bullet list whenever the items share the same fields.

| Use for | Columns |
|---|---|
| Before / after | aspect \| before \| after |
| What goes where | part \| job \| link |
| Settings | key \| value \| what it does |
| Rules or validators | rule \| where |
| Modes or cases | input \| what happens \| example |
| Smaller changes | change \| why \| where |
| Concept vs reality | what the name suggests \| what the code does |

Keep cells short, a phrase rather than a paragraph. Put links in the cells.

## Links

- Use the form `[File.cs:42](relative/path/File.cs:42)`. The label is the file name plus the line, or the symbol (`[Cut](path/Helper.cs:15)`). The target is the path relative to the current working directory, plus the line.
- Use a range when the claim covers a block: `[:29–49](path/File.cs:29)`, which links to the first line. A short `[:68](path/File.cs:68)` is fine for a file already named in the same sentence.
- Get every line number from the real file at the moment of writing.
- Link the place that does the work, not a declaration or a comment, unless the declaration is the point.
- For a PR that is not checked out locally, link GitHub blob URLs at the PR's head commit: `https://github.com/<owner>/<repo>/blob/<sha>/<path>#L42`.
- Where markdown links do not render, the `path:line` text still works. Do not use a format that breaks that.

## Picking What Is Important

Include:

- the entry point and the main flow
- who calls what, and across which boundary (service, process, API)
- rules that protect correctness: validation, invariants, limits, deadlines
- settings that change behaviour, with their defaults
- failure paths and what the user sees when something breaks
- anything surprising: a name that misleads, an order that matters, a value set to protect against something

Leave out:

- renames, formatting, imports and plain dependency-injection wiring
- line-by-line retelling of code
- every file in a diff. Group them by role and explain the roles.
- tests, unless they show intent that the code hides

## Being Honest

- Flag `UNVERIFIED`, `TODO`, best-effort paths and unchecked plan items in the "Still open" part.
- When the textbook meaning of a term differs from the code, show it in a table (*textbook | here*).
- When something could not be checked (no `gh`, a file not found), say so in one line. Do not fill the gap with guesses.

## Anti-patterns

| Avoid | Do instead |
|---|---|
| A wall of bullets | a table or a diagram |
| A paragraph longer than four lines | split it, or move it into a table |
| A diagram without a Note | always add the Note |
| A guessed line number | `grep -n`, then link |
| Explaining every file | group by role and explain the important ones |
| Retelling the code line by line | say what it achieves and why |
| Jargon left unexplained | a plain definition on first use |
| A summary at the end | stop when the content ends |
