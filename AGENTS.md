# jdrly/mesa

This is my fork of Mesa for one game, Guild Wars 2, on my Apple Silicon Mac. The game runs in
Mach9's arm64 Wine engine, whose Vulkan driver is KosmicKrisp, Mesa's driver on Metal. My app
GW2 on Mac (jdrly/gw2-on-mac) puts my build of `libvulkan_kosmickrisp.dylib` into that engine in
place of its own. The fork starts from athei/mesa at `d62ee13d18`, the commit Mach9 0.4.0's
KosmicKrisp was built from, and carries my changes for that engine on top.

Its patches are written with AI. That is allowed here and nowhere upstream.

## Nothing goes upstream

Mesa's own `AGENTS.md` and `docs/submittingpatches.rst` set rules for AI in contributions to
Mesa: a `Generated-by: LLM` or `Assisted-by: LLM` trailer, no AI-written comments or commit
messages, a list of directories whose projects take no AI code at all. Those are Mesa's rules for
what it accepts. This fork contributes nothing, so they don't stop work here. What that means:

- Nothing from this fork ever goes to freedesktop's Mesa (gitlab.freedesktop.org/mesa/mesa) or
  to athei/mesa. No merge request, pull request, issue, comment or mail, not even a finding. If
  something here looks worth telling them, tell me and stop there.
- Every AI-written commit says so in its body, and keeps Mesa's trailer `Generated-by: LLM`.
- My changes stay in KosmicKrisp (`src/kosmickrisp`) and in my own files. Nothing here touches
  the directories Mesa's list named (`src/asahi`, `src/nouveau`, `src/panfrost`, `src/poly`,
  `src/imagination`, `src/intel/compiler/jay` and the matching gallium drivers).
- `docs/submittingpatches.rst` still describes Mesa's process. That process is for Mesa.

## Branches

- `gw2-on-mac` is the default branch and the only one with my commits. Work branches off it as
  `<type>/<issue>-<slug>` (`feat`, `fix`, `docs`) and comes back through a squashed pull request.
  Nobody pushes to it directly.
- `macos-patched` mirrors athei's branch of that name and gets nothing of mine.
- The fork moves to a new athei commit only when my app moves to the Mach9 engine built from it.

## Issues, commits and pull requests

- Issues are off here. The tracker is jdrly/gw2-on-mac, with the label `area:mesa`.
- Commit subjects read like Mesa's (`kk: What it does`). The body says why, with evidence,
  names the issue as `Refs jdrly/gw2-on-mac#<n>`, and says that an AI agent wrote the change.

## Builds and tests run on GitHub's runner only

`.github/workflows/gw2.yml` builds KosmicKrisp the way Mach9's CI built the engine's, checks it
against the engine's file and publishes it. Its header says what each step proves. macOS runner
minutes are what GitHub limits, and it has disabled my forks' workflows before. So: one run at a
time, one push per pull request after review, and never a push to find out whether something
builds. Locally you can run `actionlint`, `zsh -n` on the scripts, and
`python3 -I .github/gw2/compare.py selftest <the engine's dylib> <the engine's ICD manifest>` (Mach9's
release tarball, `lib/external/libvulkan_kosmickrisp.dylib` and
`share/vulkan/icd.d/kosmickrisp_mesa_icd.aarch64.json`).

## Where my code goes

`.github/gw2/` (build script, compare), `tests/gw2/` (native tests), `docs/gw2/` (ADRs, one per
decision, `docs/gw2/adr/NNNN-<slug>.md`) and the workflow. A change to Mesa's own code is as
small as the fix allows and its comment names the issue.

Comments, commit messages and documents here are in my first person, in plain words.
