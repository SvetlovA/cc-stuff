# Example: A Finished Explanation

This is a real answer to `/explain where is the map step and where is reduce` about a .NET digest pipeline. Copy its shape, not its content:

- the direct answer first
- a diagram with a Note
- tables of locations with links
- a concrete case
- an honest comparison of the name with the code

The paths are from that project and are only illustrations.

---

# Where the map step and the reduce step are

There is no class called "Map" or "Reduce". Both live in one class, [ClusterReducer.cs](src/Business/Reducers/ClusterReducer.cs). Both use the same `cluster` skill. What differs is the input: **map** gets raw posts, **reduce** gets paragraphs that were already written.

```
Pipeline.cs:68  reducer.ReduceAsync(raw posts)
        │
        ▼
┌─ MAP (round 1) ── ClusterReducer.cs:29-49 ─────────────────────────────┐
│  posts ─▶ Cut into batches ≤ MapBatchTokens                            │
│        ─▶ cluster skill on every batch at the same time                │
│        ─▶ paragraphs (each with its citations)                         │
└────────────────────────────────────────────────────────────────────────┘
        │  only 1 batch? → done, nothing left to reduce
        ▼
┌─ REDUCE (rounds 2..MaxRounds) ── ClusterReducer.cs:53-108 ─────────────┐
│  index skill: cards of all paragraphs ─▶ "maybe the same event" groups │
│  only those groups go into batches, the rest carry over                │
│  cluster skill on those batches ─▶ merged paragraphs                   │
│  repeat until there are no new groups, time runs out or MaxRounds      │
└────────────────────────────────────────────────────────────────────────┘
        │
        ▼
Route to sections ─▶ compose ─▶ build document   (after the reduce, not part of it)
```
**Note:** map runs once over every post. Reduce is a loop. Each pass finds paragraphs from different batches that tell the same story and merges them.

## Map: round 1

| What | Where |
|---|---|
| Cut posts into batches by token budget | [ClusterBatchHelper.cs `Cut`](src/Business/Helpers/ClusterBatchHelper.cs:15) |
| Run all batches in parallel | [ClusterReducer.cs:35](src/Business/Reducers/ClusterReducer.cs:35) |
| One cluster skill call | [DigestStepExecutor.cs:32](src/Business/Executors/DigestStepExecutor.cs:32) |

The map step groups posts **within** one batch and writes one cited paragraph per story. If time runs out before map finishes, the run fails, because raw posts are never published.

## Reduce: rounds 2 and later

| What | Where |
|---|---|
| The loop | [ClusterReducer.cs:53](src/Business/Reducers/ClusterReducer.cs:53) |
| Index: suggest which paragraphs may be duplicates | [ClusterReducer.cs:65](src/Business/Reducers/ClusterReducer.cs:65) |
| Batch only the candidate groups | [ClusterBatchHelper.cs `CutGrouped`](src/Business/Helpers/ClusterBatchHelper.cs:98) |
| Commit the round | [ClusterReducer.cs:93](src/Business/Reducers/ClusterReducer.cs:93) |

The reduce step merges stories **across** batches. Why that is needed:

```
batch 1: "Rocket alert in Haifa" (channel A, 08:00)  ─┐
batch 2: "Haifa sirens"          (channel B, 08:05)  ─┴─▶ map writes 2 paragraphs
                                                          reduce merges them into 1
```
**Note:** map only sees one batch at a time, so the same event can be written twice. Reduce finds such pairs and merges them.

## How it differs from textbook map-reduce

| Textbook | Here |
|---|---|
| Map and reduce are different functions | Both use the same `cluster` skill: map gets raw posts, reduce gets written paragraphs |
| Reduce runs once and gives one result | Reduce is a loop, and the result is a **list** of stories |
| Reduce compares everything | Reduce compares only groups that the cheap `index` step suggests |

Compose is **not** part of reduce. It only writes the title and headers around stories that are already finished ([Pipeline.cs:75](src/Business/Pipelines/Pipeline.cs:75)).
