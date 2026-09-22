#import <AVFoundation/AVFoundation.h>
#import <CoreGraphics/CoreGraphics.h>
#import <CoreVideo/CoreVideo.h>
#import <Foundation/Foundation.h>
#import <ImageIO/ImageIO.h>

#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <sys/sysctl.h>

static BOOL DecoderIsBlockedBySandbox(void) {
  int value = 0;
  size_t length = sizeof(value);
  errno = 0;
  return sysctlbyname("kern.hv_vmm_present", &value, &length, NULL, 0) != 0 &&
         (errno == EPERM || errno == EACCES);
}

typedef struct {
  NSUInteger payload;
  NSUInteger end;
} ByteRange;

static uint32_t ReadBE32(const uint8_t *bytes) {
  return ((uint32_t)bytes[0] << 24) | ((uint32_t)bytes[1] << 16) |
         ((uint32_t)bytes[2] << 8) | (uint32_t)bytes[3];
}

static uint64_t ReadBE64(const uint8_t *bytes) {
  return ((uint64_t)ReadBE32(bytes) << 32) | (uint64_t)ReadBE32(bytes + 4);
}

static BOOL ReadBox(const uint8_t *bytes, NSUInteger offset, NSUInteger limit,
                    ByteRange *range, char type[5]) {
  if (offset > limit || limit - offset < 8)
    return NO;
  uint64_t boxSize = ReadBE32(bytes + offset);
  NSUInteger headerSize = 8;
  if (boxSize == 1) {
    if (limit - offset < 16)
      return NO;
    boxSize = ReadBE64(bytes + offset + 8);
    headerSize = 16;
  } else if (boxSize == 0) {
    boxSize = (uint64_t)(limit - offset);
  }
  if (boxSize < headerSize || boxSize > (uint64_t)(limit - offset))
    return NO;
  memcpy(type, bytes + offset + 4, 4);
  type[4] = 0;
  range->payload = offset + headerSize;
  range->end = offset + (NSUInteger)boxSize;
  return YES;
}

static BOOL FindTopLevelBox(NSData *data, const char wanted[4],
                            ByteRange *result) {
  const uint8_t *bytes = data.bytes;
  NSUInteger offset = 0, limit = data.length;
  while (offset < limit) {
    ByteRange range;
    char type[5];
    if (!ReadBox(bytes, offset, limit, &range, type))
      return NO;
    if (memcmp(type, wanted, 4) == 0) {
      *result = range;
      return YES;
    }
    offset = range.end;
  }
  return NO;
}

static BOOL FindBoxInRange(NSData *data, ByteRange search,
                           const char wanted[4], ByteRange *result) {
  const uint8_t *bytes = data.bytes;
  if (search.end > data.length || search.payload > search.end)
    return NO;
  for (NSUInteger typeOffset = search.payload + 4;
       typeOffset <= search.end && search.end - typeOffset >= 4;
       typeOffset++) {
    if (memcmp(bytes + typeOffset, wanted, 4) != 0)
      continue;
    NSUInteger start = typeOffset - 4;
    ByteRange candidate;
    char type[5];
    if (ReadBox(bytes, start, search.end, &candidate, type) &&
        memcmp(type, wanted, 4) == 0) {
      *result = candidate;
      return YES;
    }
  }
  return NO;
}

typedef struct {
  NSUInteger offset;
  NSUInteger length;
} SampleLocation;

typedef struct {
  SampleLocation *items;
  NSUInteger count;
} SampleTable;

static BOOL RangeIsInsideMediaData(NSData *container, uint64_t offset,
                                   uint64_t length) {
  const uint8_t *bytes = container.bytes;
  NSUInteger cursor = 0;
  while (cursor < container.length) {
    ByteRange range;
    char type[5];
    if (!ReadBox(bytes, cursor, container.length, &range, type))
      return NO;
    if (memcmp(type, "mdat", 4) == 0 && offset >= range.payload &&
        offset <= range.end &&
        length <= (uint64_t)range.end - offset)
      return YES;
    cursor = range.end;
  }
  return NO;
}

static BOOL BuildSampleTable(NSData *container, SampleTable *table,
                             NSString **failure) {
  table->items = NULL;
  table->count = 0;
  ByteRange moov, sizes, chunks, mapping;
  if (!FindTopLevelBox(container, "moov", &moov) ||
      !FindBoxInRange(container, moov, "stsz", &sizes) ||
      !FindBoxInRange(container, moov, "stsc", &mapping)) {
    *failure = @"video sample tables are missing";
    return NO;
  }
  BOOL hasCo64 = FindBoxInRange(container, moov, "co64", &chunks);
  if (!hasCo64 && !FindBoxInRange(container, moov, "stco", &chunks)) {
    *failure = @"video chunk-offset table is missing";
    return NO;
  }
  const uint8_t *bytes = container.bytes;
  if (sizes.end - sizes.payload < 12 || chunks.end - chunks.payload < 8 ||
      mapping.end - mapping.payload < 8) {
    *failure = @"video sample tables are truncated";
    return NO;
  }
  uint32_t constantSize = ReadBE32(bytes + sizes.payload + 4);
  NSUInteger sampleCount = ReadBE32(bytes + sizes.payload + 8);
  NSUInteger chunkCount = ReadBE32(bytes + chunks.payload + 4);
  NSUInteger mappingCount = ReadBE32(bytes + mapping.payload + 4);
  if (sampleCount == 0 || sampleCount > 1000000 || chunkCount == 0 ||
      mappingCount == 0) {
    *failure = @"video sample tables have invalid entry counts";
    return NO;
  }
  if ((constantSize == 0 &&
       (sampleCount > (NSUIntegerMax - 12) / 4 ||
        sizes.end - sizes.payload < 12 + sampleCount * 4)) ||
      chunkCount > (NSUIntegerMax - 8) / (hasCo64 ? 8 : 4) ||
      chunks.end - chunks.payload < 8 + chunkCount * (hasCo64 ? 8 : 4) ||
      mappingCount > (NSUIntegerMax - 8) / 12 ||
      mapping.end - mapping.payload < 8 + mappingCount * 12) {
    *failure = @"video sample tables do not contain their declared entries";
    return NO;
  }
  SampleLocation *items = calloc(sampleCount, sizeof(*items));
  if (!items) {
    *failure = @"video sample table allocation failed";
    return NO;
  }
  NSUInteger sampleIndex = 0, mappingIndex = 0;
  for (NSUInteger chunkIndex = 0; chunkIndex < chunkCount; chunkIndex++) {
    uint32_t chunkNumber = (uint32_t)chunkIndex + 1U;
    while (mappingIndex + 1 < mappingCount) {
      NSUInteger nextEntry = mapping.payload + 8 + (mappingIndex + 1) * 12;
      if (chunkNumber < ReadBE32(bytes + nextEntry))
        break;
      mappingIndex++;
    }
    NSUInteger entry = mapping.payload + 8 + mappingIndex * 12;
    uint32_t firstChunk = ReadBE32(bytes + entry);
    uint32_t samplesPerChunk = ReadBE32(bytes + entry + 4);
    if (firstChunk == 0 || chunkNumber < firstChunk || samplesPerChunk == 0) {
      free(items);
      *failure = @"video sample-to-chunk table is invalid";
      return NO;
    }
    NSUInteger chunkEntry = chunks.payload + 8 + chunkIndex * (hasCo64 ? 8 : 4);
    uint64_t sampleOffset = hasCo64 ? ReadBE64(bytes + chunkEntry)
                                    : ReadBE32(bytes + chunkEntry);
    for (uint32_t localIndex = 0; localIndex < samplesPerChunk; localIndex++) {
      if (sampleIndex >= sampleCount) {
        free(items);
        *failure = @"video chunks declare more samples than the size table";
        return NO;
      }
      uint32_t sampleSize =
          constantSize != 0
              ? constantSize
              : ReadBE32(bytes + sizes.payload + 12 + sampleIndex * 4);
      if (sampleSize == 0 || sampleOffset > NSUIntegerMax ||
          sampleSize > UINT64_MAX - sampleOffset ||
          !RangeIsInsideMediaData(container, sampleOffset, sampleSize)) {
        free(items);
        *failure = @"video sample points outside MP4 media data";
        return NO;
      }
      items[sampleIndex] = (SampleLocation){
          (NSUInteger)sampleOffset, (NSUInteger)sampleSize};
      sampleOffset += sampleSize;
      sampleIndex++;
    }
  }
  if (sampleIndex != sampleCount) {
    free(items);
    *failure = @"video chunks do not cover every declared sample";
    return NO;
  }
  table->items = items;
  table->count = sampleCount;
  return YES;
}

static BOOL AVCCLengthSize(NSData *container, NSUInteger *lengthBytes,
                           NSString **failure) {
  ByteRange moov, configuration;
  if (!FindTopLevelBox(container, "moov", &moov) ||
      !FindBoxInRange(container, moov, "avcC", &configuration) ||
      configuration.end - configuration.payload < 5) {
    *failure = @"H.264 video configuration is missing or truncated";
    return NO;
  }
  const uint8_t *containerBytes = container.bytes;
  *lengthBytes =
      (NSUInteger)((containerBytes[configuration.payload + 4] & 0x03U) + 1U);
  return YES;
}

static BOOL ValidateAVCCSample(NSData *sample, NSUInteger lengthBytes,
                               NSString **failure) {
  const uint8_t *sampleBytes = sample.bytes;
  NSUInteger offset = 0, unitCount = 0;
  while (offset < sample.length) {
    if (sample.length - offset < lengthBytes) {
      *failure = @"H.264 sample has a truncated NAL length prefix";
      return NO;
    }
    uint32_t unitLength = 0;
    for (NSUInteger index = 0; index < lengthBytes; index++)
      unitLength = (unitLength << 8) | sampleBytes[offset + index];
    offset += lengthBytes;
    if (unitLength == 0 || unitLength > sample.length - offset) {
      *failure = @"H.264 sample has an invalid NAL unit length";
      return NO;
    }
    uint8_t header = sampleBytes[offset];
    uint8_t unitType = header & 0x1fU;
    if ((header & 0x80U) != 0 || unitType == 0 || unitType > 23) {
      *failure = @"H.264 sample has an invalid NAL unit header";
      return NO;
    }
    offset += unitLength;
    unitCount++;
  }
  if (unitCount == 0) {
    *failure = @"H.264 sample contains no NAL units";
    return NO;
  }
  return YES;
}

static BOOL ReaderProvidesCompressedSample(AVAsset *asset, AVAssetTrack *track,
                                           NSString **failure) {
  NSError *readerError = nil;
  AVAssetReader *reader = [AVAssetReader assetReaderWithAsset:asset
                                                        error:&readerError];
  if (!reader) {
    *failure = readerError.localizedDescription ?: @"cannot create asset reader";
    return NO;
  }
  AVAssetReaderTrackOutput *output =
      [AVAssetReaderTrackOutput assetReaderTrackOutputWithTrack:track
                                                 outputSettings:nil];
  output.alwaysCopiesSampleData = NO;
  if (![reader canAddOutput:output]) {
    *failure = @"cannot read compressed video samples";
    return NO;
  }
  [reader addOutput:output];
  if (![reader startReading]) {
    *failure = reader.error.localizedDescription ?: @"cannot start video sample reader";
    return NO;
  }
  CMSampleBufferRef sample = [output copyNextSampleBuffer];
  if (!sample) {
    *failure = reader.error.localizedDescription ?: @"video track has no readable sample";
    return NO;
  }
  CFRelease(sample);
  return YES;
}

static BOOL DecodeAllH264Samples(AVAsset *asset, AVAssetTrack *track,
                                 NSUInteger expectedCount,
                                 NSString **failure) {
  NSError *readerError = nil;
  AVAssetReader *reader = [AVAssetReader assetReaderWithAsset:asset
                                                        error:&readerError];
  if (!reader) {
    *failure = readerError.localizedDescription ?: @"cannot create H.264 decoder";
    return NO;
  }
  NSDictionary *settings = @{
    (__bridge NSString *)kCVPixelBufferPixelFormatTypeKey :
        @(kCVPixelFormatType_32BGRA)
  };
  AVAssetReaderTrackOutput *output =
      [AVAssetReaderTrackOutput assetReaderTrackOutputWithTrack:track
                                                 outputSettings:settings];
  output.alwaysCopiesSampleData = NO;
  if (![reader canAddOutput:output]) {
    *failure = @"cannot configure H.264 pixel decoder";
    return NO;
  }
  [reader addOutput:output];
  if (![reader startReading]) {
    *failure = reader.error.localizedDescription ?: @"cannot start H.264 decoder";
    return NO;
  }
  NSUInteger decodedCount = 0;
  for (;;) {
    CMSampleBufferRef sample = [output copyNextSampleBuffer];
    if (!sample)
      break;
    CVImageBufferRef image = CMSampleBufferGetImageBuffer(sample);
    BOOL validImage = image && CVPixelBufferGetWidth(image) > 0 &&
                      CVPixelBufferGetHeight(image) > 0;
    CFRelease(sample);
    if (!validImage || decodedCount >= expectedCount) {
      *failure = @"H.264 decoder returned an invalid or excess frame";
      return NO;
    }
    decodedCount++;
  }
  if (reader.status != AVAssetReaderStatusCompleted ||
      decodedCount != expectedCount) {
    *failure = reader.error.localizedDescription
                   ?: @"H.264 decoder did not validate every video sample";
    return NO;
  }
  return YES;
}

static BOOL ValidateAllSamples(AVAsset *asset, AVAssetTrack *track,
                               NSData *container, FourCharCode subtype,
                               size_t expectedWidth, size_t expectedHeight,
                               NSUInteger *sampleCount, NSString **failure) {
  SampleTable table;
  if (!BuildSampleTable(container, &table, failure))
    return NO;
  BOOL valid = YES;
  if (subtype == (FourCharCode)0x6a706567U) {
    valid = ReaderProvidesCompressedSample(asset, track, failure);
    for (NSUInteger index = 0; valid && index < table.count; index++) {
      SampleLocation location = table.items[index];
      NSData *sample = [container subdataWithRange:
          NSMakeRange(location.offset, location.length)];
      CGImageSourceRef source = CGImageSourceCreateWithData(
          (__bridge CFDataRef)sample, NULL);
      CGImageRef image = source
                             ? CGImageSourceCreateImageAtIndex(source, 0, NULL)
                             : NULL;
      valid = image && CGImageGetWidth(image) == expectedWidth &&
              CGImageGetHeight(image) == expectedHeight;
      if (image)
        CGImageRelease(image);
      if (source)
        CFRelease(source);
      if (!valid)
        *failure = [NSString stringWithFormat:
            @"Motion JPEG sample %llu cannot be decoded by ImageIO",
            (unsigned long long)index];
    }
  } else if (subtype == (FourCharCode)0x61766331U) {
    NSUInteger lengthBytes = 0;
    valid = AVCCLengthSize(container, &lengthBytes, failure);
    for (NSUInteger index = 0; valid && index < table.count; index++) {
      SampleLocation location = table.items[index];
      NSData *sample = [container subdataWithRange:
          NSMakeRange(location.offset, location.length)];
      valid = ValidateAVCCSample(sample, lengthBytes, failure);
    }
    if (valid)
      valid = DecodeAllH264Samples(asset, track, table.count, failure);
  } else {
    *failure = @"video codec is not an approved H.264 or Motion JPEG format";
    valid = NO;
  }
  if (valid)
    *sampleCount = table.count;
  free(table.items);
  return valid;
}

int main(int argc, const char *argv[]) {
  @autoreleasepool {
    if (argc != 2) {
      fprintf(stderr, "usage: video_decode_probe VIDEO\n");
      return 2;
    }
    NSURL *url = [NSURL fileURLWithPath:@(argv[1])];
    NSError *inputError = nil;
    NSData *inputData = [NSData dataWithContentsOfURL:url
                                              options:NSDataReadingMappedIfSafe
                                                error:&inputError];
    if (!inputData) {
      fprintf(stderr, "%s\n",
              inputError.localizedDescription.UTF8String ?: "cannot read video");
      return 3;
    }
    AVURLAsset *asset = [AVURLAsset URLAssetWithURL:url options:nil];
    __block NSArray<AVAssetTrack *> *videoTracks = nil;
    __block NSString *trackFailure = nil;
    dispatch_semaphore_t trackSemaphore = dispatch_semaphore_create(0);
    [asset loadTracksWithMediaType:AVMediaTypeVideo
                 completionHandler:^(NSArray<AVAssetTrack *> *tracks,
                                     NSError *error) {
      videoTracks = tracks;
      trackFailure = error.localizedDescription;
      dispatch_semaphore_signal(trackSemaphore);
    }];
    if (dispatch_semaphore_wait(
            trackSemaphore,
            dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC)) != 0) {
      fprintf(stderr, "video track parsing timed out\n");
      return 3;
    }
    if (videoTracks.count == 0) {
      fprintf(stderr, "%s\n",
              trackFailure.UTF8String ?: "file has no parseable video track");
      return 4;
    }
    AVAssetTrack *track = videoTracks.firstObject;
    CGSize naturalSize = track.naturalSize;
    size_t trackWidth = (size_t)llround(fabs(naturalSize.width));
    size_t trackHeight = (size_t)llround(fabs(naturalSize.height));
    double duration = CMTimeGetSeconds(track.timeRange.duration);
    if (trackWidth == 0 || trackHeight == 0 || !isfinite(duration) ||
        duration <= 0) {
      fprintf(stderr, "video track has invalid dimensions or duration\n");
      return 4;
    }
    NSArray *formatDescriptions = track.formatDescriptions;
    if (formatDescriptions.count == 0) {
      fprintf(stderr, "video track has no format description\n");
      return 4;
    }
    CMFormatDescriptionRef format =
        (__bridge CMFormatDescriptionRef)formatDescriptions.firstObject;
    FourCharCode subtype = CMFormatDescriptionGetMediaSubType(format);
    char codec[5] = {
        (char)((subtype >> 24) & 0xffU), (char)((subtype >> 16) & 0xffU),
        (char)((subtype >> 8) & 0xffU), (char)(subtype & 0xffU), 0};
    NSString *sampleFailure = nil;
    NSUInteger sampleCount = 0;
    if (!ValidateAllSamples(asset, track, inputData, subtype, trackWidth,
                            trackHeight, &sampleCount, &sampleFailure)) {
      fprintf(stderr, "%s\n",
              sampleFailure.UTF8String ?: "invalid compressed video samples");
      return 5;
    }
    AVAssetImageGenerator *generator =
        [[AVAssetImageGenerator alloc] initWithAsset:asset];
    generator.appliesPreferredTrackTransform = YES;
    __block size_t width = 0, height = 0;
    __block NSString *failure = nil;
    dispatch_semaphore_t semaphore = dispatch_semaphore_create(0);
    [generator
        generateCGImageAsynchronouslyForTime:kCMTimeZero
                           completionHandler:^(CGImageRef image,
                                               CMTime actualTime,
                                               NSError *error) {
      (void)actualTime;
      if (image) {
        width = CGImageGetWidth(image);
        height = CGImageGetHeight(image);
      } else {
        failure = error.localizedDescription ?: @"frame decode failed";
      }
      dispatch_semaphore_signal(semaphore);
    }];
    if (dispatch_semaphore_wait(
            semaphore, dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC)) != 0) {
      fprintf(stderr, "frame decode timed out\n");
      if (subtype == (FourCharCode)0x6a706567U &&
          DecoderIsBlockedBySandbox()) {
        printf("%zux%zu %.9g %s %zu\n", trackWidth, trackHeight, duration,
               codec, sampleCount);
        return 77;
      }
      return 6;
    }
    if (width == 0 || height == 0) {
      fprintf(stderr, "%s\n",
              failure.UTF8String ?: "decoded frame has invalid dimensions");
      if (subtype == (FourCharCode)0x6a706567U &&
          DecoderIsBlockedBySandbox()) {
        printf("%zux%zu %.9g %s %zu\n", trackWidth, trackHeight, duration,
               codec, sampleCount);
        return 77;
      }
      return 7;
    }
    printf("%zux%zu %.9g %s %zu\n", width, height, duration, codec,
           sampleCount);
  }
  return 0;
}
