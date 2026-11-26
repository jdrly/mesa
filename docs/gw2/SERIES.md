# My Mesa fork as a patch series

- Fork: jdrly/mesa, branch `gw2-on-mac`.
- Base: athei/mesa `d62ee13d180`, the commit Mach9 0.4.0's KosmicKrisp was built from.
  `origin/macos-patched` in this fork is that commit.
- Head the topic table was written against: `9d08aebd96a` (#339), 2026-11-26.
- Series branch: `series/2026-11-26`.

The workspace's ADR 0015 makes each of my forks a curated patch series. This file is the series of
this one: every commit in `d62ee13d180..gw2-on-mac` with its topic, and the rules that cut them
into a `series/…` branch whose tree equals `gw2-on-mac`'s. Here that is short. I carry one commit,
and the series is that commit and this file.

## Topics

| Topic | Commits | What it does | Switches | 0.2.1 | App | Evidence | Verdict |
|---|---|---|---|---|---|---|---|
| `sparse-record` | 1 | KosmicKrisp takes a linear texture's sparseness from its own record and never asks Metal's `sparseBufferTier` (`9d08aebd96a`). With it come the fork's `AGENTS.md`, its ADR 0001, the workflow and a native test | none | shipped, always on. My app pins this build in `prebuilt.lock` | no switch | 0.2.0 closed the game at start on macOS 26 on an M1 and an M2 Max. With this the M1 starts (0.2.1's notes, #339) | **keep.** It is 0.2.1's fix |
| `docs` | 0 | This file. The commit that adds it needs no line, the rules place it | none | not in use | no | n/a | **keep.** Docs |

No rule splits `sparse-record`'s commit into its workflow, docs and test. A fork of one patch gains
nothing from four commits.

## athei's rewrite

athei rewrote his Mesa branch on 2026-11-25. `macos-patched` is now at `f81019e4eef`, 33 commits on
the common base where `d62ee13d180` had 29. Four subjects are new, two for KosmicKrisp and two for
zink. My commit replays onto `f81019e4eef` clean: on 2026-11-26 I merged it onto that tip with its
own parent as the merge base, and git reported no conflict.

## The series branch

Two commits on `d62ee13d180`: `sparse-record`, then `docs`. The pull request that adds this file
merges squashed, so the series is `gw2-on-mac`'s head itself, pushed under the series name.

## Machine blocks

My workspace's replay script reads these blocks by their fence label. Paths are globs where `*`
matches `/` too, and the first rule that matches wins. No topic is skipped.

```series-topics
9d08aebd96a01203d1a086fbbbfdf5e5e8db6d75 sparse-record
```

```series-rules
docs/gw2/SERIES.md docs
```

```series-order
mode disjoint
sparse-record
docs
```

```series-titles
sparse-record kk: Linear textures on a buffer take sparseness from my own record, not Metal's sparseBufferTier
docs gw2: SERIES.md lists my one commit by topic
```

## Rules

- A commit needs no line in `series-topics` when the rules place every path it changes. That
  covers the commit that adds this file, and later commits that only touch this file.
- Any other commit gets its line when the series is next rebuilt, and the build refuses one
  without it.
- The series branch is rebuilt under a new date, never rewritten.
- `gw2-on-mac` is never rewritten.
