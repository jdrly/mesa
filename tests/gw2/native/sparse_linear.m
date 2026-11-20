/*
 * gw2 (jdrly/gw2-on-mac#339): KosmicKrisp's linear textures on a buffer, natively on the
 * runner, without Wine or Vulkan. Built with the compile flags of the build's
 * src/kosmickrisp/bridge/mtl_device.m and linked with its libmtl_bridge.a, so it calls the
 * bridge functions the driver links (.github/gw2/mesa.zsh test).
 *
 *   sparse_linear <case>
 *
 * Exits 0 when the case passes, 1 when it fails, 77 when the case needs placement sparse and this
 * runner's device says it has none (supportsPlacementSparse answers NO). Any other buffer the
 * device doesn't make is a failure, also where that property throws. Each case is its own process, so an exception
 * or a crash in one can't hide another.
 *
 * Stand-in cases, on any Mac: mtl_new_texture_with_descriptor_linear gets a stand-in for the
 * buffer that logs every message it is sent and keeps the texture descriptor it is handed (it
 * forwards resourceOptions to a real buffer and makes no texture). It says it responds to
 * sparseBufferTier and answers it itself with MTLBufferSparseTierNone, so a bridge that asks
 * always gets as far as sending it, on any macOS.
 *   stand-in-plain   flag false: the descriptor keeps a new descriptor's placementSparsePageSize,
 *                    and nothing sends the buffer sparseBufferTier or asks whether it responds
 *                    to it. Built with -DGW2_BASE against the engine's commit, this case has to
 *                    fail on exactly that, which shows it tests the bug.
 *   stand-in-sparse  flag true: the descriptor gets MTLSparsePageSize64, and nothing sends the
 *                    buffer sparseBufferTier
 *
 * Real buffers, each made as the driver makes it, a texture asked for with the flag the driver
 * passes for it:
 *   plain    a shared buffer from the device, flag false. Never skipped.
 *   heap     mtl_new_heap and mtl_new_buffer_with_length (device memory, kk_bo.c), flag false.
 *            The heap asks for a placement sparse page size, as the driver's does, so it skips
 *            only where the device has no placement sparse.
 *   nocopy   mtl_new_buffer_with_bytes_no_copy (imported host memory), flag false. Never skipped.
 *   sparse   mtl_new_sparse_buffer (a VK_BUFFER_CREATE_SPARSE_BINDING_BIT buffer), flag true. It
 *            skips only where the device has no placement sparse.
 * Each has to give a texture without an exception. Then the case asks Metal itself for the
 * buffer's sparseBufferTier, inside @try: where that works (macOS 27), whether it is
 * MTLBufferSparseTierNone has to match the flag, which is the evidence that the flag gives the
 * descriptor what sparseBufferTier gave it there. Where it throws (macOS 26's stub), the case
 * prints that, and it passes on the texture alone.
 */

#include "kosmickrisp/bridge/mtl_bridge.h"
#include "kosmickrisp/bridge/mtl_buffer.h"
#include "kosmickrisp/bridge/mtl_device.h"
#include "kosmickrisp/bridge/mtl_heap.h"
#include "kk_image_layout.h"
#include "kk_private.h"

#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define SKIP 77
#define HEAP_B (16u * KK_SPARSE_PAGE_SIZE)
#define WIDTH_PX 256u
#define STRIDE_B (WIDTH_PX * 4u)

#ifdef GW2_BASE
/* the engine's commit: no flag, the bridge asks the buffer */
#define LINEAR(buf, layout, sparse) mtl_new_texture_with_descriptor_linear((buf), (layout), 0u)
#else
#define LINEAR(buf, layout, sparse) mtl_new_texture_with_descriptor_linear((buf), (layout), 0u, (sparse))
#endif

/* Stands in for an id<MTLBuffer>: logs each message, forwards it to the real buffer, except the
 * one that makes the texture, whose descriptor it keeps. */
@interface GW2BufferSpy : NSProxy {
@public
   id<MTLBuffer> target;
   NSMutableArray<NSString *> *sent;
   BOOL saw_descriptor;
   NSInteger page_size;
}
@end

@implementation GW2BufferSpy
- (NSMethodSignature *)methodSignatureForSelector:(SEL)sel
{
   if (sel == @selector(sparseBufferTier))
      return [NSMethodSignature signatureWithObjCTypes:"q@:"];
   return [(NSObject *)target methodSignatureForSelector:sel];
}

- (void)forwardInvocation:(NSInvocation *)invocation
{
   [sent addObject:NSStringFromSelector(invocation.selector)];
   if (invocation.selector == @selector(sparseBufferTier)) {
      /* answered here, never by the real buffer, whose answer may be an exception */
      NSInteger none = MTLBufferSparseTierNone;
      [invocation setReturnValue:&none];
      return;
   }
   if (invocation.selector == @selector(newTextureWithDescriptor:offset:bytesPerRow:)) {
      MTLTextureDescriptor *descriptor = nil;
      [invocation getArgument:&descriptor atIndex:2];
      saw_descriptor = YES;
      page_size = descriptor.placementSparsePageSize;
      id<MTLTexture> none = nil;
      [invocation setReturnValue:&none];
      return;
   }
   [invocation invokeWithTarget:target];
}

- (BOOL)respondsToSelector:(SEL)sel
{
   [sent addObject:[@"respondsToSelector:" stringByAppendingString:NSStringFromSelector(sel)]];
   return sel == @selector(sparseBufferTier) || [(NSObject *)target respondsToSelector:sel];
}
@end

static struct kk_image_layout
texel_buffer_layout(void)
{
   /* what kk_CreateBufferView asks for: a texture buffer over the buffer, read only */
   return (struct kk_image_layout){
      .width_px = WIDTH_PX,
      .height_px = 1u,
      .depth_px = 1u,
      .layers = 1u,
      .type = MTL_TEXTURE_TYPE_TEXTURE_BUFFER,
      .sample_count_sa = 1u,
      .levels = 1u,
      .linear = true,
      .optimized_layout = false,
      .usage = MTL_TEXTURE_USAGE_SHADER_READ,
      .format = {.mtl = MTLPixelFormatRGBA8Unorm},
      .linear_stride_B = STRIDE_B,
   };
}

static int
stand_in(id<MTLDevice> dev, bool sparse)
{
   GW2BufferSpy *spy = [GW2BufferSpy alloc];
   spy->target = [dev newBufferWithLength:KK_SPARSE_PAGE_SIZE options:MTLResourceStorageModeShared];
   spy->sent = [NSMutableArray new];
   if (!spy->target) {
      printf("FAIL: the device made no shared buffer for the stand-in\n");
      return 1;
   }
   struct kk_image_layout layout = texel_buffer_layout();
   NSInteger fresh = [[MTLTextureDescriptor new] autorelease].placementSparsePageSize;

   int status = 0;
   @try {
      LINEAR((mtl_buffer *)spy, &layout, sparse);
   } @catch (NSException *e) {
      printf("FAIL: %s: %s\n", e.name.UTF8String, e.reason.UTF8String);
      status = 1;
   }
   printf("sent to the buffer: %s\n", [spy->sent componentsJoinedByString:@", "].UTF8String);
   for (NSString *s in spy->sent) {
      if ([s containsString:@"sparseBufferTier"]) {
         printf("FAIL: the bridge sent the buffer %s\n", s.UTF8String);
         status = 1;
      }
   }
   if (!spy->saw_descriptor) {
      printf("FAIL: the bridge asked the buffer for no texture\n");
      return 1;
   }
   NSInteger want = sparse ? MTLSparsePageSize64 : fresh;
   printf("flag %s: placementSparsePageSize %ld, want %ld (a new descriptor's is %ld)\n",
          sparse ? "true" : "false", (long)spy->page_size, (long)want, (long)fresh);
   if (spy->page_size != want) {
      printf("FAIL: placementSparsePageSize %ld, want %ld\n", (long)spy->page_size, (long)want);
      status = 1;
   }
   return status;
}

/* 1: the device has placement sparse, 0: it hasn't, -1: the property threw */
static int
placement_sparse(id<MTLDevice> dev)
{
   @try {
      return dev.supportsPlacementSparse ? 1 : 0;
   } @catch (NSException *e) {
      printf("supportsPlacementSparse throws here (%s: %s)\n", e.name.UTF8String, e.reason.UTF8String);
      return -1;
   }
}

static int
real(id<MTLDevice> dev, const char *kind)
{
   mtl_buffer *buffer = NULL;
   mtl_heap *heap = NULL;
   void *host = NULL;
   bool sparse = false;

   if (!strcmp(kind, "plain")) {
      buffer = (mtl_buffer *)[dev newBufferWithLength:KK_SPARSE_PAGE_SIZE
                                              options:MTLResourceStorageModeShared];
      if (!buffer) {
         printf("FAIL: the device made no shared buffer\n");
         return 1;
      }
   } else if (!strcmp(kind, "heap")) {
      heap = mtl_new_heap((mtl_device *)dev, HEAP_B, KK_MTL_RESOURCE_OPTIONS);
      if (heap)
         buffer = mtl_new_buffer_with_length(heap, KK_SPARSE_PAGE_SIZE, 0u);
   } else if (!strcmp(kind, "nocopy")) {
      if (posix_memalign(&host, KK_SPARSE_PAGE_SIZE, KK_SPARSE_PAGE_SIZE) != 0)
         host = NULL;
      if (host)
         buffer = mtl_new_buffer_with_bytes_no_copy((mtl_device *)dev, host, KK_SPARSE_PAGE_SIZE);
   } else if (!strcmp(kind, "sparse")) {
      sparse = true;
      buffer = mtl_new_sparse_buffer((mtl_device *)dev, KK_SPARSE_PAGE_SIZE);
   } else {
      printf("FAIL: no case %s\n", kind);
      return 1;
   }
   if (!buffer) {
      /* only a case that needs placement sparse may skip, and only where the device says it has
       * none */
      if (strcmp(kind, "nocopy") && placement_sparse(dev) == 0) {
         printf("skip: this runner's Metal made no %s buffer, and its device has no placement sparse\n", kind);
         return SKIP;
      }
      printf("FAIL: the device made no %s buffer\n", kind);
      return 1;
   }

   struct kk_image_layout layout = texel_buffer_layout();
   int status = 0;
   mtl_texture *texture = NULL;
   @try {
      texture = LINEAR(buffer, &layout, sparse);
   } @catch (NSException *e) {
      printf("FAIL: %s: %s\n", e.name.UTF8String, e.reason.UTF8String);
      return 1;
   }
   if (texture) {
      printf("%s buffer, flag %s: a texture\n", kind, sparse ? "true" : "false");
      mtl_release(texture);
   } else {
      printf("FAIL: %s buffer, flag %s: no texture\n", kind, sparse ? "true" : "false");
      status = 1;
   }

   id<MTLBuffer> buf = (id<MTLBuffer>)buffer;
   printf("respondsToSelector:sparseBufferTier: %s\n",
          [buf respondsToSelector:@selector(sparseBufferTier)] ? "yes" : "no");
   @try {
      NSInteger tier = buf.sparseBufferTier;
      bool metal_sparse = tier != MTLBufferSparseTierNone;
      printf("Metal's sparseBufferTier: %ld, so %s; the flag: %s\n", (long)tier,
             metal_sparse ? "sparse" : "not sparse", sparse ? "true" : "false");
      if (metal_sparse != sparse) {
         printf("FAIL: the flag isn't what sparseBufferTier says\n");
         status = 1;
      }
   } @catch (NSException *e) {
      printf("Metal's sparseBufferTier throws here (%s: %s), so no comparison\n",
             e.name.UTF8String, e.reason.UTF8String);
   }

   mtl_release(buffer);
   if (heap)
      mtl_release(heap);
   free(host);
   return status;
}

int
main(int argc, char **argv)
{
   if (argc != 2) {
      fprintf(stderr, "usage: %s stand-in-plain | stand-in-sparse | plain | heap | nocopy | sparse\n", argv[0]);
      return 2;
   }
   setvbuf(stdout, NULL, _IOLBF, 0);
   @autoreleasepool {
      id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
      if (!dev) {
         printf("FAIL: no Metal device\n");
         return 1;
      }
      NSOperatingSystemVersion v = NSProcessInfo.processInfo.operatingSystemVersion;
      printf("== %s on %s, macOS %ld.%ld.%ld\n", argv[1], dev.name.UTF8String,
             (long)v.majorVersion, (long)v.minorVersion, (long)v.patchVersion);
      int status;
      if (!strcmp(argv[1], "stand-in-plain"))
         status = stand_in(dev, false);
      else if (!strcmp(argv[1], "stand-in-sparse"))
         status = stand_in(dev, true);
      else
         status = real(dev, argv[1]);
      printf("%s: %s\n", argv[1], status == 0 ? "pass" : status == SKIP ? "skip" : "fail");
      return status;
   }
}
