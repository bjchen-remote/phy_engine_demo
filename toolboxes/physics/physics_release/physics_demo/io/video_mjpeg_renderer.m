#import <CoreGraphics/CoreGraphics.h>
#import <Foundation/Foundation.h>
#import <ImageIO/ImageIO.h>

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#import "video_render_core.h"

/*
 * Dependency-free video fallback for macOS hosts without an H.264 encoder.
 * Each trajectory frame is rendered by the same CoreGraphics implementation
 * as the primary path, compressed as JPEG through ImageIO, and muxed into a
 * small ISO BMFF file with a `jpeg` visual sample entry.
 */


static int AtLeastOne(int value) { return value < 1 ? 1 : value; }

static void AppendBytes(NSMutableData *data, const void *bytes, NSUInteger count) {
  [data appendBytes:bytes length:count];
}

static void AppendU8(NSMutableData *data, uint8_t value) {
  AppendBytes(data, &value, sizeof(value));
}

static void AppendBE16(NSMutableData *data, uint16_t value) {
  const uint8_t bytes[2] = {(uint8_t)(value >> 8), (uint8_t)value};
  AppendBytes(data, bytes, sizeof(bytes));
}

static void AppendBE32(NSMutableData *data, uint32_t value) {
  const uint8_t bytes[4] = {
      (uint8_t)(value >> 24), (uint8_t)(value >> 16),
      (uint8_t)(value >> 8), (uint8_t)value};
  AppendBytes(data, bytes, sizeof(bytes));
}

static void AppendBE64(NSMutableData *data, uint64_t value) {
  const uint8_t bytes[8] = {
      (uint8_t)(value >> 56), (uint8_t)(value >> 48),
      (uint8_t)(value >> 40), (uint8_t)(value >> 32),
      (uint8_t)(value >> 24), (uint8_t)(value >> 16),
      (uint8_t)(value >> 8), (uint8_t)value};
  AppendBytes(data, bytes, sizeof(bytes));
}

static void AppendFourCC(NSMutableData *data, const char value[4]) {
  AppendBytes(data, value, 4);
}

static NSUInteger BeginBox(NSMutableData *data, const char type[4]) {
  NSUInteger offset = data.length;
  AppendBE32(data, 0);
  AppendFourCC(data, type);
  return offset;
}

static BOOL EndBox(NSMutableData *data, NSUInteger offset) {
  NSUInteger length = data.length - offset;
  if (length > UINT32_MAX)
    return NO;
  uint8_t *bytes = (uint8_t *)data.mutableBytes + offset;
  uint32_t value = (uint32_t)length;
  bytes[0] = (uint8_t)(value >> 24);
  bytes[1] = (uint8_t)(value >> 16);
  bytes[2] = (uint8_t)(value >> 8);
  bytes[3] = (uint8_t)value;
  return YES;
}

static void AppendIdentityMatrix(NSMutableData *data) {
  static const uint32_t matrix[9] = {
      0x00010000U, 0, 0, 0, 0x00010000U, 0, 0, 0, 0x40000000U};
  for (NSUInteger index = 0; index < 9; index++)
    AppendBE32(data, matrix[index]);
}

static NSData *BuildMovieBox(uint32_t frameCount, uint32_t fps, uint16_t width,
                             uint16_t height, const uint32_t *sampleSizes,
                             uint64_t firstSampleOffset) {
  NSMutableData *data = [NSMutableData data];
  NSUInteger moov = BeginBox(data, "moov");

  NSUInteger mvhd = BeginBox(data, "mvhd");
  AppendBE32(data, 0);             /* version and flags */
  AppendBE32(data, 0);             /* creation time */
  AppendBE32(data, 0);             /* modification time */
  AppendBE32(data, fps);           /* movie timescale */
  AppendBE32(data, frameCount);    /* movie duration */
  AppendBE32(data, 0x00010000U);   /* preferred rate: 1.0 */
  AppendBE16(data, 0x0100U);       /* preferred volume: 1.0 */
  AppendBE16(data, 0);
  AppendBE32(data, 0);
  AppendBE32(data, 0);
  AppendIdentityMatrix(data);
  for (NSUInteger index = 0; index < 6; index++)
    AppendBE32(data, 0);
  AppendBE32(data, 2);             /* next track identifier */
  if (!EndBox(data, mvhd))
    return nil;

  NSUInteger trak = BeginBox(data, "trak");
  NSUInteger tkhd = BeginBox(data, "tkhd");
  AppendBE32(data, 0x00000007U);   /* enabled, in movie, in preview */
  AppendBE32(data, 0);
  AppendBE32(data, 0);
  AppendBE32(data, 1);             /* track identifier */
  AppendBE32(data, 0);
  AppendBE32(data, frameCount);
  AppendBE32(data, 0);
  AppendBE32(data, 0);
  AppendBE16(data, 0);             /* layer */
  AppendBE16(data, 0);             /* alternate group */
  AppendBE16(data, 0);             /* video volume */
  AppendBE16(data, 0);
  AppendIdentityMatrix(data);
  AppendBE32(data, (uint32_t)width << 16);
  AppendBE32(data, (uint32_t)height << 16);
  if (!EndBox(data, tkhd))
    return nil;

  NSUInteger mdia = BeginBox(data, "mdia");
  NSUInteger mdhd = BeginBox(data, "mdhd");
  AppendBE32(data, 0);
  AppendBE32(data, 0);
  AppendBE32(data, 0);
  AppendBE32(data, fps);
  AppendBE32(data, frameCount);
  AppendBE16(data, 0x55c4U);       /* ISO-639-2/T language: und */
  AppendBE16(data, 0);
  if (!EndBox(data, mdhd))
    return nil;

  NSUInteger hdlr = BeginBox(data, "hdlr");
  AppendBE32(data, 0);
  AppendBE32(data, 0);
  AppendFourCC(data, "vide");
  AppendBE32(data, 0);
  AppendBE32(data, 0);
  AppendBE32(data, 0);
  static const char handlerName[] = "VideoHandler";
  AppendBytes(data, handlerName, sizeof(handlerName));
  if (!EndBox(data, hdlr))
    return nil;

  NSUInteger minf = BeginBox(data, "minf");
  NSUInteger vmhd = BeginBox(data, "vmhd");
  AppendBE32(data, 1);             /* required vmhd flag */
  AppendBE16(data, 0);
  AppendBE16(data, 0);
  AppendBE16(data, 0);
  AppendBE16(data, 0);
  if (!EndBox(data, vmhd))
    return nil;

  NSUInteger dinf = BeginBox(data, "dinf");
  NSUInteger dref = BeginBox(data, "dref");
  AppendBE32(data, 0);
  AppendBE32(data, 1);
  NSUInteger url = BeginBox(data, "url ");
  AppendBE32(data, 1);             /* media data is in this file */
  if (!EndBox(data, url) || !EndBox(data, dref) || !EndBox(data, dinf))
    return nil;

  NSUInteger stbl = BeginBox(data, "stbl");
  NSUInteger stsd = BeginBox(data, "stsd");
  AppendBE32(data, 0);
  AppendBE32(data, 1);
  NSUInteger jpeg = BeginBox(data, "jpeg");
  for (NSUInteger index = 0; index < 6; index++)
    AppendU8(data, 0);
  AppendBE16(data, 1);             /* data reference index */
  AppendBE16(data, 0);
  AppendBE16(data, 0);
  AppendBE32(data, 0);
  AppendBE32(data, 0);
  AppendBE32(data, 0);
  AppendBE16(data, width);
  AppendBE16(data, height);
  AppendBE32(data, 0x00480000U);   /* 72 dpi */
  AppendBE32(data, 0x00480000U);
  AppendBE32(data, 0);
  AppendBE16(data, 1);             /* frame count */
  static const char compressor[] = "Motion JPEG";
  AppendU8(data, (uint8_t)(sizeof(compressor) - 1));
  AppendBytes(data, compressor, sizeof(compressor) - 1);
  for (NSUInteger index = sizeof(compressor); index < 32; index++)
    AppendU8(data, 0);
  AppendBE16(data, 24);            /* color depth */
  AppendBE16(data, UINT16_MAX);
  if (!EndBox(data, jpeg) || !EndBox(data, stsd))
    return nil;

  NSUInteger stts = BeginBox(data, "stts");
  AppendBE32(data, 0);
  AppendBE32(data, 1);
  AppendBE32(data, frameCount);
  AppendBE32(data, 1);
  if (!EndBox(data, stts))
    return nil;

  NSUInteger stsc = BeginBox(data, "stsc");
  AppendBE32(data, 0);
  AppendBE32(data, 1);
  AppendBE32(data, 1);             /* first chunk */
  AppendBE32(data, frameCount);    /* every sample resides in one chunk */
  AppendBE32(data, 1);             /* sample description index */
  if (!EndBox(data, stsc))
    return nil;

  NSUInteger stsz = BeginBox(data, "stsz");
  AppendBE32(data, 0);
  AppendBE32(data, 0);             /* per-sample sizes follow */
  AppendBE32(data, frameCount);
  for (uint32_t index = 0; index < frameCount; index++)
    AppendBE32(data, sampleSizes[index]);
  if (!EndBox(data, stsz))
    return nil;

  NSUInteger co64 = BeginBox(data, "co64");
  AppendBE32(data, 0);
  AppendBE32(data, 1);
  AppendBE64(data, firstSampleOffset);
  if (!EndBox(data, co64) || !EndBox(data, stbl) || !EndBox(data, minf) ||
      !EndBox(data, mdia) || !EndBox(data, trak) || !EndBox(data, moov))
    return nil;
  return data;
}

static BOOL WriteRaw(FILE *file, const void *bytes, size_t count) {
  return count == 0 || fwrite(bytes, 1, count, file) == count;
}

static BOOL WriteBE32File(FILE *file, uint32_t value) {
  const uint8_t bytes[4] = {
      (uint8_t)(value >> 24), (uint8_t)(value >> 16),
      (uint8_t)(value >> 8), (uint8_t)value};
  return WriteRaw(file, bytes, sizeof(bytes));
}

static BOOL WriteBE64File(FILE *file, uint64_t value) {
  const uint8_t bytes[8] = {
      (uint8_t)(value >> 56), (uint8_t)(value >> 48),
      (uint8_t)(value >> 40), (uint8_t)(value >> 32),
      (uint8_t)(value >> 24), (uint8_t)(value >> 16),
      (uint8_t)(value >> 8), (uint8_t)value};
  return WriteRaw(file, bytes, sizeof(bytes));
}

static BOOL PatchBE64(FILE *file, off_t offset, uint64_t value) {
  off_t current = ftello(file);
  return current >= 0 && fseeko(file, offset, SEEK_SET) == 0 &&
         WriteBE64File(file, value) && fseeko(file, current, SEEK_SET) == 0;
}

static CGContextRef CreateBitmapContext(size_t width, size_t height) {
  CGColorSpaceRef colorSpace = CGColorSpaceCreateDeviceRGB();
  CGContextRef context = CGBitmapContextCreate(
      NULL, width, height, 8, width * 4, colorSpace,
      (CGBitmapInfo)kCGBitmapByteOrder32Little |
          (CGBitmapInfo)kCGImageAlphaPremultipliedFirst);
  CGColorSpaceRelease(colorSpace);
  return context;
}

static NSData *CreateJPEG(CGContextRef context, double quality) {
  CGImageRef image = CGBitmapContextCreateImage(context);
  if (!image)
    return nil;
  NSMutableData *data = [NSMutableData data];
  CGImageDestinationRef destination = CGImageDestinationCreateWithData(
      (__bridge CFMutableDataRef)data, CFSTR("public.jpeg"), 1, NULL);
  if (!destination) {
    CGImageRelease(image);
    return nil;
  }
  NSDictionary *properties = @{
    (__bridge NSString *)kCGImageDestinationLossyCompressionQuality : @(quality)
  };
  CGImageDestinationAddImage(destination, image,
                             (__bridge CFDictionaryRef)properties);
  BOOL finalized = CGImageDestinationFinalize(destination);
  CFRelease(destination);
  CGImageRelease(image);
  return finalized ? data : nil;
}

static int FailEncoding(FILE *file, CGContextRef context,
                        uint32_t *sampleSizes, NSURL *outputURL,
                        const char *message) {
  if (file)
    fclose(file);
  if (context)
    CGContextRelease(context);
  free(sampleSizes);
  [[NSFileManager defaultManager] removeItemAtURL:outputURL error:nil];
  fprintf(stderr, "%s\n", message);
  return 5;
}

int main(int argc, const char *argv[]) {
  @autoreleasepool {
    if (argc != 4 && argc != 6) {
      fprintf(stderr, "usage: mjpeg_renderer RESULT_JSON OUTPUT_MP4 FPS [MAX_BYTES WIDTH]\n");
      return 2;
    }
    NSError *error = nil;
    NSData *inputData = [NSData dataWithContentsOfFile:@(argv[1])
                                               options:0
                                                 error:&error];
    if (!inputData) {
      fprintf(stderr, "%s\n", error.localizedDescription.UTF8String);
      return 3;
    }
    NSDictionary *root = [NSJSONSerialization JSONObjectWithData:inputData
                                                          options:0
                                                            error:&error];
    if (![root isKindOfClass:[NSDictionary class]]) {
      fprintf(stderr, "invalid result JSON\n");
      return 3;
    }
    NSDictionary *scene = root[@"scene"], *trajectory = root[@"trajectory"];
    NSArray *frames = trajectory[@"frames"];
    if (![frames isKindOfClass:[NSArray class]] || frames.count == 0 ||
        frames.count > UINT32_MAX) {
      fprintf(stderr, "result has an unsupported frame count\n");
      return 3;
    }
    uint64_t maxBytes = 0;
    size_t width = 960, height = 540;
    if (argc == 6) {
      char *end = NULL;
      errno = 0;
      maxBytes = strtoull(argv[4], &end, 10);
      if (errno || !end || *end || argv[4][0] == '-' || maxBytes < 1024) return 2;
      long parsedWidth = strtol(argv[5], &end, 10);
      if (!end || *end || (parsedWidth != 960 && parsedWidth != 720 && parsedWidth != 480)) return 2;
      width = (size_t)parsedWidth;
      height = width * 9 / 16;
    }
    const int parsedFPS = AtLeastOne(atoi(argv[3]));
    const uint32_t fps = (uint32_t)parsedFPS;
    const uint32_t frameCount = (uint32_t)frames.count;
    PhyVideoCamera camera = PhyVideoBuildTrajectoryCamera(scene, trajectory, width, height);

    NSURL *outputURL = [NSURL fileURLWithPath:@(argv[2])];
    [[NSFileManager defaultManager] removeItemAtURL:outputURL error:nil];
    FILE *file = fopen(argv[2], "wb+");
    if (!file) {
      fprintf(stderr, "cannot open output: %s\n", strerror(errno));
      return 4;
    }
    uint32_t *sampleSizes = calloc((size_t)frameCount, sizeof(*sampleSizes));
    CGContextRef context = CreateBitmapContext(width, height);
    if (!sampleSizes || !context)
      return FailEncoding(file, context, sampleSizes, outputURL,
                          "fallback renderer allocation failed");

    uint64_t frameBudget = UINT32_MAX;
    if (maxBytes) {
      NSData *metadata = BuildMovieBox(frameCount, fps, (uint16_t)width, (uint16_t)height, sampleSizes, 44);
      uint64_t overhead = 44 + metadata.length;
      if (!metadata || maxBytes <= overhead)
        return FailEncoding(file, context, sampleSizes, outputURL, "video_byte_budget_exceeded");
      frameBudget = (maxBytes - overhead) / frameCount;
    }

    /* ftyp: isom + common compatible brands. */
    const char ftypPayload[20] = {
        'i', 's', 'o', 'm', 0, 0, 2, 0,
        'i', 's', 'o', 'm', 'i', 's', 'o', '2', 'm', 'p', '4', '1'};
    if (!WriteBE32File(file, 28) || !WriteRaw(file, "ftyp", 4) ||
        !WriteRaw(file, ftypPayload, sizeof(ftypPayload)))
      return FailEncoding(file, context, sampleSizes, outputURL,
                          "failed to write MP4 file type box");

    off_t mdatOffset = ftello(file);
    if (mdatOffset < 0 || !WriteBE32File(file, 1) ||
        !WriteRaw(file, "mdat", 4) || !WriteBE64File(file, 0))
      return FailEncoding(file, context, sampleSizes, outputURL,
                          "failed to write MP4 media header");
    off_t firstSampleOffset = ftello(file);
    if (firstSampleOffset < 0)
      return FailEncoding(file, context, sampleSizes, outputURL,
                          "failed to locate MP4 media data");

    for (uint32_t index = 0; index < frameCount; index++) {
      @autoreleasepool {
        NSDictionary *frame = frames[index];
        NSDictionary *previous = index > 0 ? frames[index - 1] : frame;
        if (!PhyVideoDrawTrajectoryFrame(context, frame, previous, scene, trajectory,
                                          camera, width, height))
          return FailEncoding(file, context, sampleSizes, outputURL,
                              "rendering failed: invalid topology, pose, attachments or allocation");
        NSData *jpegData = nil;
        const double qualities[] = {0.88, 0.75, 0.60, 0.45, 0.35};
        for (size_t attempt = 0; attempt < sizeof(qualities)/sizeof(qualities[0]); ++attempt) {
          jpegData = CreateJPEG(context, qualities[attempt]);
          if (!jpegData || jpegData.length <= frameBudget) break;
        }
        if (jpegData.length > frameBudget)
          return FailEncoding(file, context, sampleSizes, outputURL, "video_byte_budget_exceeded");
        if (!jpegData || jpegData.length == 0 || jpegData.length > UINT32_MAX ||
            !WriteRaw(file, jpegData.bytes, jpegData.length))
          return FailEncoding(file, context, sampleSizes, outputURL,
                              "failed to encode Motion JPEG frame");
        sampleSizes[index] = (uint32_t)jpegData.length;
      }
    }
    off_t mdatEnd = ftello(file);
    if (mdatEnd < mdatOffset ||
        !PatchBE64(file, mdatOffset + 8,
                   (uint64_t)(mdatEnd - mdatOffset)))
      return FailEncoding(file, context, sampleSizes, outputURL,
                          "failed to finalize MP4 media data size");

    NSData *moov = BuildMovieBox(frameCount, fps, (uint16_t)width,
                                 (uint16_t)height, sampleSizes,
                                 (uint64_t)firstSampleOffset);
    if (!moov || !WriteRaw(file, moov.bytes, moov.length) || fflush(file) != 0)
      return FailEncoding(file, context, sampleSizes, outputURL,
                          "failed to write MP4 movie metadata");
    if (fclose(file) != 0) {
      CGContextRelease(context);
      free(sampleSizes);
      [[NSFileManager defaultManager] removeItemAtURL:outputURL error:nil];
      fprintf(stderr, "failed to close MP4 output\n");
      return 5;
    }
    CGContextRelease(context);
    free(sampleSizes);
  }
  return 0;
}
