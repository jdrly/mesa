# 1. My Mesa fork, and KosmicKrisp's own record of a sparse buffer

Date: 2026-11-20. Issue: jdrly/gw2-on-mac#339.

## Context

GW2 on Mac 0.2 runs Guild Wars 2 in Mach9 0.4.0's arm64 Wine engine. Its Vulkan driver is
KosmicKrisp from athei/mesa at `d62ee13d18`. On macOS 26 the game dies a few seconds after Play
with `-[AGXG13GFamilyBuffer sparseBufferTier]: unrecognized selector`. On macOS 27 it runs.

athei's commit `4540806486` ("kk: Add sparse resources") made
`mtl_new_texture_with_descriptor_linear` ask each buffer for `sparseBufferTier`, behind
`respondsToSelector:`, to decide whether a linear texture on it gets
`placementSparsePageSize = MTLSparsePageSize64`. On my rented M1 with macOS 26.7.3, natively and
without Wine, I measured:

- the selector exists only on Metal's base class `_MTLResource`, and the AGX buffer classes don't
  implement it;
- `respondsToSelector:` says yes on every buffer: shared, private, from a heap, from
  `newBufferWithBytesNoCopy`;
- the call throws on every one of them;
- the SDK declares the property `API_AVAILABLE(macos(26.0))`.

So neither `respondsToSelector:` nor a macOS version check can tell whether the call is safe:
both would be guesses, and Apple's own availability is wrong.

The engine is athei's signed bundle and I don't patch his repositories. My app already replaces
engine files with builds from my forks of Wine and FEX, checked against the engine's files on
GitHub's runner.

## Decision

1. **The fork.** jdrly/mesa's branch `gw2-on-mac` starts at `d62ee13d18` and carries my changes
   to KosmicKrisp. Its patches are written with AI, nothing goes upstream, and `AGENTS.md` gives
   the rule in place of Mesa's. `.github/workflows/gw2.yml` builds only the driver, the way Mach9
   0.4.0 built it, gates it against the engine's file and publishes
   `kosmickrisp-arm64-<sha>.tar.xz` to the pre-release `arm64-builds`. My app installs that file
   over the engine's `lib/external/libvulkan_kosmickrisp.dylib`.
2. **The rule.** KosmicKrisp decides whether a buffer is sparse from its own record of how it
   made the buffer, never by asking Metal. `mtl_new_texture_with_descriptor_linear` takes a
   `sparse` flag from its caller:
   - a buffer view passes `kk_buffer_is_sparse()`, the `VK_BUFFER_CREATE_SPARSE_BINDING_BIT`
     that `kk_CreateBuffer` makes the buffer with `mtl_new_sparse_buffer` on;
   - a linear image passes false, since device memory's buffer comes from a heap or from host
     memory and never from `mtl_new_sparse_buffer`.

   No code in the driver sends `sparseBufferTier`. The workflow checks that in the built file's
   `__objc_selrefs`.

## Consequences

- The fix doesn't depend on which macOS ships the selector or when Apple fixes the stub.
- The flag has to stay in step with how the driver allocates. A new way to make a sparse buffer
  has to pass true to this function too. The native test (`tests/gw2/native/sparse_linear.m`)
  compares the flag with Metal's own `sparseBufferTier` for each kind of buffer where that call
  works, which is macOS 27 on GitHub's runner.
- The per-object compare in the workflow names every object that differs from my build of the
  engine's commit. For this change I expect the bridge's `mtl_buffer.m.o` and its two callers,
  `kk_buffer_view.c.o` and `kk_image.c.o`: Mesa's code outside the bridge has to pass the flag, so
  the change can't stay inside the bridge's objects.
- Moving to a newer athei commit repeats the gate against that engine's file, and this change
  may conflict with athei's code there.
