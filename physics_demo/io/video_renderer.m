#import <AVFoundation/AVFoundation.h>
#import <CoreVideo/CoreVideo.h>
#import <Foundation/Foundation.h>

#import "video_render_core.h"


static int AtLeastOne(int value) { return value < 1 ? 1 : value; }

static void PrintError(NSError *error) {
  if (!error) {
    fprintf(stderr, "unknown AVFoundation error\n");
    return;
  }
  fprintf(stderr, "%s (domain=%s code=%ld)\n",
          error.localizedDescription.UTF8String,
          error.domain.UTF8String, (long)error.code);
  if (error.userInfo.count > 0)
    fprintf(stderr, "%s\n", error.userInfo.description.UTF8String);
}

static CGContextRef CreateBitmapContext(CVPixelBufferRef buffer, size_t width,
                                        size_t height) {
  CGColorSpaceRef colorSpace = CGColorSpaceCreateDeviceRGB();
  CGContextRef context = CGBitmapContextCreate(
      CVPixelBufferGetBaseAddress(buffer), width, height, 8,
      CVPixelBufferGetBytesPerRow(buffer), colorSpace,
      (CGBitmapInfo)kCGBitmapByteOrder32Little |
          (CGBitmapInfo)kCGImageAlphaPremultipliedFirst);
  CGColorSpaceRelease(colorSpace);
  return context;
}

int main(int argc, const char *argv[]) {
  @autoreleasepool {
    if (argc != 4) {
      fprintf(stderr, "usage: renderer RESULT_JSON OUTPUT_MP4 FPS\n");
      return 2;
    }
    NSError *error = nil;
    NSData *data = [NSData dataWithContentsOfFile:@(argv[1])
                                          options:0
                                            error:&error];
    if (!data) {
      fprintf(stderr, "%s\n", error.localizedDescription.UTF8String);
      return 3;
    }
    NSDictionary *root = [NSJSONSerialization JSONObjectWithData:data
                                                         options:0
                                                           error:&error];
    if (![root isKindOfClass:[NSDictionary class]]) {
      fprintf(stderr, "invalid result JSON\n");
      return 3;
    }
    NSDictionary *scene = root[@"scene"], *trajectory = root[@"trajectory"];
    NSArray *frames = trajectory[@"frames"];
    int32_t fps = (int32_t)AtLeastOne(atoi(argv[3]));
    if (![frames isKindOfClass:[NSArray class]] || frames.count == 0) {
      fprintf(stderr, "result has no frames\n");
      return 3;
    }
    NSURL *outputURL = [NSURL fileURLWithPath:@(argv[2])];
    [[NSFileManager defaultManager] removeItemAtURL:outputURL error:nil];
    AVAssetWriter *writer = [AVAssetWriter assetWriterWithURL:outputURL
                                                     fileType:AVFileTypeMPEG4
                                                        error:&error];
    if (!writer) {
      PrintError(error);
      return 4;
    }
    const size_t width = 960, height = 540;
    PhyVideoCamera camera = PhyVideoBuildTrajectoryCamera(scene, trajectory, width, height);
    NSDictionary *settings = @{
      AVVideoCodecKey : AVVideoCodecTypeH264,
      AVVideoWidthKey : @(width),
      AVVideoHeightKey : @(height),
      AVVideoCompressionPropertiesKey : @{
        AVVideoAverageBitRateKey : @3200000,
        AVVideoExpectedSourceFrameRateKey : @(fps),
        AVVideoMaxKeyFrameIntervalKey : @(fps * 2)
      }
    };
    AVAssetWriterInput *input =
        [AVAssetWriterInput assetWriterInputWithMediaType:AVMediaTypeVideo
                                           outputSettings:settings];
    input.expectsMediaDataInRealTime = NO;
    AVAssetWriterInputPixelBufferAdaptor *adaptor =
        [AVAssetWriterInputPixelBufferAdaptor
            assetWriterInputPixelBufferAdaptorWithAssetWriterInput:input
                                       sourcePixelBufferAttributes:@{
                                         (NSString *)
                                         kCVPixelBufferPixelFormatTypeKey :
                                             @(kCVPixelFormatType_32BGRA),
                                         (NSString *)
                                         kCVPixelBufferWidthKey : @(width),
                                         (NSString *)
                                         kCVPixelBufferHeightKey : @(height)
                                       }];
    if (![writer canAddInput:input]) {
      fprintf(stderr, "cannot add video input\n");
      return 4;
    }
    [writer addInput:input];
    if (![writer startWriting]) {
      PrintError(writer.error);
      return 4;
    }
    [writer startSessionAtSourceTime:kCMTimeZero];
    for (NSUInteger index = 0; index < frames.count; index++) {
      @autoreleasepool {
        while (!input.readyForMoreMediaData)
          [NSThread sleepForTimeInterval:0.002];
        CVPixelBufferRef buffer = NULL;
        if (CVPixelBufferPoolCreatePixelBuffer(NULL, adaptor.pixelBufferPool,
                                               &buffer) != kCVReturnSuccess ||
            !buffer) {
          fprintf(stderr, "pixel buffer allocation failed\n");
          return 5;
        }
        CVPixelBufferLockBaseAddress(buffer, 0);
        CGContextRef context = CreateBitmapContext(buffer, width, height);
        if (!context) {
          CVPixelBufferRelease(buffer);
          fprintf(stderr, "graphics context failed\n");
          return 5;
        }
        BOOL rendered = PhyVideoDrawTrajectoryFrame(context, frames[index],
                  index > 0 ? frames[index - 1] : frames[index], scene, trajectory,
                  camera, width, height);
        CGContextRelease(context);
        CVPixelBufferUnlockBaseAddress(buffer, 0);
        if (!rendered) {
          CVPixelBufferRelease(buffer);
          fprintf(stderr, "rendering failed: invalid mesh/connection data or allocation\n");
          return 5;
        }
        BOOL appended =
            [adaptor appendPixelBuffer:buffer
                  withPresentationTime:CMTimeMake((int64_t)index, fps)];
        CVPixelBufferRelease(buffer);
        if (!appended) {
          PrintError(writer.error);
          return 5;
        }
      }
    }
    [input markAsFinished];
    dispatch_semaphore_t semaphore = dispatch_semaphore_create(0);
    [writer finishWritingWithCompletionHandler:^{
      dispatch_semaphore_signal(semaphore);
    }];
    dispatch_semaphore_wait(semaphore, DISPATCH_TIME_FOREVER);
    if (writer.status != AVAssetWriterStatusCompleted) {
      PrintError(writer.error);
      return 6;
    }
  }
  return 0;
}
