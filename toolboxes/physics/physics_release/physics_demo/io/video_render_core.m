#import "video_render_core.h"

#include <float.h>
#include <stdint.h>

/* Display-only liquid reconstruction uses recorded samples and material tags.
 * Both legacy and mixed frames reconstruct the same per-material compact
 * screen-space field and smooth its front depth before shading.
 * No particles, solver states, or physical collision radii are changed.
 * This is a bounded visual surface, not a measured volume or optical solver. */

typedef struct {
  double u, v, depth;
} CameraPoint;
typedef struct {
  double minimumU, maximumU, minimumV, maximumV, minimumDepth, maximumDepth;
  double pitch;
  BOOL hasPoint;
} CameraBounds;
typedef struct {
  CGPoint point;
  int material; /* 0 water, 1 sand, 2 honey, 3 glue, 4 molten lead, 5 lava */
  int depthBand;
  BOOL surface;
} RenderParticle;

/* Fitting and projection share the selected three-quarter camera orientation
 * so auto-fit never clips the geometry it later renders. */
static const double kCameraYaw = -0.62;
static const double kCameraPitch = -0.33;
static const int kDepthBands = 5;

static NSArray *Vector(id value) {
  return [value isKindOfClass:[NSArray class]] && [value count] == 3
             ? value
             : @[ @0, @0, @0 ];
}
static double VectorValue(NSArray *value, NSUInteger index) {
  return [value[index] doubleValue];
}
static BOOL MeshVector(id value) {
  if (![value isKindOfClass:[NSArray class]] || [value count] != 3)
    return NO;
  for (id component in value)
    if (![component isKindOfClass:[NSNumber class]] || !isfinite([component doubleValue]))
      return NO;
  return YES;
}
static BOOL UnitQuaternion(id value) {
  if (![value isKindOfClass:[NSArray class]] || [value count] != 4) return NO;
  double norm = 0;
  for (id component in value) {
    if (![component isKindOfClass:[NSNumber class]] || !isfinite([component doubleValue])) return NO;
    double x = [component doubleValue]; norm += x * x;
  }
  return fabs(norm - 1.0) < 1e-6;
}
static NSArray *RotateBodyPoint(NSArray *q, NSArray *local, NSArray *center) {
  double w=[q[0] doubleValue],x=[q[1] doubleValue],y=[q[2] doubleValue],z=[q[3] doubleValue];
  double a=VectorValue(local,0),b=VectorValue(local,1),c=VectorValue(local,2);
  double tx=2*(y*c-z*b),ty=2*(z*a-x*c),tz=2*(x*b-y*a);
  return @[@(VectorValue(center,0)+a+w*tx+y*tz-z*ty),
           @(VectorValue(center,1)+b+w*ty+z*tx-x*tz),
           @(VectorValue(center,2)+c+w*tz+x*ty-y*tx)];
}
static BOOL MeshIndex(id value, NSUInteger maximum, NSUInteger *result) {
  if (![value isKindOfClass:[NSNumber class]])
    return NO;
  double number = [value doubleValue];
  if (!isfinite(number) || number < 0 || number > (double)maximum || floor(number) != number)
    return NO;
  *result = [value unsignedIntegerValue];
  return YES;
}
static double Clamp(double value, double minimum, double maximum) {
  return fmin(maximum, fmax(minimum, value));
}
static int AtLeastOne(int value) { return value < 1 ? 1 : value; }

static CameraPoint RotatePoint(NSArray *point, double pitch) {
  double x = VectorValue(point, 0), y = VectorValue(point, 1),
         z = VectorValue(point, 2);
  double rotatedX = cos(kCameraYaw) * x - sin(kCameraYaw) * z;
  double rotatedZ = sin(kCameraYaw) * x + cos(kCameraYaw) * z;
  return (CameraPoint){
      rotatedX,
      cos(pitch) * y - sin(pitch) * rotatedZ,
      sin(pitch) * y + cos(pitch) * rotatedZ};
}

static void IncludePoint(CameraBounds *bounds, NSArray *point) {
  CameraPoint value = RotatePoint(Vector(point), bounds->pitch);
  if (!bounds->hasPoint) {
    bounds->minimumU = bounds->maximumU = value.u;
    bounds->minimumV = bounds->maximumV = value.v;
    bounds->minimumDepth = bounds->maximumDepth = value.depth;
    bounds->hasPoint = YES;
    return;
  }
  bounds->minimumU = fmin(bounds->minimumU, value.u);
  bounds->maximumU = fmax(bounds->maximumU, value.u);
  bounds->minimumV = fmin(bounds->minimumV, value.v);
  bounds->maximumV = fmax(bounds->maximumV, value.v);
  bounds->minimumDepth = fmin(bounds->minimumDepth, value.depth);
  bounds->maximumDepth = fmax(bounds->maximumDepth, value.depth);
}

static void IncludeSphere(CameraBounds *bounds, NSArray *center,
                          double radius) {
  NSArray *safeCenter = Vector(center);
  for (NSUInteger axis = 0; axis < 3; axis++)
    for (NSInteger sign = -1; sign <= 1; sign += 2) {
      NSMutableArray *point = [safeCenter mutableCopy];
      point[axis] = @([safeCenter[axis] doubleValue] + (double)sign * radius);
      IncludePoint(bounds, point);
    }
}

static void IncludeBox(CameraBounds *bounds, NSArray *center, NSArray *size) {
  NSArray *safeCenter = Vector(center), *safeSize = Vector(size);
  for (NSInteger x = -1; x <= 1; x += 2)
    for (NSInteger y = -1; y <= 1; y += 2)
      for (NSInteger z = -1; z <= 1; z += 2) {
        IncludePoint(bounds, @[
          @([safeCenter[0] doubleValue] +
            0.5 * (double)x * [safeSize[0] doubleValue]),
          @([safeCenter[1] doubleValue] +
            0.5 * (double)y * [safeSize[1] doubleValue]),
          @([safeCenter[2] doubleValue] +
            0.5 * (double)z * [safeSize[2] doubleValue]),
        ]);
      }
}

PhyVideoCamera PhyVideoBuildCamera(NSArray *frames, NSArray *rigidShapes,
                          NSArray *colliders, NSArray *minimum,
                          NSArray *maximum, double particleRadius, size_t width,
                          size_t height) {
  CameraBounds bounds = {0};
  bounds.pitch = kCameraPitch;
  for (NSDictionary *frame in frames)
    if ([frame[@"m"] isKindOfClass:[NSArray class]] && [frame[@"m"] count] > 0) {
      /* Mesh surfaces benefit from seeing the top and any insertion opening.
       * Preserve the established particle camera for existing trajectories. */
      bounds.pitch = 0.42;
      break;
    }
  for (NSDictionary *frame in frames) {
    for (NSArray *point in frame[@"__camera_extra_points"] ?: @[])
      if (MeshVector(point)) IncludePoint(&bounds,point);
    for (NSArray *point in (
             [frame[@"p"] isKindOfClass:[NSArray class]] ? frame[@"p"] : @[]))
      IncludePoint(&bounds, point);
    for (NSArray *point in (
             [frame[@"g"] isKindOfClass:[NSArray class]] ? frame[@"g"] : @[]))
      IncludeSphere(&bounds, point, 0.08);
    for (NSArray *point in (
             [frame[@"m"] isKindOfClass:[NSArray class]] ? frame[@"m"] : @[]))
      if (MeshVector(point))
        IncludePoint(&bounds, point);
    NSArray *rigids =
        [frame[@"r"] isKindOfClass:[NSArray class]] ? frame[@"r"] : @[];
    NSArray *poses = [frame[@"q"] isKindOfClass:[NSArray class]] ? frame[@"q"] : @[];
    for (NSUInteger index = 0; index < rigids.count; index++) {
      NSDictionary *shape =
          index < rigidShapes.count ? rigidShapes[index] : @{};
      if (index < poses.count && UnitQuaternion(poses[index]) &&
          ([shape[@"type"] isEqual:@"box"] || [shape[@"type"] isEqual:@"cylinder"])) {
        NSArray *size = [shape[@"type"] isEqual:@"box"] ? Vector(shape[@"size"]) :
            @[@(2*[shape[@"radius"] doubleValue]), shape[@"height"] ?: @0,
              @(2*[shape[@"radius"] doubleValue])];
        for (int x=-1;x<=1;x+=2) for (int y=-1;y<=1;y+=2) for (int z=-1;z<=1;z+=2)
          IncludePoint(&bounds,RotateBodyPoint(poses[index],
            @[@(0.5*x*VectorValue(size,0)),@(0.5*y*VectorValue(size,1)),@(0.5*z*VectorValue(size,2))],
            Vector(rigids[index])));
      } else if ([shape[@"type"] isEqual:@"box"])
        IncludeBox(&bounds, rigids[index], shape[@"size"]);
      else
        IncludeSphere(&bounds, rigids[index],
                      fmax(0.01, [shape[@"radius"] doubleValue]));
    }
  }
  for (NSDictionary *collider in colliders) {
    NSString *kind = collider[@"type"];
    if ([kind isEqual:@"sphere"])
      IncludeSphere(&bounds, collider[@"center"],
                    [collider[@"radius"] doubleValue]);
    else if ([kind isEqual:@"box"])
      IncludeBox(&bounds, collider[@"center"], collider[@"size"]);
    else if ([kind isEqual:@"capsule"]) {
      IncludeSphere(&bounds, collider[@"a"], [collider[@"radius"] doubleValue]);
      IncludeSphere(&bounds, collider[@"b"], [collider[@"radius"] doubleValue]);
    }
  }
  if (!bounds.hasPoint) {
    NSArray *center = @[
      @((VectorValue(minimum, 0) + VectorValue(maximum, 0)) * 0.5),
      @((VectorValue(minimum, 1) + VectorValue(maximum, 1)) * 0.5),
      @((VectorValue(minimum, 2) + VectorValue(maximum, 2)) * 0.5)
    ];
    NSArray *size = @[
      @(VectorValue(maximum, 0) - VectorValue(minimum, 0)),
      @(VectorValue(maximum, 1) - VectorValue(minimum, 1)),
      @(VectorValue(maximum, 2) - VectorValue(minimum, 2))
    ];
    IncludeBox(&bounds, center, size);
  }
  /* Fit the recorded geometry at its own scale.  The old 0.55 m floor made
   * millimetre droplets occupy only a few pixels even though their trajectory
   * was valid.  Eight particle radii still bound a stationary one-sample
   * liquid; the absolute epsilon only protects degenerate non-particle input. */
  double minimumSpan = fmax(1.0e-4, 8.0 * particleRadius);
  double spanU = fmax(bounds.maximumU - bounds.minimumU, minimumSpan);
  double spanV = fmax(bounds.maximumV - bounds.minimumV, minimumSpan);
  double contentSpan = fmax(spanU, spanV);
  spanU = fmax(spanU, 0.34 * contentSpan);
  spanV = fmax(spanV, 0.34 * contentSpan);
  double scale =
      fmin(0.86 * (double)width / spanU, 0.76 * (double)height / spanV) / 1.16;
  double depthPadding =
      fmax(0.05, 0.08 * (bounds.maximumDepth - bounds.minimumDepth));
  return (PhyVideoCamera){
      cos(kCameraYaw),
      sin(kCameraYaw),
      cos(bounds.pitch),
      sin(bounds.pitch),
      0.5 * (bounds.minimumU + bounds.maximumU),
      0.5 * (bounds.minimumV + bounds.maximumV),
      scale,
      0.5 * (double)width,
      0.48 * (double)height,
      bounds.minimumDepth - depthPadding,
      bounds.maximumDepth + depthPadding,
  };
}

static CameraPoint Project(NSArray *point, PhyVideoCamera camera) {
  double x = VectorValue(point, 0), y = VectorValue(point, 1),
         z = VectorValue(point, 2);
  double rotatedX = camera.cosineYaw * x - camera.sineYaw * z;
  double rotatedZ = camera.sineYaw * x + camera.cosineYaw * z;
  return (CameraPoint){
      camera.originX + (rotatedX - camera.centerU) * camera.scale,
      camera.originY + (camera.cosinePitch * y - camera.sinePitch * rotatedZ -
                        camera.centerV) *
                           camera.scale,
      camera.sinePitch * y + camera.cosinePitch * rotatedZ,
  };
}
static int DepthBand(double depth, PhyVideoCamera camera) {
  double range = fmax(1.0e-9, camera.maximumDepth - camera.minimumDepth);
  int band =
      (int)floor((double)kDepthBands * (depth - camera.minimumDepth) / range);
  if (band < 0)
    return 0;
  if (band >= kDepthBands)
    return kDepthBands - 1;
  return band;
}

static void FillCircle(CGContextRef context, CGPoint point, double radius,
                       CGFloat red, CGFloat green, CGFloat blue,
                       CGFloat alpha) {
  CGContextSetRGBFillColor(context, red, green, blue, alpha);
  CGContextFillEllipseInRect(context,
                             CGRectMake(point.x - radius, point.y - radius,
                                        2.0 * radius, 2.0 * radius));
}
static void StrokeCircle(CGContextRef context, CGPoint point, double radius,
                         CGFloat red, CGFloat green, CGFloat blue,
                         CGFloat alpha, double lineWidth) {
  CGContextSetRGBStrokeColor(context, red, green, blue, alpha);
  CGContextSetLineWidth(context, lineWidth);
  CGContextStrokeEllipseInRect(context,
                               CGRectMake(point.x - radius, point.y - radius,
                                          2.0 * radius, 2.0 * radius));
}
static void DrawVerticalGradient(CGContextRef context, CGRect rectangle,
                                 const CGFloat components[8]) {
  CGColorSpaceRef space = CGColorSpaceCreateDeviceRGB();
  CGFloat locations[] = {0.0, 1.0};
  CGGradientRef gradient =
      CGGradientCreateWithColorComponents(space, components, locations, 2);
  CGContextDrawLinearGradient(
      context, gradient,
      CGPointMake(CGRectGetMidX(rectangle), CGRectGetMinY(rectangle)),
      CGPointMake(CGRectGetMidX(rectangle), CGRectGetMaxY(rectangle)), 0);
  CGGradientRelease(gradient);
  CGColorSpaceRelease(space);
}

static void DrawBackdrop(CGContextRef context, size_t width, size_t height) {
  CGFloat canvasWidth = (CGFloat)width, canvasHeight = (CGFloat)height;
  const CGFloat background[] = {0.018, 0.029, 0.055, 1.0,
                                0.075, 0.115, 0.175, 1.0};
  DrawVerticalGradient(context, CGRectMake(0, 0, canvasWidth, canvasHeight),
                       background);
  CGColorSpaceRef space = CGColorSpaceCreateDeviceRGB();
  CGFloat components[] = {0.12, 0.23, 0.35, 0.22, 0.01, 0.02, 0.04, 0.0};
  CGFloat locations[] = {0.0, 1.0};
  CGGradientRef glow =
      CGGradientCreateWithColorComponents(space, components, locations, 2);
  CGContextDrawRadialGradient(
      context, glow,
      CGPointMake(0.50 * canvasWidth, 0.66 * canvasHeight), 1.0,
      CGPointMake(0.50 * canvasWidth, 0.60 * canvasHeight),
      0.78 * canvasWidth, 0);
  CGGradientRelease(glow);
  CGColorSpaceRelease(space);
}

double PhyVideoHorizontalFloor(NSArray *colliders, NSArray *minimum) {
  double selected = VectorValue(minimum, 1);
  BOOL found = NO;
  for (NSDictionary *collider in colliders) {
    if ([collider[@"type"] isEqual:@"plane"]) {
      NSArray *normal = Vector(collider[@"normal"]);
      double nx = VectorValue(normal, 0), ny = VectorValue(normal, 1),
             nz = VectorValue(normal, 2);
      double length = sqrt(nx * nx + ny * ny + nz * nz);
      if (length > 1.0e-15) {
        double unitY = ny / length;
        if (unitY > 0.9) {
          /* Collision code normalizes the normal while retaining offset, so
             apply the identical convention here.  Multiple upward planes
             constrain the scene to their highest horizontal intercept. */
          double candidate = [collider[@"offset"] doubleValue] / unitY;
          if (!found || candidate > selected) selected = candidate;
          found = YES;
        }
      }
    }
  }
  return selected;
}

static void DrawGround(CGContextRef context, NSArray *colliders,
                       NSArray *minimum, NSArray *maximum, PhyVideoCamera camera) {
  double floorY = PhyVideoHorizontalFloor(colliders, minimum);
  double minX = VectorValue(minimum, 0), maxX = VectorValue(maximum, 0),
         minZ = VectorValue(minimum, 2), maxZ = VectorValue(maximum, 2);
  /* Numerical walls may be far outside the visible scene. Fit grid work to
   * the viewport, and keep its SI spacing independent of those wall positions. */
  if (fabs(camera.sinePitch)>1e-6 && camera.scale>0) {
    CGRect clip=CGContextGetClipBoundingBox(context);
    double loX=DBL_MAX,hiX=-DBL_MAX,loZ=DBL_MAX,hiZ=-DBL_MAX;
    for (int i=0;i<4;i++) {
      double u=((i&1?CGRectGetMaxX(clip):CGRectGetMinX(clip))-camera.originX)/camera.scale+camera.centerU;
      double v=((i&2?CGRectGetMaxY(clip):CGRectGetMinY(clip))-camera.originY)/camera.scale+camera.centerV;
      double depth=(camera.cosinePitch*floorY-v)/camera.sinePitch;
      double x=camera.cosineYaw*u+camera.sineYaw*depth;
      double z=-camera.sineYaw*u+camera.cosineYaw*depth;
      loX=fmin(loX,x);hiX=fmax(hiX,x);loZ=fmin(loZ,z);hiZ=fmax(hiZ,z);
    }
    minX=fmax(minX,loX);maxX=fmin(maxX,hiX);
    minZ=fmax(minZ,loZ);maxZ=fmin(maxZ,hiZ);
  }
  if (minX>=maxX || minZ>=maxZ) return;
  CameraPoint corners[4] = {
    Project(@[ @(minX), @(floorY), @(minZ) ], camera),
    Project(@[ @(maxX), @(floorY), @(minZ) ], camera),
    Project(@[ @(maxX), @(floorY), @(maxZ) ], camera),
    Project(@[ @(minX), @(floorY), @(maxZ) ], camera)
  };
  CGContextBeginPath(context);
  CGContextMoveToPoint(context, corners[0].u, corners[0].v);
  for (int index = 1; index < 4; index++)
    CGContextAddLineToPoint(context, corners[index].u, corners[index].v);
  CGContextClosePath(context);
  CGContextSetRGBFillColor(context, 0.09, 0.13, 0.18, 0.42);
  CGContextFillPath(context);
  CGContextSetLineWidth(context, 0.8);
  double wanted=40.0/fmax(camera.scale,1e-9);
  double step=pow(10.0,floor(log10(wanted)));
  double ratio=wanted/step;
  step*=ratio>=5?5:(ratio>=2?2:1);
  for (int axis=0;axis<2;axis++) {
    double low=axis?minZ:minX,high=axis?maxZ:maxX;
    double spacing=fmax(step,(high-low)/512.0);
    double start=ceil(low/spacing)*spacing;
    for (int line=0;line<=512;line++) {
      double value=start+line*spacing;
      if(value>high) break;
      CameraPoint a=Project(axis?@[@(minX),@(floorY),@(value)]:@[@(value),@(floorY),@(minZ)],camera);
      CameraPoint b=Project(axis?@[@(maxX),@(floorY),@(value)]:@[@(value),@(floorY),@(maxZ)],camera);
      CGContextSetRGBStrokeColor(context,0.44,0.58,0.70,fabs(value)<1e-10?.25:.12);
      CGContextMoveToPoint(context,a.u,a.v);CGContextAddLineToPoint(context,b.u,b.v);
      CGContextStrokePath(context);
    }
  }
}

static void DrawShadedSphere(CGContextRef context, CGPoint point,
                             double radius) {
  radius = fmax(2.0, radius);
  FillCircle(context,
             CGPointMake(point.x + 0.10 * radius, point.y - 0.10 * radius),
             1.04 * radius, 0, 0, 0, 0.32);
  CGContextSaveGState(context);
  CGContextAddEllipseInRect(
      context,
      CGRectMake(point.x - radius, point.y - radius, 2 * radius, 2 * radius));
  CGContextClip(context);
  CGColorSpaceRef space = CGColorSpaceCreateDeviceRGB();
  CGFloat components[] = {0.83, 0.88, 0.94, 1, 0.20, 0.26, 0.34, 1};
  CGFloat locations[] = {0, 1};
  CGGradientRef gradient =
      CGGradientCreateWithColorComponents(space, components, locations, 2);
  CGContextDrawRadialGradient(
      context, gradient,
      CGPointMake(point.x - 0.32 * radius, point.y + 0.38 * radius),
      0.05 * radius, point, 1.18 * radius, 0);
  CGGradientRelease(gradient);
  CGColorSpaceRelease(space);
  CGContextRestoreGState(context);
  StrokeCircle(context, point, radius, 0.93, 0.97, 1, 0.72, 1.4);
}

static void BoxCorners(NSArray *center, NSArray *size, PhyVideoCamera camera,
                       CameraPoint corners[8]) {
  NSArray *safeCenter = Vector(center), *safeSize = Vector(size);
  NSUInteger index = 0;
  for (NSInteger x = -1; x <= 1; x += 2)
    for (NSInteger y = -1; y <= 1; y += 2)
      for (NSInteger z = -1; z <= 1; z += 2)
        corners[index++] = Project(
            @[
              @(VectorValue(safeCenter, 0) +
                0.5 * (double)x * VectorValue(safeSize, 0)),
              @(VectorValue(safeCenter, 1) +
                0.5 * (double)y * VectorValue(safeSize, 1)),
              @(VectorValue(safeCenter, 2) +
                0.5 * (double)z * VectorValue(safeSize, 2))
            ],
            camera);
}

typedef struct {
  int index;
  double depth;
} FaceOrder;

static BOOL IsThinWallBox(NSArray *size) {
  NSArray *safeSize = Vector(size);
  double dimensions[3] = {fabs(VectorValue(safeSize, 0)),
                          fabs(VectorValue(safeSize, 1)),
                          fabs(VectorValue(safeSize, 2))};
  for (int left = 0; left < 2; left++)
    for (int right = left + 1; right < 3; right++)
      if (dimensions[left] > dimensions[right]) {
        double temporary = dimensions[left];
        dimensions[left] = dimensions[right];
        dimensions[right] = temporary;
      }
  /* A panel must be thin in exactly one direction. Rods and compact boxes
   * remain opaque, even when their absolute dimensions are small. */
  return dimensions[0] <= 0.22 * dimensions[1] &&
         dimensions[1] > 1.0e-9;
}

static void DrawShadedBox(CGContextRef context, NSArray *center, NSArray *size,
                          PhyVideoCamera camera, BOOL thinWall) {
  CameraPoint corners[8];
  BoxCorners(center, size, camera, corners);
  const int faces[6][4] = {{0, 1, 3, 2}, {4, 6, 7, 5}, {0, 4, 5, 1},
                           {2, 3, 7, 6}, {0, 2, 6, 4}, {1, 5, 7, 3}};
  FaceOrder order[6];
  for (int face = 0; face < 6; face++) {
    order[face] = (FaceOrder){
        face,
        0.25 * (corners[faces[face][0]].depth + corners[faces[face][1]].depth +
                corners[faces[face][2]].depth + corners[faces[face][3]].depth)};
  }
  for (int left = 0; left < 5; left++)
    for (int right = left + 1; right < 6; right++)
      if (order[left].depth > order[right].depth) {
        FaceOrder temporary = order[left];
        order[left] = order[right];
        order[right] = temporary;
      }
  for (int item = 0; item < 6; item++) {
    int face = order[item].index;
    CGContextBeginPath(context);
    CGContextMoveToPoint(context, corners[faces[face][0]].u,
                         corners[faces[face][0]].v);
    for (int corner = 1; corner < 4; corner++)
      CGContextAddLineToPoint(context, corners[faces[face][corner]].u,
                              corners[faces[face][corner]].v);
    CGContextClosePath(context);
    if (thinWall) {
      /* A collision wall often represents a glass vessel or tank boundary.
       * A low-alpha cool tint keeps its extent visible without hiding the
       * recorded water behind it. */
      CGFloat alpha = 0.010 + 0.002 * item;
      CGContextSetRGBFillColor(context, 0.62, 0.82, 0.94, alpha);
    } else {
      CGFloat shade = 0.30 + 0.035 * face;
      CGContextSetRGBFillColor(context, shade, shade + 0.045, shade + 0.10,
                               0.90);
    }
    CGContextFillPath(context);
  }
  const int edges[12][2] = {{0, 1}, {0, 2}, {0, 4}, {1, 3}, {1, 5}, {2, 3},
                            {2, 6}, {3, 7}, {4, 5}, {4, 6}, {5, 7}, {6, 7}};
  double minimumDepth = corners[0].depth, maximumDepth = corners[0].depth;
  for (int corner = 1; corner < 8; corner++) {
    minimumDepth = fmin(minimumDepth, corners[corner].depth);
    maximumDepth = fmax(maximumDepth, corners[corner].depth);
  }
  double depthRange = fmax(1.0e-9, maximumDepth - minimumDepth);
  for (int edge = 0; edge < 12; edge++) {
    if (thinWall) {
      double edgeDepth =
          0.5 * (corners[edges[edge][0]].depth +
                 corners[edges[edge][1]].depth);
      double nearness = Clamp((edgeDepth - minimumDepth) / depthRange, 0.0, 1.0);
      CGContextSetRGBStrokeColor(context, 0.68, 0.88, 0.98,
                                 0.22 + 0.46 * nearness);
      CGContextSetLineWidth(context, 0.80 + 0.55 * nearness);
    } else {
      CGContextSetRGBStrokeColor(context, 0.84, 0.91, 0.98, 0.72);
      CGContextSetLineWidth(context, 1.3);
    }
    CGContextMoveToPoint(context, corners[edges[edge][0]].u,
                         corners[edges[edge][0]].v);
    CGContextAddLineToPoint(context, corners[edges[edge][1]].u,
                            corners[edges[edge][1]].v);
    CGContextStrokePath(context);
  }
}

static double ColliderDepth(NSDictionary *collider, PhyVideoCamera camera) {
  if ([collider[@"type"] isEqual:@"capsule"]) {
    NSArray *a = Vector(collider[@"a"]), *b = Vector(collider[@"b"]);
    return Project(
               @[
                 @((VectorValue(a, 0) + VectorValue(b, 0)) * 0.5),
                 @((VectorValue(a, 1) + VectorValue(b, 1)) * 0.5),
                 @((VectorValue(a, 2) + VectorValue(b, 2)) * 0.5)
               ],
               camera)
        .depth;
  }
  return Project(Vector(collider[@"center"]), camera).depth;
}
static void DrawCollider(CGContextRef context, NSDictionary *collider,
                         PhyVideoCamera camera) {
  NSString *kind = collider[@"type"];
  if ([kind isEqual:@"sphere"]) {
    CameraPoint center = Project(Vector(collider[@"center"]), camera);
    DrawShadedSphere(context, CGPointMake(center.u, center.v),
                     [collider[@"radius"] doubleValue] * camera.scale);
  } else if ([kind isEqual:@"box"])
    DrawShadedBox(
        context, collider[@"center"], collider[@"size"], camera,
        [collider[@"appearance"] isEqual:@"glass"] ||
            (!collider[@"appearance"] && IsThinWallBox(collider[@"size"])));
  else if ([kind isEqual:@"capsule"]) {
    CameraPoint a = Project(Vector(collider[@"a"]), camera),
                b = Project(Vector(collider[@"b"]), camera);
    double diameter =
        fmax(3, 2 * [collider[@"radius"] doubleValue] * camera.scale);
    CGContextSetLineCap(context, kCGLineCapRound);
    CGContextSetRGBStrokeColor(context, 0.01, 0.02, 0.04, 0.35);
    CGContextSetLineWidth(context, diameter + 4);
    CGContextMoveToPoint(context, a.u + 2, a.v - 2);
    CGContextAddLineToPoint(context, b.u + 2, b.v - 2);
    CGContextStrokePath(context);
    CGContextSetRGBStrokeColor(context, 0.31, 0.38, 0.47, 1);
    CGContextSetLineWidth(context, diameter);
    CGContextMoveToPoint(context, a.u, a.v);
    CGContextAddLineToPoint(context, b.u, b.v);
    CGContextStrokePath(context);
    CGContextSetRGBStrokeColor(context, 0.83, 0.89, 0.95, 0.64);
    CGContextSetLineWidth(context, fmax(1, 0.08 * diameter));
    CGContextMoveToPoint(context, a.u - 0.14 * diameter, a.v + 0.14 * diameter);
    CGContextAddLineToPoint(context, b.u - 0.14 * diameter,
                            b.v + 0.14 * diameter);
    CGContextStrokePath(context);
    CGContextSetLineCap(context, kCGLineCapButt);
  }
}

enum { kMaterialCount = 6 };

static int RenderMaterial(id name) {
  if ([name isEqual:@"sand"]) return 1;
  if ([name isEqual:@"honey"]) return 2;
  if ([name isEqual:@"glue"]) return 3;
  if ([name isEqual:@"molten_lead"]) return 4;
  if ([name isEqual:@"lava"]) return 5;
  return 0; /* Missing legacy metadata means water. */
}

static void LiquidPalette(int material, CGFloat colors[8]) {
  static const CGFloat palettes[kMaterialCount][8] = {
    {0.015,0.18,0.30,0.88, 0.32,0.79,0.96,0.94},
    {0,0,0,1, 0,0,0,1},
    {0.37,0.14,0.025,0.94, 0.96,0.61,0.12,0.98},
    {0.58,0.64,0.62,0.98, 0.96,0.98,0.91,1.0},
    {0.17,0.20,0.23,1.0, 0.82,0.88,0.92,1.0},
    {0.42,0.035,0.005,1.0, 1.0,0.35,0.015,1.0}
  };
  for (int i=0;i<8;i++) colors[i]=palettes[material][i];
}

/* Optional v2 display path. It uses the recorded water samples and their
 * previous positions; the simulation and saved trajectory are untouched. */
typedef struct {
  CGPoint point, previousPoint;
  double depth;
  int material, localDensity, depthBand;
  BOOL surface;
} LegacyWaterParticle;

static CGImageRef CreateLegacyWaterMask(LegacyWaterParticle *particles, NSUInteger count,
                                  int band, double radius, size_t width,
                                  size_t height) {
  CGColorSpaceRef space = CGColorSpaceCreateDeviceGray();
  CGContextRef maskContext = CGBitmapContextCreate(
      NULL, width, height, 8, width, space, (CGBitmapInfo)kCGImageAlphaNone);
  CGColorSpaceRelease(space);
  if (!maskContext)
    return NULL;
  CGContextSetGrayFillColor(maskContext, 0, 1);
  CGContextFillRect(maskContext,
                    CGRectMake(0, 0, (CGFloat)width, (CGFloat)height));
  CGContextSetGrayFillColor(maskContext, 1, 1);
  CGContextSetShouldAntialias(maskContext, true);
  for (NSUInteger index = 0; index < count; index++) {
    LegacyWaterParticle particle = particles[index];
    if (particle.material != 0 || (band >= 0 && particle.depthBand != band))
      continue;
    /* Dense samples overlap into a liquid sheet. Sparse samples keep their
     * measured separation, so genuine spray does not turn into a fake blob. */
    double visualRadius = (particle.localDensity > 2 ? 1.72 : 1.12) * radius;
    CGContextFillEllipseInRect(maskContext,
                               CGRectMake(particle.point.x - visualRadius,
                                          particle.point.y - visualRadius,
                                          2 * visualRadius, 2 * visualRadius));
  }
  CGImageRef mask = CGBitmapContextCreateImage(maskContext);
  CGContextRelease(maskContext);
  return mask;
}

static void DrawLegacyWaterBody(CGContextRef context, LegacyWaterParticle *particles,
                          NSUInteger count, double radius, size_t width,
                          size_t height) {
  CGImageRef mask =
      CreateLegacyWaterMask(particles, count, -1, radius, width, height);
  if (!mask)
    return;
  double canvasWidth = (double)width, canvasHeight = (double)height;
  CGRect canvas = CGRectMake(0, 0, (CGFloat)canvasWidth,
                             (CGFloat)canvasHeight);
  CGContextSaveGState(context);
  CGContextClipToMask(context, CGRectOffset(canvas, 2, -2), mask);
  CGContextSetRGBFillColor(context, 0, 0.01, 0.025, 0.26);
  CGContextFillRect(context, canvas);
  CGContextRestoreGState(context);
  CGFloat colors[8] = {
      0.025, 0.32, 0.61, 0.79,
      0.10, 0.69, 0.96, 0.87,
  };
  CGContextSaveGState(context);
  CGContextClipToMask(context, canvas, mask);
  DrawVerticalGradient(context, canvas, colors);
  CGContextRestoreGState(context);
  CGImageRelease(mask);
}

static void DrawLegacyWaterBand(CGContextRef context, LegacyWaterParticle *particles,
                          NSUInteger count, int band, double radius) {
  for (NSUInteger index = 0; index < count; index++) {
    LegacyWaterParticle particle = particles[index];
    if (particle.material != 0 || particle.depthBand != band)
      continue;
    double dx = particle.point.x - particle.previousPoint.x,
           dy = particle.point.y - particle.previousPoint.y,
           travel = hypot(dx, dy);
    if (particle.localDensity <= 7 && travel > 0.8) {
      double limited = fmin(travel, 2.8 * radius + 7),
             factor = limited / travel;
      CGContextSetRGBStrokeColor(context, 0.18, 0.72, 1, 0.23);
      CGContextSetLineWidth(context, fmax(1, 0.62 * radius));
      CGContextSetLineCap(context, kCGLineCapRound);
      CGContextMoveToPoint(context, particle.point.x, particle.point.y);
      CGContextAddLineToPoint(context, particle.point.x - dx * factor,
                              particle.point.y - dy * factor);
      CGContextStrokePath(context);
      CGContextSetLineCap(context, kCGLineCapButt);
    }
    if (particle.localDensity <= 7) {
      FillCircle(context, particle.point, 1.10 * radius, 0.08, 0.52, 0.88,
                 0.72);
      StrokeCircle(context, particle.point, 1.08 * radius, 0.66, 0.94, 1, 0.42,
                   fmax(0.7, 0.16 * radius));
      FillCircle(context,
                 CGPointMake(particle.point.x - 0.30 * radius,
                             particle.point.y + 0.34 * radius),
                 fmax(0.7, 0.23 * radius), 0.90, 0.99, 1, 0.76);
    } else if (particle.surface) {
      /* Dense water still needs trajectory-derived texture.  Short surface
         streaks expose circulation and impact waves without moving or adding
         any simulated sample. */
      if (travel > 0.15 && index % 3 == 0) {
        double limited = fmin(travel, 3.2 * radius + 5.0),
               factor = limited / travel;
        CGContextSetRGBStrokeColor(context, 0.74, 0.97, 1.0, 0.55);
        CGContextSetLineWidth(context, fmax(0.8, 0.28 * radius));
        CGContextSetLineCap(context, kCGLineCapRound);
        CGContextMoveToPoint(context, particle.point.x, particle.point.y);
        CGContextAddLineToPoint(context,
                                particle.point.x - dx * factor,
                                particle.point.y - dy * factor);
        CGContextStrokePath(context);
        CGContextSetLineCap(context, kCGLineCapButt);
      }
      if (index % 4 == 0)
        StrokeCircle(context, particle.point, 0.90 * radius,
                     0.64, 0.93, 1.0, 0.15, fmax(0.55, 0.10 * radius));
      FillCircle(context,
                 CGPointMake(particle.point.x - 0.12 * radius,
                             particle.point.y + 0.22 * radius),
                 fmax(0.55, 0.22 * radius), 0.78, 0.97, 1, 0.48);
    }
  }
}

static LegacyWaterParticle *
BuildLegacyWaterParticles(NSDictionary *frame, NSDictionary *previousFrame,
                     NSArray *materials, PhyVideoCamera camera, double radius,
                     size_t width, size_t height, NSUInteger *outputCount) {
  NSArray *values =
              [frame[@"p"] isKindOfClass:[NSArray class]] ? frame[@"p"] : @[],
          *previous = [previousFrame[@"p"] isKindOfClass:[NSArray class]]
                          ? previousFrame[@"p"]
                          : @[];
  NSUInteger count = values.count;
  *outputCount = count;
  if (count == 0)
    return NULL;
  LegacyWaterParticle *particles = calloc(count, sizeof(LegacyWaterParticle));
  if (!particles)
    return NULL;
  double cellSize = fmax(4, 2.4 * radius);
  int columns = AtLeastOne((int)ceil((double)width / cellSize)),
      rows = AtLeastOne((int)ceil((double)height / cellSize));
  int *occupancy = calloc((size_t)columns * (size_t)rows, sizeof(int));
  double bucketWidth = fmax(3, 1.8 * radius);
  int buckets = AtLeastOne((int)ceil((double)width / bucketWidth));
  double *surfaceHeight = malloc((size_t)buckets * sizeof(double));
  if (!occupancy || !surfaceHeight) {
    free(occupancy);
    free(surfaceHeight);
    free(particles);
    return NULL;
  }
  for (int bucket = 0; bucket < buckets; bucket++)
    surfaceHeight[bucket] = -DBL_MAX;
  for (NSUInteger index = 0; index < count; index++) {
    CameraPoint projected = Project(Vector(values[index]), camera);
    CameraPoint old = Project(index < previous.count ? Vector(previous[index])
                                                     : Vector(values[index]),
                              camera);
    NSString *material = index < materials.count ? materials[index] : @"water";
    particles[index] = (LegacyWaterParticle){CGPointMake(projected.u, projected.v),
                                        CGPointMake(old.u, old.v),
                                        projected.depth,
                                        [material isEqual:@"sand"] ? 1 : 0,
                                        0,
                                        DepthBand(projected.depth, camera),
                                        NO};
    int column = (int)floor(projected.u / cellSize),
        row = (int)floor(projected.v / cellSize);
    if (column >= 0 && column < columns && row >= 0 && row < rows)
      occupancy[row * columns + column]++;
    if (particles[index].material == 0) {
      int bucket = (int)floor(projected.u / bucketWidth);
      if (bucket >= 0 && bucket < buckets)
        surfaceHeight[bucket] = fmax(surfaceHeight[bucket], projected.v);
    }
  }
  for (NSUInteger index = 0; index < count; index++) {
    int column = (int)floor(particles[index].point.x / cellSize),
        row = (int)floor(particles[index].point.y / cellSize), density = 0;
    for (int y = row - 1; y <= row + 1; y++)
      for (int x = column - 1; x <= column + 1; x++)
        if (x >= 0 && x < columns && y >= 0 && y < rows)
          density += occupancy[y * columns + x];
    particles[index].localDensity = density;
    int bucket = (int)floor(particles[index].point.x / bucketWidth);
    if (bucket >= 0 && bucket < buckets)
      particles[index].surface =
          particles[index].point.y >= surfaceHeight[bucket] - 0.80 * radius;
  }
  free(surfaceHeight);
  free(occupancy);
  return particles;
}


/* The legacy depth bands remain only for sand and opaque scene objects. */
static RenderParticle *
BuildRenderParticles(NSDictionary *frame, NSArray *materials,
                     PhyVideoCamera camera, double radius, size_t width,
                     NSUInteger *outputCount) {
  NSArray *values=[frame[@"p"] isKindOfClass:[NSArray class]]?frame[@"p"]:@[];
  NSUInteger count=values.count;
  *outputCount=count;
  if (!count) return NULL;
  RenderParticle *particles=calloc(count,sizeof(RenderParticle));
  if (!particles) return NULL;
  double bucketWidth=fmax(3,1.8*radius);
  int buckets=AtLeastOne((int)ceil((double)width/bucketWidth));
  double *surfaceHeight=malloc((size_t)buckets*sizeof(double));
  if (!surfaceHeight) {free(particles);return NULL;}
  for (int bucket=0;bucket<buckets;bucket++) surfaceHeight[bucket]=-DBL_MAX;
  for (NSUInteger index=0;index<count;index++) {
    CameraPoint projected=Project(Vector(values[index]),camera);
    int material=RenderMaterial(index<materials.count?materials[index]:@"water");
    particles[index]=(RenderParticle){CGPointMake(projected.u,projected.v),
        material,DepthBand(projected.depth,camera),NO};
    if (material==0) {
      int bucket=(int)floor(projected.u/bucketWidth);
      if (bucket>=0 && bucket<buckets)
        surfaceHeight[bucket]=fmax(surfaceHeight[bucket],projected.v);
    }
  }
  for (NSUInteger index=0;index<count;index++) {
    int bucket=(int)floor(particles[index].point.x/bucketWidth);
    if (bucket>=0 && bucket<buckets)
      particles[index].surface=particles[index].point.y>=surfaceHeight[bucket]-.80*radius;
  }
  free(surfaceHeight);
  return particles;
}

static void DrawSandBand(CGContextRef context, RenderParticle *particles,
                         NSUInteger count, int band, double radius) {
  for (NSUInteger index = 0; index < count; index++) {
    RenderParticle p = particles[index];
    if (p.material != 1 || p.depthBand != band)
      continue;
    double light = 0.88 + 0.05 * band;
    FillCircle(context, CGPointMake(p.point.x + 1, p.point.y - 1), radius, 0.03,
               0.025, 0.02, 0.28);
    FillCircle(context, p.point, radius, 0.78 * light, 0.52 * light,
               0.23 * light, 0.96);
    if (p.surface)
      FillCircle(
          context,
          CGPointMake(p.point.x - 0.25 * radius, p.point.y + 0.28 * radius),
          fmax(0.5, 0.18 * radius), 1, 0.84, 0.52, 0.55);
  }
}
static void DrawGravityBodies(CGContextRef context, NSArray *values,
                              PhyVideoCamera camera, int band) {
  for (NSArray *position in values) {
    CameraPoint p = Project(Vector(position), camera);
    if (DepthBand(p.depth, camera) != band)
      continue;
    FillCircle(context, CGPointMake(p.u, p.v), 13, 1, 0.55, 0.08, 0.09);
    FillCircle(context, CGPointMake(p.u, p.v), 7, 1, 0.82, 0.23, 1);
    FillCircle(context, CGPointMake(p.u - 2, p.v + 2), 2.2, 1, 1, 0.83, 0.95);
  }
}

BOOL PhyVideoDrawConnections(CGContextRef context, NSDictionary *frame,
                             NSDictionary *scene, NSArray *bodyIDs,
                             PhyVideoCamera camera) {
  id links = scene[@"connections"];
  if (!links) return YES;
  NSArray *positions = frame[@"g"], *entities = scene[@"entities"];
  if (![links isKindOfClass:[NSArray class]] || [links count] == 0 ||
      [links count] > 256 || ![positions isKindOfClass:[NSArray class]] ||
      ![bodyIDs isKindOfClass:[NSArray class]] || bodyIDs.count != positions.count ||
      ![entities isKindOfClass:[NSArray class]] || entities.count != positions.count ||
      positions.count > ([scene[@"__attachment_overlay"] boolValue] ? 512 : 64))
    return NO;
  NSMutableDictionary *indices = [NSMutableDictionary dictionary];
  NSMutableDictionary *fixed = [NSMutableDictionary dictionary];
  for (NSUInteger index = 0; index < bodyIDs.count; index++) {
    id identifier = bodyIDs[index];
    if (![identifier isKindOfClass:[NSString class]] || indices[identifier] ||
        !MeshVector(positions[index])) return NO;
    indices[identifier] = @(index);
  }
  for (id entity in entities) {
    if (![entity isKindOfClass:[NSDictionary class]] ||
        ![entity[@"id"] isKindOfClass:[NSString class]] ||
        (entity[@"fixed"] && ![entity[@"fixed"] isKindOfClass:[NSNumber class]]) ||
        ![entity[@"type"] isEqual:@"point_mass"] || !indices[entity[@"id"]] ||
        fixed[entity[@"id"]]) return NO;
    fixed[entity[@"id"]] = @([entity[@"fixed"] boolValue]);
  }
  /* Validate the complete overlay before drawing any of it. */
  for (id link in links) {
    if (![link isKindOfClass:[NSDictionary class]]) return NO;
    NSArray *ends = link[@"entities"];
    NSString *kind = link[@"type"];
    id rest = link[@"rest_length"];
    if (![ends isKindOfClass:[NSArray class]] || ends.count != 2 ||
        ![ends[0] isKindOfClass:[NSString class]] ||
        ![ends[1] isKindOfClass:[NSString class]] || [ends[0] isEqual:ends[1]] ||
        !indices[ends[0]] || !indices[ends[1]] ||
        !([kind isEqual:@"spring"] || [kind isEqual:@"rod"] || [kind isEqual:@"rope"]) ||
        ![rest isKindOfClass:[NSNumber class]] || !isfinite([rest doubleValue]) ||
        [rest doubleValue] <= 0 || [rest doubleValue] > 1000)
      return NO;
  }
  CGContextSaveGState(context);
  CGContextSetLineCap(context, kCGLineCapRound);
  CGContextSetLineJoin(context, kCGLineJoinRound);
  for (NSDictionary *link in links) {
    NSArray *ends = link[@"entities"];
    NSArray *a = positions[[indices[ends[0]] unsignedIntegerValue]],
            *b = positions[[indices[ends[1]] unsignedIntegerValue]];
    CameraPoint pa = Project(a, camera), pb = Project(b, camera);
    double dx = pb.u - pa.u, dy = pb.v - pa.v, span = hypot(dx, dy);
    double lengthSquared = 0;
    for (NSUInteger axis = 0; axis < 3; axis++) {
      double delta = VectorValue(b, axis) - VectorValue(a, axis);
      lengthSquared += delta * delta;
    }
    double length = sqrt(lengthSquared), rest = [link[@"rest_length"] doubleValue];
    NSString *kind = link[@"type"];
    CGContextBeginPath(context);
    CGContextMoveToPoint(context, pa.u, pa.v);
    if ([kind isEqual:@"spring"] && span > 4) {
      /* A screen-space helix is illustrative; both leads end at the recorded
       * positions. Coil count is stable for a link, independent of extension. */
      int turns = (int)Clamp(round(6 + 2 * sqrt(rest)), 6, 16);
      double ux = dx / span, uy = dy / span;
      double amplitude = fmin(8.0, 0.065 * span);
      CGContextAddLineToPoint(context, pa.u + 0.12 * dx, pa.v + 0.12 * dy);
      for (int sample = 1; sample <= turns * 20; sample++) {
        double s = (double)sample / (turns * 20), phase = s * turns * 2 * M_PI;
        double envelope = fmin(1.0, fmin(8 * s, 8 * (1 - s)));
        double across = amplitude * sin(phase) * envelope;
        double along = 0.28 * amplitude * (cos(phase) - 1) * envelope;
        double t = 0.12 + 0.76 * s;
        CGContextAddLineToPoint(context, pa.u + t * dx + along * ux - across * uy,
                                 pa.v + t * dy + along * uy + across * ux);
      }
      CGContextAddLineToPoint(context, pb.u, pb.v);
      CGContextSetRGBStrokeColor(context, 0.27, 0.85, 0.96, 0.98);
      CGContextSetLineWidth(context, 2.1);
    } else if ([kind isEqual:@"rope"] && length < rest) {
      /* Sag conveys slack only: this is not a resolved cable/catenary model.
       * The length used to choose sag is three-dimensional, never projected. */
      double sag = fmin(24.0, 0.3 * sqrt(fmax(0, rest * rest - lengthSquared)) * camera.scale);
      CGContextAddQuadCurveToPoint(context, 0.5 * (pa.u + pb.u),
                                   0.5 * (pa.v + pb.v) - sag, pb.u, pb.v);
      CGContextSetRGBStrokeColor(context, 1.0, 0.69, 0.30, 0.98);
      CGContextSetLineWidth(context, 2.5);
    } else {
      CGContextAddLineToPoint(context, pb.u, pb.v);
      if ([kind isEqual:@"rod"])
        CGContextSetRGBStrokeColor(context, 0.77, 0.83, 0.93, 1.0);
      else if ([kind isEqual:@"rope"])
        CGContextSetRGBStrokeColor(context, 1.0, 0.69, 0.30, 0.98);
      else
        CGContextSetRGBStrokeColor(context, 0.27, 0.85, 0.96, 0.98);
      CGContextSetLineWidth(context, [kind isEqual:@"rod"] ? 3.5 : 2.1);
    }
    CGContextStrokePath(context);
  }
  /* Repaint endpoints above the connections. Fixed points use a square mount;
   * mobile points retain the established round mass glyph. */
  for (NSUInteger index = 0; ![scene[@"__attachment_overlay"] boolValue] && index < positions.count; index++) {
    CameraPoint point = Project(positions[index], camera);
    if ([fixed[bodyIDs[index]] boolValue]) {
      CGRect mount = CGRectMake(point.u - 8, point.v - 8, 16, 16);
      CGContextSetRGBFillColor(context, 0.17, 0.23, 0.32, 1);
      CGContextFillRect(context, mount);
      CGContextSetRGBStrokeColor(context, 0.80, 0.89, 0.99, 1);
      CGContextSetLineWidth(context, 1.6);
      CGContextStrokeRect(context, mount);
      FillCircle(context, CGPointMake(point.u, point.v), 2.4, 0.88, 0.95, 1, 1);
    } else {
      FillCircle(context, CGPointMake(point.u, point.v), 7, 1, 0.82, 0.23, 1);
      FillCircle(context, CGPointMake(point.u - 2, point.v + 2), 2.2, 1, 1, 0.83, 0.95);
    }
  }
  CGContextRestoreGState(context);
  return YES;
}

static void DrawRigids(CGContextRef context, NSArray *values, NSArray *shapes,
                       PhyVideoCamera camera, int band) {
  for (NSUInteger index = 0; index < values.count; index++) {
    CameraPoint p = Project(Vector(values[index]), camera);
    if (DepthBand(p.depth, camera) != band)
      continue;
    NSDictionary *shape = index < shapes.count ? shapes[index] : @{};
    if ([shape[@"type"] isEqual:@"box"])
      DrawShadedBox(context, values[index], shape[@"size"], camera, NO);
    else
      DrawShadedSphere(context, CGPointMake(p.u, p.v),
                       fmax(0.02, [shape[@"radius"] doubleValue]) *
                           camera.scale);
  }
}

/* Analytic physical shapes become temporary display triangles only. Their
 * vertices are transformed by the recorded body-to-world quaternion. Colored
 * cap sectors and a unique meridian make a cylinder/sphere's spin observable. */
static BOOL PositiveDimension(id value) {
  return [value isKindOfClass:[NSNumber class]] && isfinite([value doubleValue]) &&
         [value doubleValue]>0 && [value doubleValue]<=100;
}
static void AppendDisplayMesh(NSMutableArray *vertices,NSMutableArray *objects,
                              NSArray *points,NSArray *triangles,NSArray *colors,
                              NSArray *color) {
  [objects addObject:@{@"vertex_start":@(vertices.count),@"vertex_count":@(points.count),
      @"triangles":triangles,@"triangle_colors":colors,@"color":color,@"smooth_edges":@YES}];
  [vertices addObjectsFromArray:points];
}
static void DisplayTriangle(NSMutableArray *triangles,NSMutableArray *colors,
                            NSUInteger a,NSUInteger b,NSUInteger c,NSArray *color) {
  [triangles addObject:@[@(a),@(b),@(c)]];[colors addObject:color];
}
static BOOL AppendRigidGeometry(NSMutableArray *vertices,NSMutableArray *objects,
                                NSArray *positions,NSArray *poses,NSArray *shapes) {
  if (![positions isKindOfClass:[NSArray class]] || ![poses isKindOfClass:[NSArray class]] ||
      ![shapes isKindOfClass:[NSArray class]] || positions.count!=poses.count ||
      positions.count!=shapes.count || positions.count>64) return NO;
  NSArray *cyan=@[@0.20,@0.74,@0.88],*gold=@[@0.96,@0.69,@0.24],
          *white=@[@0.91,@0.95,@0.98],*red=@[@0.95,@0.31,@0.21];
  for (NSUInteger object=0;object<positions.count;object++) {
    if (!MeshVector(positions[object]) || !UnitQuaternion(poses[object]) ||
        ![shapes[object] isKindOfClass:[NSDictionary class]]) return NO;
    NSDictionary *shape=shapes[object];NSString *kind=shape[@"type"];
    NSMutableArray *local=[NSMutableArray array],*triangles=[NSMutableArray array],
                   *colors=[NSMutableArray array];
    if ([kind isEqual:@"box"]) {
      NSArray *size=shape[@"size"];
      if (!MeshVector(size) || !PositiveDimension(size[0]) || !PositiveDimension(size[1]) ||
          !PositiveDimension(size[2])) return NO;
      for (int x=-1;x<=1;x+=2) for (int y=-1;y<=1;y+=2) for (int z=-1;z<=1;z+=2)
        [local addObject:@[@(0.5*x*VectorValue(size,0)),@(0.5*y*VectorValue(size,1)),@(0.5*z*VectorValue(size,2))]];
      const NSUInteger faces[6][4]={{0,1,3,2},{4,6,7,5},{0,4,5,1},{2,3,7,6},{0,2,6,4},{1,5,7,3}};
      NSArray *palette=@[cyan,red,gold,white,cyan,gold];
      for (NSUInteger face=0;face<6;face++) {
        DisplayTriangle(triangles,colors,faces[face][0],faces[face][1],faces[face][2],palette[face]);
        DisplayTriangle(triangles,colors,faces[face][0],faces[face][2],faces[face][3],palette[face]);
      }
    } else if ([kind isEqual:@"cylinder"]) {
      if (!PositiveDimension(shape[@"radius"]) || !PositiveDimension(shape[@"height"])) return NO;
      const NSUInteger sides=32;double radius=[shape[@"radius"] doubleValue],half=.5*[shape[@"height"] doubleValue];
      for (NSUInteger side=0;side<2;side++) for (NSUInteger j=0;j<sides;j++) {
        double angle=2*M_PI*(double)j/(double)sides;
        [local addObject:@[@(radius*cos(angle)),@(side?half:-half),@(radius*sin(angle))]];
      }
      [local addObject:@[@0,@(-half),@0]];[local addObject:@[@0,@(half),@0]];
      for (NSUInteger j=0;j<sides;j++) {
        NSUInteger next=(j+1)%sides;
        NSArray *sideColor=j<2?red:(j%8<2?gold:cyan);
        DisplayTriangle(triangles,colors,j,next,sides+j,sideColor);
        DisplayTriangle(triangles,colors,next,sides+next,sides+j,sideColor);
        NSArray *capColor=j<2?red:(j%8<2?white:gold);
        DisplayTriangle(triangles,colors,2*sides,j,next,j%8<2?white:cyan);
        DisplayTriangle(triangles,colors,2*sides+1,sides+next,sides+j,capColor);
      }
    } else if ([kind isEqual:@"sphere"]) {
      if (!PositiveDimension(shape[@"radius"])) return NO;
      const NSUInteger rings=16,sides=32;double radius=[shape[@"radius"] doubleValue];
      for (NSUInteger ring=0;ring<=rings;ring++) for (NSUInteger j=0;j<sides;j++) {
        double latitude=M_PI*(double)ring/(double)rings,longitude=2*M_PI*(double)j/(double)sides;
        [local addObject:@[@(radius*sin(latitude)*cos(longitude)),@(radius*cos(latitude)),
                          @(radius*sin(latitude)*sin(longitude))]];
      }
      for (NSUInteger ring=0;ring<rings;ring++) for (NSUInteger j=0;j<sides;j++) {
        NSUInteger next=(j+1)%sides,a=ring*sides+j,b=ring*sides+next,c=(ring+1)*sides+j,d=(ring+1)*sides+next;
        NSArray *color=j<2?red:(ring==7||ring==8?gold:cyan);
        DisplayTriangle(triangles,colors,a,b,c,color);DisplayTriangle(triangles,colors,b,d,c,color);
      }
    } else return NO;
    NSMutableArray *world=[NSMutableArray arrayWithCapacity:local.count];
    for (NSArray *point in local) [world addObject:RotateBodyPoint(poses[object],point,positions[object])];
    AppendDisplayMesh(vertices,objects,world,triangles,colors,cyan);
  }
  return YES;
}
static BOOL AppendCapsuleGeometry(NSMutableArray *vertices,NSMutableArray *objects,
                                  NSArray *a,NSArray *b,double radius,NSArray *color) {
  if (!MeshVector(a) || !MeshVector(b) || !isfinite(radius) || radius<=0 || radius>100) return NO;
  double axis[3],u[3],v[3],center[3],length=0;
  for (NSUInteger k=0;k<3;k++) {axis[k]=VectorValue(b,k)-VectorValue(a,k);length+=axis[k]*axis[k];center[k]=.5*(VectorValue(a,k)+VectorValue(b,k));}
  length=sqrt(length);
  if (length<1e-12) {axis[0]=0;axis[1]=1;axis[2]=0;} else for (int k=0;k<3;k++) axis[k]/=length;
  /* Cross with the coordinate direction least parallel to the capsule. */
  double reference[3]={0,0,0};int least=fabs(axis[0])<fabs(axis[1])?0:1;
  if (fabs(axis[2])<fabs(axis[least])) least=2;reference[least]=1;
  u[0]=axis[1]*reference[2]-axis[2]*reference[1];u[1]=axis[2]*reference[0]-axis[0]*reference[2];u[2]=axis[0]*reference[1]-axis[1]*reference[0];
  double un=sqrt(u[0]*u[0]+u[1]*u[1]+u[2]*u[2]);for (int k=0;k<3;k++) u[k]/=un;
  v[0]=axis[1]*u[2]-axis[2]*u[1];v[1]=axis[2]*u[0]-axis[0]*u[2];v[2]=axis[0]*u[1]-axis[1]*u[0];
  const NSUInteger sides=20,halfRings=6,rings=2*(halfRings+1);
  NSMutableArray *points=[NSMutableArray array],*triangles=[NSMutableArray array],*colors=[NSMutableArray array];
  for (NSUInteger ring=0;ring<rings;ring++) {
    BOOL top=ring>halfRings;double latitude=top?(.5*M_PI*(double)(ring-halfRings-1)/(double)halfRings):
                                               (-.5*M_PI+.5*M_PI*(double)ring/(double)halfRings);
    double along=(top?.5:-.5)*length+radius*sin(latitude),radial=radius*cos(latitude);
    for (NSUInteger j=0;j<sides;j++) {
      double angle=2*M_PI*(double)j/(double)sides,p[3];
      for (int k=0;k<3;k++) p[k]=center[k]+along*axis[k]+radial*(cos(angle)*u[k]+sin(angle)*v[k]);
      [points addObject:@[@(p[0]),@(p[1]),@(p[2])]];
    }
  }
  for (NSUInteger ring=0;ring<rings-1;ring++) for (NSUInteger j=0;j<sides;j++) {
    NSUInteger next=(j+1)%sides,a0=ring*sides+j,b0=ring*sides+next,c=(ring+1)*sides+j,d=(ring+1)*sides+next;
    DisplayTriangle(triangles,colors,a0,b0,c,color);DisplayTriangle(triangles,colors,b0,d,c,color);
  }
  AppendDisplayMesh(vertices,objects,points,triangles,colors,color);return YES;
}

typedef struct {
  CameraPoint center, recordedCenter;
  double radius;
  int material,next;
  NSUInteger originalIndex;
  BOOL sparse;
} LiquidSample;

static int CompareLiquidSamples(const void *left,const void *right) {
  const LiquidSample *a=left,*b=right;
  if (a->material!=b->material) return a->material<b->material?-1:1;
  const double ka[3]={a->center.u,a->center.v,a->center.depth},
               kb[3]={b->center.u,b->center.v,b->center.depth};
  for (int axis=0;axis<3;axis++) {
    if (ka[axis]<kb[axis]) return -1;
    if (ka[axis]>kb[axis]) return 1;
  }
  return 0;
}

/* Bounded screen bins find nearby samples in physical 3-D distance. Expanded
 * kernels join resolved sheets after impact, while isolated spray retains its
 * original footprint. Dense bins need no expansion and skip the neighbor walk;
 * its result is independent of insertion order and avoids quadratic work. */
static LiquidSample *BuildLiquidSamples(NSArray *samples,NSArray *materials,
    PhyVideoCamera camera,double particleRadius,size_t width,size_t height) {
  if (samples.count>INT_MAX) return NULL;
  double cellSize=fmax(2.3,8*particleRadius*camera.scale);
  int columns=(int)ceil((double)width/cellSize)+2,
      rows=(int)ceil((double)height/cellSize)+2;
  size_t binCount=(size_t)columns*(size_t)rows*kMaterialCount;
  int *heads=malloc(binCount*sizeof(int)),*counts=calloc(binCount,sizeof(int));
  LiquidSample *points=calloc(samples.count,sizeof(LiquidSample));
  CameraPoint *smoothed=calloc(samples.count,sizeof(CameraPoint));
  if (!heads || !counts || !points || !smoothed) {
    free(heads);free(counts);free(points);free(smoothed);return NULL;
  }
  for (size_t i=0;i<binCount;i++) heads[i]=-1;
  double baseRadius=fmax(2.3,2.2*particleRadius*camera.scale);
  for (NSUInteger i=0;i<samples.count;i++) {
    CameraPoint center=Project(samples[i],camera);
    if (!isfinite(center.u) || !isfinite(center.v) || !isfinite(center.depth)) {
      free(heads);free(counts);free(points);free(smoothed);return NULL;
    }
    int material=RenderMaterial(i<materials.count?materials[i]:@"water");
    points[i]=(LiquidSample){center,center,baseRadius,material,-1,i,NO};
  }
  /* Canonicalize before any neighbourhood accumulation.  Rendering remains
   * independent of the trajectory's sample order even though floating-point
   * sums are used for reconstruction-only kernel-centre smoothing. */
  qsort(points,samples.count,sizeof(LiquidSample),CompareLiquidSamples);
  for (NSUInteger i=0;i<samples.count;i++) {
    CameraPoint center=points[i].center;
    if (center.u< -cellSize || center.u>=(double)width+cellSize ||
        center.v< -cellSize || center.v>=(double)height+cellSize) continue;
    int x=(int)floor(center.u/cellSize)+1,y=(int)floor(center.v/cellSize)+1;
    if (x<0 || x>=columns || y<0 || y>=rows) continue;
    size_t bin=((size_t)points[i].material*(size_t)rows+(size_t)y)*(size_t)columns+(size_t)x;
    points[i].next=heads[bin];heads[bin]=(int)i;counts[bin]++;
  }
  double maximumDistance=8*particleRadius*camera.scale;
  double maximumDistanceSquared=maximumDistance*maximumDistance;
  for (NSUInteger i=0;i<samples.count;i++) {
    LiquidSample *point=&points[i];
    smoothed[i]=point->center;
    if (point->material==1 || point->center.u< -cellSize || point->center.u>=(double)width+cellSize ||
        point->center.v< -cellSize || point->center.v>=(double)height+cellSize) continue;
    int x=(int)floor(point->center.u/cellSize)+1,y=(int)floor(point->center.v/cellSize)+1;
    int candidates=0;
    for (int yy=MAX(0,y-1);yy<=MIN(rows-1,y+1);yy++)
      for (int xx=MAX(0,x-1);xx<=MIN(columns-1,x+1);xx++)
        candidates+=counts[((size_t)point->material*(size_t)rows+(size_t)yy)*(size_t)columns+(size_t)xx];
    if (candidates>512) continue;
    double nearest[3]={DBL_MAX,DBL_MAX,DBL_MAX};
    double weightTotal=1.0,meanU=point->center.u,meanV=point->center.v,
           meanDepth=point->center.depth;
    int neighborCount=0;
    for (int yy=MAX(0,y-1);yy<=MIN(rows-1,y+1);yy++)
      for (int xx=MAX(0,x-1);xx<=MIN(columns-1,x+1);xx++) {
        size_t bin=((size_t)point->material*(size_t)rows+(size_t)yy)*(size_t)columns+(size_t)xx;
        for (int j=heads[bin];j>=0;j=points[j].next) {
          if ((NSUInteger)j==i) continue;
          double dx=points[j].center.u-point->center.u,dy=points[j].center.v-point->center.v,
                 dz=(points[j].center.depth-point->center.depth)*camera.scale;
          double distance=dx*dx+dy*dy+dz*dz;
          if (distance>maximumDistanceSquared) continue;
          double q=1.0-distance/fmax(maximumDistanceSquared,1.0e-12),weight=q*q*q;
          weightTotal+=weight;
          meanU+=weight*points[j].center.u;meanV+=weight*points[j].center.v;
          meanDepth+=weight*points[j].center.depth;neighborCount++;
          if (distance<nearest[2]) {
            nearest[2]=distance;
            if (nearest[2]<nearest[1]) {double swap=nearest[1];nearest[1]=nearest[2];nearest[2]=swap;}
            if (nearest[1]<nearest[0]) {double swap=nearest[0];nearest[0]=nearest[1];nearest[1]=swap;}
          }
        }
    }
    if (nearest[2]<DBL_MAX)
      point->radius=fmax(baseRadius,fmin(5.5*particleRadius*camera.scale,1.35*sqrt(nearest[2])));
    point->sparse=neighborCount<=7;
    if (neighborCount>=4 && weightTotal>1.0) {
      double du=.55*(meanU/weightTotal-point->center.u),
             dv=.55*(meanV/weightTotal-point->center.v),
             dd=.55*(meanDepth/weightTotal-point->center.depth);
      double planar=hypot(du,dv),maximumPlanarShift=.45*baseRadius;
      if (planar>maximumPlanarShift) {
        du*=maximumPlanarShift/planar;dv*=maximumPlanarShift/planar;
      }
      dd=Clamp(dd,-.45*particleRadius,.45*particleRadius);
      smoothed[i]=(CameraPoint){point->center.u+du,point->center.v+dv,point->center.depth+dd};
    }
  }
  for (NSUInteger i=0;i<samples.count;i++) points[i].center=smoothed[i];
  free(heads);free(counts);
  free(smoothed);
  return points;
}

static void BlendSparseWaterPixel(uint8_t *pixels,size_t pixel,
    double red,double green,double blue,double alpha) {
  alpha=Clamp(alpha,0,1);
  double oldAlpha=pixels[4*pixel+3]/255.0;
  const double color[3]={red,green,blue};
  for (size_t channel=0;channel<3;channel++)
    pixels[4*pixel+channel]=(uint8_t)(255*Clamp(
        color[channel]*alpha+(pixels[4*pixel+channel]/255.0)*(1-alpha),0,1));
  pixels[4*pixel+3]=(uint8_t)(255*Clamp(alpha+oldAlpha*(1-alpha),0,1));
}

/* Sparse recorded samples keep the small, separate blue beads and bounded
 * motion traces of v2. Rasterizing into the mesh's depth buffer lets the cone
 * hide droplets behind it; no sample positions or velocities are changed. */
static void DrawSparseWaterDroplets(uint8_t *pixels,float *sceneDepth,
    LiquidSample *points,NSUInteger count,NSArray *previousPositions,
    PhyVideoCamera camera,size_t width,size_t height,double particleRadius) {
  double radius=Clamp(1.08*particleRadius*camera.scale,1.65,8.5);
  if (![previousPositions isKindOfClass:[NSArray class]]) previousPositions=@[];
  for (NSUInteger i=0;i<count;i++) {
    LiquidSample point=points[i];
    if (point.material!=0 || !point.sparse) continue;
    CameraPoint center=point.recordedCenter,previous=center;
    if (point.originalIndex<previousPositions.count &&
        MeshVector(previousPositions[point.originalIndex]))
      previous=Project(previousPositions[point.originalIndex],camera);
    double dx=center.u-previous.u,dy=center.v-previous.v,travel=hypot(dx,dy);
    if (travel>0.8) {
      double limited=fmin(travel,2.8*radius+7),factor=limited/travel;
      double ax=center.u-dx*factor,ay=center.v-dy*factor;
      double halfWidth=fmax(1,0.62*radius)*.5;
      int left=(int)Clamp(floor(fmin(ax,center.u)-halfWidth-1),0,(double)width-1),
          right=(int)Clamp(ceil(fmax(ax,center.u)+halfWidth+1),0,(double)width-1),
          bottom=(int)Clamp(floor(fmin(ay,center.v)-halfWidth-1),0,(double)height-1),
          top=(int)Clamp(ceil(fmax(ay,center.v)+halfWidth+1),0,(double)height-1);
      double lengthSquared=(center.u-ax)*(center.u-ax)+(center.v-ay)*(center.v-ay);
      for (int y=bottom;y<=top;y++) for (int x=left;x<=right;x++) {
        double u=lengthSquared>0?Clamp((((double)x+.5-ax)*(center.u-ax)+
            ((double)y+.5-ay)*(center.v-ay))/lengthSquared,0,1):1;
        double distance=hypot((double)x+.5-(ax+u*(center.u-ax)),
                              (double)y+.5-(ay+u*(center.v-ay)));
        double coverage=Clamp(halfWidth+.5-distance,0,1);
        if (coverage<=0) continue;
        double z=previous.depth+(center.depth-previous.depth)*(1-factor+u*factor);
        size_t pixel=((height-1-(size_t)y)*width+(size_t)x);
        if (z<sceneDepth[pixel]) continue;
        BlendSparseWaterPixel(pixels,pixel,.18,.72,1,.23*coverage);
      }
    }
    double bodyRadius=1.10*radius;
    int left=(int)Clamp(floor(center.u-bodyRadius-1),0,(double)width-1),
        right=(int)Clamp(ceil(center.u+bodyRadius+1),0,(double)width-1),
        bottom=(int)Clamp(floor(center.v-bodyRadius-1),0,(double)height-1),
        top=(int)Clamp(ceil(center.v+bodyRadius+1),0,(double)height-1);
    for (int y=bottom;y<=top;y++) for (int x=left;x<=right;x++) {
      double ux=(double)x+.5-center.u,uy=(double)y+.5-center.v;
      double distance=hypot(ux,uy);
      double coverage=Clamp(bodyRadius+.5-distance,0,1);
      if (coverage<=0) continue;
      double z=center.depth+particleRadius*sqrt(fmax(0,1-(distance/bodyRadius)*(distance/bodyRadius)));
      size_t pixel=((height-1-(size_t)y)*width+(size_t)x);
      if (z<sceneDepth[pixel]) continue;
      BlendSparseWaterPixel(pixels,pixel,.08,.52,.88,.72*coverage);
      double rimWidth=fmax(.7,.16*radius),rim=Clamp(.5*rimWidth+.5-
          fabs(distance-1.08*radius),0,1);
      if (rim>0) BlendSparseWaterPixel(pixels,pixel,.66,.94,1,.42*rim);
      double highlight=hypot(ux+.30*radius,uy-.34*radius);
      double highlightRadius=fmax(.7,.23*radius);
      double glint=Clamp(highlightRadius+.5-highlight,0,1);
      if (glint>0) BlendSparseWaterPixel(pixels,pixel,.90,.99,1,.76*glint);
      sceneDepth[pixel]=(float)z;
    }
  }
}

/* A compact screen-space field joins nearby recorded liquid samples. The
 * front-depth gate prevents distant overlapping layers from adding density.
 * Fixed, depth-aware spatial passes remove individual sphere highlights; no
 * temporal accumulation, invented spray, or trajectory writes are involved. */
static BOOL DrawLiquidSurface(uint8_t *pixels, float *sceneDepth,
    NSDictionary *frame, NSArray *materials, PhyVideoCamera camera,
    size_t width, size_t height, double particleRadius) {
  NSArray *samples=frame[@"p"] ?: @[];
  if (![samples isKindOfClass:[NSArray class]]) return NO;
  if (!samples.count) return YES;
  BOOL present[kMaterialCount]={NO},hasLiquid=NO;
  for (NSUInteger i=0;i<samples.count;i++) {
    if (!MeshVector(samples[i])) return NO;
    int material=RenderMaterial(i<materials.count?materials[i]:@"water");
    if (material!=1) {present[material]=YES;hasLiquid=YES;}
  }
  if (!hasLiquid) return YES;
  if (!isfinite(particleRadius) || particleRadius<=0 ||
      !isfinite(camera.scale) || camera.scale<=0) return NO;
  LiquidSample *points=BuildLiquidSamples(samples,materials,camera,particleRadius,width,height);
  if (!points) return NO;
  BOOL separateSparseWater=[frame[@"__separate_sparse_water"] boolValue];
  size_t count=width*height;
  float *field=calloc(count,sizeof(float)),*front=malloc(count*sizeof(float)),
        *smooth=malloc(count*sizeof(float));
  if (!field || !front || !smooth) {free(field);free(front);free(smooth);free(points);return NO;}
  double radius=fmax(2.3,2.20*particleRadius*camera.scale);
  double depthRange=fmax(5.0*particleRadius,1.0e-9);
  int filterRadius=(int)Clamp(ceil(4.0*particleRadius*camera.scale),2,24);
  /* Subpixel splashes keep a lower isovalue so contour filtering cannot
   * erase their small peaks; resolved dense surfaces use the rounder contour. */
  double contourResolution=Clamp((radius-3.0)/3.0,0,1);
  double coverageThreshold=.12+.08*contourResolution;
  double coverageFeather=.10+.04*contourResolution;
  for (int material=0;material<kMaterialCount;material++) {
    if (!present[material]) continue;
    for (size_t i=0;i<count;i++) {field[i]=0;front[i]=-FLT_MAX;}
    int minimumX=(int)width,minimumY=(int)height,maximumX=-1,maximumY=-1;
    /* The first traversal finds the visible front envelope. The second adds
     * only nearby samples to an order-independent, bounded-support field. */
    for (int pass=0;pass<2;pass++) for (NSUInteger i=0;i<samples.count;i++) {
      if (points[i].material!=material ||
          (separateSparseWater && material==0 && points[i].sparse)) continue;
      CameraPoint center=points[i].center;
      double supportRadius=points[i].radius;
      int left=(int)Clamp(floor(center.u-supportRadius),0,(double)width-1),
          right=(int)Clamp(ceil(center.u+supportRadius),0,(double)width-1),
          bottom=(int)Clamp(floor(center.v-supportRadius),0,(double)height-1),
          top=(int)Clamp(ceil(center.v+supportRadius),0,(double)height-1);
      for (int y=bottom;y<=top;y++) for (int x=left;x<=right;x++) {
        double dx=((double)x+.5-center.u)/supportRadius,dy=((double)y+.5-center.v)/supportRadius;
        double support=1-dx*dx-dy*dy;
        if (support<=0) continue;
        double z=center.depth+particleRadius*sqrt(support);
        size_t pixel=(height-1-(size_t)y)*width+(size_t)x;
        if (z<sceneDepth[pixel]) continue;
        if (!pass) {
          front[pixel]=fmaxf(front[pixel],(float)z);
          minimumX=MIN(minimumX,x);maximumX=MAX(maximumX,x);
          minimumY=MIN(minimumY,y);maximumY=MAX(maximumY,y);
        } else if (z>=front[pixel]-4.0*particleRadius) {
          field[pixel]+=(float)(support*support);
        }
      }
    }
    if (maximumX<minimumX || maximumY<minimumY) continue;
    /* Smooth the scalar coverage as well as depth: otherwise the union of
     * sample footprints still shows a beaded silhouette. Two compact x/y
     * iterations round dense contours. Unsupported pixels stay empty, while
     * depth discontinuities are not mixed; isolated sample peaks survive. */
    int contourRadius=(int)Clamp(ceil(.85*radius),2,8);
    float *coverageSource=field,*coverageTarget=smooth;
    for (int pass=0;pass<4;pass++) {
      for (int y=minimumY;y<=maximumY;y++) for (int x=minimumX;x<=maximumX;x++) {
        size_t pixel=(height-1-(size_t)y)*width+(size_t)x;
        coverageTarget[pixel]=0;
        if (front[pixel]==-FLT_MAX) continue;
        double total=0,weights=0;
        for (int offset=-contourRadius;offset<=contourRadius;offset++) {
          double weight=contourRadius+1-abs(offset);
          weights+=weight;
          int xx=x+((pass&1)?0:offset),yy=y+((pass&1)?offset:0);
          if (xx<minimumX || xx>maximumX || yy<minimumY || yy>maximumY) continue;
          size_t other=(height-1-(size_t)yy)*width+(size_t)xx;
          if (front[other]==-FLT_MAX || fabs(front[other]-front[pixel])>6*depthRange) continue;
          total+=weight*coverageSource[other];
        }
        coverageTarget[pixel]=(float)(total/weights);
      }
      float *swap=coverageSource;coverageSource=coverageTarget;coverageTarget=swap;
    }
    float *source=front,*target=smooth;
    /* Three separable, footprint-scaled iterations suppress the sampling grid.
     * Invalid pixels and depth breaks do not participate, so filtering cannot
     * bridge gaps or solid silhouettes. The bounded radius limits work. */
    for (int pass=0;pass<6;pass++) {
      for (int y=minimumY;y<=maximumY;y++) for (int x=minimumX;x<=maximumX;x++) {
        size_t pixel=(height-1-(size_t)y)*width+(size_t)x;
        target[pixel]=source[pixel];
        if (field[pixel]<=coverageThreshold) continue;
        double total=0,weights=0;
        for (int offset=-filterRadius;offset<=filterRadius;offset++) {
          int xx=x+((pass&1)?0:offset),yy=y+((pass&1)?offset:0);
          if (xx<minimumX || xx>maximumX || yy<minimumY || yy>maximumY) continue;
          size_t other=(height-1-(size_t)yy)*width+(size_t)xx;
          if (field[other]<=coverageThreshold) continue;
          double dz=(source[other]-source[pixel])/depthRange;
          if (fabs(dz)>2) continue;
          double weight=(filterRadius+1-abs(offset))/(1+dz*dz);
          total+=weight*source[other];weights+=weight;
        }
        if (weights>0) target[pixel]=(float)(total/weights);
      }
      float *swap=source;source=target;target=swap;
    }
    CGFloat colors[8];LiquidPalette(material,colors);
    for (int y=minimumY;y<=maximumY;y++) for (int x=minimumX;x<=maximumX;x++) {
      size_t pixel=(height-1-(size_t)y)*width+(size_t)x;
      double coverage=Clamp((field[pixel]-coverageThreshold)/coverageFeather,0,1);
      if (coverage<=0 || source[pixel]<sceneDepth[pixel]) continue;
      double z=source[pixel],neighbors[4]={z,z,z,z};
      const int offsets[4][2]={{-1,0},{1,0},{0,-1},{0,1}};
      for (int k=0;k<4;k++) {
        int xx=x+offsets[k][0],yy=y+offsets[k][1];
        if (xx<minimumX || xx>maximumX || yy<minimumY || yy>maximumY) continue;
        size_t other=(height-1-(size_t)yy)*width+(size_t)xx;
        if (field[other]>coverageThreshold && fabs(source[other]-z)<2*depthRange) neighbors[k]=source[other];
      }
      double nx=(neighbors[0]-neighbors[1])*.5*camera.scale,
             ny=(neighbors[2]-neighbors[3])*.5*camera.scale,nz=1;
      double length=sqrt(nx*nx+ny*ny+1);nx/=length;ny/=length;nz/=length;
      double light=fmax(0,-.40*nx+.70*ny+.59*nz);
      double gloss=pow(fmax(0,-.24*nx+.42*ny+.875*nz),material==3?10:28);
      /* A broad tint gradient avoids reintroducing per-sample density rings. */
      double thickness=Clamp((double)(maximumY-y)/fmax(1,maximumY-minimumY),0,1);
      /* Water is transparent in bulk and more reflective at grazing angles.
       * This is a deterministic display cue, not a refraction solver: the
       * reconstructed depth and every trajectory sample remain unchanged. */
      double alpha=coverage*(material==0?
          (.58+.24*(1-nz)+.08*thickness):(material==2?.97:1.0));
      double oldAlpha=pixels[4*pixel+3]/255.0;
      for (int channel=0;channel<3;channel++) {
        double base=colors[4+channel]*(1-.36*thickness)+colors[channel]*.36*thickness;
        double value=base*(.40+.60*light)+gloss*(material==0?.46:(material==4?.52:.24));
        if (material==4) value=base*(.30+.58*Clamp(.5+.5*ny,0,1))+.46*gloss;
        if (material==5) value=base*(.78+.22*light)+.08*gloss; /* Display glow, not heat. */
        pixels[4*pixel+(size_t)channel]=(uint8_t)(255*Clamp(
            Clamp(value,0,1)*alpha+(pixels[4*pixel+(size_t)channel]/255.0)*(1-alpha),0,1));
      }
      pixels[4*pixel+3]=(uint8_t)(255*Clamp(alpha+oldAlpha*(1-alpha),0,1));
      sceneDepth[pixel]=(float)z;
    }
  }
  if (separateSparseWater)
    DrawSparseWaterDroplets(pixels,sceneDepth,points,samples.count,
        frame[@"__previous_p"],camera,width,height,particleRadius);
  free(field);free(front);free(smooth);free(points);
  return YES;
}

static BOOL DrawRasterSurface(CGContextRef context,uint8_t *pixels,
    size_t width,size_t height) {
  CGColorSpaceRef space=CGColorSpaceCreateDeviceRGB();
  CGContextRef surface=CGBitmapContextCreate(pixels,width,height,8,width*4,space,
      (CGBitmapInfo)kCGBitmapByteOrder32Big|(CGBitmapInfo)kCGImageAlphaPremultipliedLast);
  CGColorSpaceRelease(space);
  CGImageRef image=surface?CGBitmapContextCreateImage(surface):NULL;
  if (image) CGContextDrawImage(context,CGRectMake(0,0,(CGFloat)width,(CGFloat)height),image);
  BOOL rendered=image!=NULL;
  if (image) CGImageRelease(image);
  if (surface) CGContextRelease(surface);
  return rendered;
}

/* Ordinary particle scenes reuse the exact liquid shader. Their existing
 * collider/sand depth-band overlays are retained after this transparent layer. */
static BOOL DrawLiquidLayer(CGContextRef context,NSDictionary *frame,
    NSArray *materials,PhyVideoCamera camera,size_t width,size_t height,
    double particleRadius) {
  NSArray *samples=frame[@"p"] ?: @[];
  if (![samples isKindOfClass:[NSArray class]]) return NO;
  if (!samples.count) return YES;
  size_t count=width*height;
  uint8_t *pixels=calloc(count,4);
  float *depth=malloc(count*sizeof(float));
  if (!pixels || !depth) {free(pixels);free(depth);return NO;}
  for (size_t i=0;i<count;i++) depth[i]=-FLT_MAX;
  BOOL rendered=DrawLiquidSurface(pixels,depth,frame,materials,camera,width,height,particleRadius)
                && DrawRasterSurface(context,pixels,width,height);
  free(pixels);free(depth);
  return rendered;
}

/* A shared depth buffer resolves every triangle across every mesh. Sorting
 * triangle centres is insufficient for a tool passing through a ring: long
 * intersecting faces can have different front-to-back order at each pixel.
 * Only the recorded vertices are projected; shading never changes geometry. */
static BOOL DrawMeshes(CGContextRef context, NSArray *vertices,
                       NSArray *objects, PhyVideoCamera camera,
                       size_t width, size_t height, NSDictionary *mixedFrame,
                       NSArray *materials, double particleRadius) {
  if (![objects isKindOfClass:[NSArray class]] ||
      ![vertices isKindOfClass:[NSArray class]])
    return NO;
  if (objects.count == 0 && !mixedFrame)
    return YES;
  if (vertices.count == 0 && objects.count > 0)
    return NO;
  for (id vertex in vertices)
    if (!MeshVector(vertex))
      return NO;
  size_t pixelCount = width * height;
  uint8_t *pixels = calloc(pixelCount, 4);
  float *depth = malloc(pixelCount * sizeof(float));
  CameraPoint *points = malloc(MAX((NSUInteger)1, vertices.count) * sizeof(CameraPoint));
  if (!pixels || !depth || !points) {
    free(pixels);
    free(depth);
    free(points);
    return NO;
  }
  for (size_t index = 0; index < pixelCount; index++)
    depth[index] = -FLT_MAX;
  for (NSUInteger index = 0; index < vertices.count; index++)
    points[index] = Project(Vector(vertices[index]), camera);
  BOOL valid = YES;
  for (NSDictionary *object in objects) {
    NSUInteger start, count;
    if (![object isKindOfClass:[NSDictionary class]] ||
        !MeshIndex(object[@"vertex_start"], vertices.count, &start) ||
        !MeshIndex(object[@"vertex_count"], vertices.count - start, &count) ||
        count == 0 || ![object[@"triangles"] isKindOfClass:[NSArray class]] ||
        [object[@"triangles"] count] == 0) {
      valid = NO;
      break;
    }
    NSArray *color = object[@"color"] ?: @[ @0.25, @0.65, @0.9 ];
    if (!MeshVector(color)) {
      valid = NO;
      break;
    }
    NSArray *triangleColors=object[@"triangle_colors"];
    BOOL smoothEdges=[object[@"smooth_edges"] boolValue];
    if (triangleColors && (![triangleColors isKindOfClass:[NSArray class]] ||
                          triangleColors.count!=[object[@"triangles"] count])) { valid=NO;break; }
    NSUInteger triangleIndex=0;
    for (NSArray *triangle in object[@"triangles"]) {
      NSArray *faceColor=triangleColors?triangleColors[triangleIndex]:color;
      triangleIndex++;
      if (!MeshVector(faceColor)) {valid=NO;break;}
      NSUInteger a, b, c;
      if (![triangle isKindOfClass:[NSArray class]] || triangle.count != 3 ||
          !MeshIndex(triangle[0], count - 1, &a) ||
          !MeshIndex(triangle[1], count - 1, &b) ||
          !MeshIndex(triangle[2], count - 1, &c)) {
        valid = NO;
        break;
      }
      CameraPoint p = points[start + a], q = points[start + b],
                  r = points[start + c];
      double area = (q.u - p.u) * (r.v - p.v) -
                    (q.v - p.v) * (r.u - p.u);
      if (fabs(area) < 1.0e-10)
        continue;
      int left = (int)Clamp(floor(fmin(p.u, fmin(q.u, r.u))), 0, (double)width - 1),
          right = (int)Clamp(ceil(fmax(p.u, fmax(q.u, r.u))), 0, (double)width - 1),
          bottom = (int)Clamp(floor(fmin(p.v, fmin(q.v, r.v))), 0, (double)height - 1),
          top = (int)Clamp(ceil(fmax(p.v, fmax(q.v, r.v))), 0, (double)height - 1);
      /* Orthographic barycentric coordinates interpolate depth exactly.
       * Their gradients also give screen-space distance to triangle edges. */
      double ax = (q.v - r.v) / area, ay = (r.u - q.u) / area,
             bx = (r.v - p.v) / area, by = (p.u - r.u) / area;
      double edgeA = hypot(ax, ay), edgeB = hypot(bx, by),
             edgeC = hypot(ax + bx, ay + by);
      double ux = (q.u - p.u) / camera.scale,
             uy = (q.v - p.v) / camera.scale, uz = q.depth - p.depth,
             vx = (r.u - p.u) / camera.scale,
             vy = (r.v - p.v) / camera.scale, vz = r.depth - p.depth;
      double nx = uy * vz - uz * vy, ny = uz * vx - ux * vz,
             nz = ux * vy - uy * vx;
      double length = sqrt(nx * nx + ny * ny + nz * nz);
      double facing = nz < 0 ? -1 : 1;
      double light = fmax(0, facing * (-0.40 * nx + 0.70 * ny + 0.59 * nz) /
                                fmax(1.0e-20, length));
      double shade = 0.46 + 0.54 * light;
      double red = Clamp(VectorValue(faceColor, 0), 0, 1) * shade,
             green = Clamp(VectorValue(faceColor, 1), 0, 1) * shade,
             blue = Clamp(VectorValue(faceColor, 2), 0, 1) * shade;
      for (int y = bottom; y <= top; y++) {
        double dx = (double)left + 0.5 - r.u, dy = (double)y + 0.5 - r.v;
        double wa = ax * dx + ay * dy, wb = bx * dx + by * dy;
        for (int x = left; x <= right; x++, wa += ax, wb += bx) {
          double wc = 1 - wa - wb;
          if (wa < -1.0e-9 || wb < -1.0e-9 || wc < -1.0e-9)
            continue;
          double z = wa * p.depth + wb * q.depth + wc * r.depth;
          size_t pixel = ((height - 1 - (size_t)y) * width + (size_t)x);
          if (z < depth[pixel])
            continue;
          depth[pixel] = (float)z;
          double edgeDistance = fmin(wa / edgeA, fmin(wb / edgeB, wc / edgeC));
          double edgeShade = smoothEdges ? 1.0 :
                             0.86 + 0.14 * Clamp(edgeDistance / 0.65, 0, 1);
          pixels[4 * pixel] = (uint8_t)(255 * red * edgeShade);
          pixels[4 * pixel + 1] = (uint8_t)(255 * green * edgeShade);
          pixels[4 * pixel + 2] = (uint8_t)(255 * blue * edgeShade);
          pixels[4 * pixel + 3] = 255;
        }
      }
    }
    if (!valid)
      break;
  }
  /* Sand and point masses keep their sphere shading and shared depth. Liquid
   * samples are reconstructed once below, rather than painted as blue beads. */
  if (valid && mixedFrame) for (int family=0;family<2;family++) {
    NSArray *samples=mixedFrame[family?@"g":@"p"] ?: @[];
    NSArray *pointRadii=mixedFrame[@"__point_radii"] ?: @[];
    if (![samples isKindOfClass:[NSArray class]]) {valid=NO;break;}
    for (NSUInteger i=0;i<samples.count;i++) {
      if (!MeshVector(samples[i])) {valid=NO;break;}
      if (!family && RenderMaterial(i<materials.count?materials[i]:@"water")!=1) continue;
      CameraPoint center=Project(samples[i],camera);
      double radius=family?(i<pointRadii.count?fmax(0,[pointRadii[i] doubleValue]):0):particleRadius;
      double screenRadius=family?fmax(5.0,radius*camera.scale):fmax(1.65,radius*camera.scale);
      if (!isfinite(screenRadius) || screenRadius<=0) {valid=NO;break;}
      BOOL sand=!family && i<materials.count && [materials[i] isEqual:@"sand"];
      double base[3]={family?1.0:(sand?.80:.13),family?.80:(sand?.54:.58),family?.22:(sand?.26:.90)};
      int left=(int)Clamp(floor(center.u-screenRadius),0,(double)width-1),
          right=(int)Clamp(ceil(center.u+screenRadius),0,(double)width-1),
          bottom=(int)Clamp(floor(center.v-screenRadius),0,(double)height-1),
          top=(int)Clamp(ceil(center.v+screenRadius),0,(double)height-1);
      for (int y=bottom;y<=top;y++) for (int x=left;x<=right;x++) {
        double dx=((double)x+.5-center.u)/screenRadius,dy=((double)y+.5-center.v)/screenRadius;
        double radial=dx*dx+dy*dy;if(radial>1) continue;
        double nz=sqrt(fmax(0,1-radial)),z=center.depth+nz*radius;
        size_t pixel=((height-1-(size_t)y)*width+(size_t)x);
        if(z<depth[pixel]) continue;
        depth[pixel]=(float)z;
        double shade=.42+.58*fmax(0,-.4*dx+.7*dy+.59*nz);
        double specular=pow(fmax(0,-.4*dx+.7*dy+.59*nz),18)*.25;
        for(int channel=0;channel<3;channel++) pixels[4*pixel+(size_t)channel]=
            (uint8_t)(255*Clamp(base[channel]*shade+specular,0,1));
        pixels[4*pixel+3]=255;
      }
    }
    if(!valid) break;
  }
  if (valid && mixedFrame)
    valid=DrawLiquidSurface(pixels,depth,mixedFrame,materials,camera,width,height,particleRadius);
  BOOL rendered=valid && DrawRasterSurface(context,pixels,width,height);
  free(points);
  free(depth);
  free(pixels);
  return rendered;
}

static BOOL DrawFrameWithWaterStyle(CGContextRef context, NSDictionary *frame,
                      NSDictionary *previousFrame, NSArray *materials,
                      NSArray *rigidShapes, NSArray *colliders, NSArray *meshObjects,
                      NSArray *minimum, NSArray *maximum, PhyVideoCamera camera,
                      double particleRadius, size_t width, size_t height,
                      BOOL legacyWater) {
  DrawBackdrop(context, width, height);
  DrawGround(context, colliders, minimum, maximum, camera);
  if (frame[@"q"]) {
    NSArray *recordedVertices=frame[@"m"] ?: @[];
    if (![recordedVertices isKindOfClass:[NSArray class]] ||
        ![meshObjects isKindOfClass:[NSArray class]]) return NO;
    NSMutableArray *vertices=[recordedVertices mutableCopy],*objects=[meshObjects mutableCopy];
    if (!AppendRigidGeometry(vertices,objects,frame[@"r"] ?: @[],frame[@"q"],rigidShapes)) return NO;
    for (int band=0;band<kDepthBands;band++) for (NSDictionary *collider in colliders)
      if (![collider[@"type"] isEqual:@"plane"] && DepthBand(ColliderDepth(collider,camera),camera)==band)
        DrawCollider(context,collider,camera);
    if (!DrawMeshes(context,vertices,objects,camera,width,height,frame,materials,particleRadius)) return NO;
    CGContextSetRGBStrokeColor(context,0.58,0.76,0.91,0.18);
    CGContextSetLineWidth(context,1);
    CGContextStrokeRect(context,CGRectInset(CGRectMake(0,0,(CGFloat)width,(CGFloat)height),12.5,12.5));
    return YES;
  }
  double screenRadius = Clamp(1.08 * particleRadius * camera.scale, 1.65, 8.5);
  NSUInteger count = 0;
  RenderParticle *particles = legacyWater ? NULL :
      BuildRenderParticles(frame, materials, camera,screenRadius, width, &count);
  NSArray *gravity =
              [frame[@"g"] isKindOfClass:[NSArray class]] ? frame[@"g"] : @[],
          *rigids =
              [frame[@"r"] isKindOfClass:[NSArray class]] ? frame[@"r"] : @[];
  NSUInteger legacyCount=0;
  LegacyWaterParticle *legacy=NULL;
  if (legacyWater) {
    legacy=BuildLegacyWaterParticles(frame,previousFrame,materials,camera,
        screenRadius,width,height,&legacyCount);
    if (legacyCount && !legacy) {free(particles);return NO;}
    if (legacy) DrawLegacyWaterBody(context,legacy,legacyCount,screenRadius,width,height);
  } else if (!DrawLiquidLayer(context,frame,materials,camera,width,height,particleRadius)) {
    free(particles);return NO;
  }
  for (int band = 0; band < kDepthBands; band++) {
    if (legacy) DrawLegacyWaterBand(context,legacy,legacyCount,band,screenRadius);
    if (particles) {
      DrawSandBand(context, particles, count, band, screenRadius);
    }
    for (NSDictionary *collider in colliders)
      if (![collider[@"type"] isEqual:@"plane"] &&
          DepthBand(ColliderDepth(collider, camera), camera) == band)
        DrawCollider(context, collider, camera);
    DrawGravityBodies(context, gravity, camera, band);
    DrawRigids(context, rigids, rigidShapes, camera, band);
  }
  free(particles);
  free(legacy);
  if (!DrawMeshes(context, frame[@"m"] ?: @[], meshObjects, camera, width, height,nil,materials,particleRadius))
    return NO;
  CGContextSetRGBStrokeColor(context, 0.58, 0.76, 0.91, 0.18);
  CGContextSetLineWidth(context, 1);
  CGContextStrokeRect(
      context,
      CGRectInset(CGRectMake(0, 0, (CGFloat)width, (CGFloat)height), 12.5,
                  12.5));
  return YES;
}

BOOL PhyVideoDrawFrame(CGContextRef context, NSDictionary *frame,
                      NSDictionary *previousFrame, NSArray *materials,
                      NSArray *rigidShapes, NSArray *colliders, NSArray *meshObjects,
                      NSArray *minimum, NSArray *maximum, PhyVideoCamera camera,
                      double particleRadius, size_t width, size_t height) {
  return DrawFrameWithWaterStyle(context,frame,previousFrame,materials,rigidShapes,
      colliders,meshObjects,minimum,maximum,camera,particleRadius,width,height,NO);
}

static NSDictionary *DisplayIndexMap(id identifiers,id positions) {
  if (![identifiers isKindOfClass:[NSArray class]] || ![positions isKindOfClass:[NSArray class]] ||
      [identifiers count]!=[positions count]) return nil;
  NSMutableDictionary *map=[NSMutableDictionary dictionary];
  for (NSUInteger index=0;index<[identifiers count];index++) {
    id identifier=identifiers[index];
    if (![identifier isKindOfClass:[NSString class]] || map[identifier] || !MeshVector(positions[index])) return nil;
    map[identifier]=@(index);
  }
  return map;
}
static NSArray *DisplayAttachment(id endpoint,NSDictionary *entities,NSDictionary *points,
    NSDictionary *rigids,NSDictionary *meshes,NSDictionary *frame) {
  if (![endpoint isKindOfClass:[NSDictionary class]] || ![endpoint[@"entity"] isKindOfClass:[NSString class]]) return nil;
  NSString *identifier=endpoint[@"entity"];
  NSDictionary *entity=entities[identifier];NSString *type=entity[@"type"];
  if ([type isEqual:@"point_mass"]) {
    NSNumber *index=points[identifier];
    if (!index || [endpoint count]!=1) return nil;
    return frame[@"g"][[index unsignedIntegerValue]];
  }
  if ([type isEqual:@"rigid_body"]) {
    NSNumber *index=rigids[identifier];NSArray *local=endpoint[@"local_point"] ?: @[@0,@0,@0];
    if (!index || !MeshVector(local) || [endpoint count]>(endpoint[@"local_point"]?2:1)) return nil;
    NSUInteger i=[index unsignedIntegerValue];NSArray *poses=frame[@"q"];
    if (![poses isKindOfClass:[NSArray class]] || i>=poses.count || !UnitQuaternion(poses[i])) return nil;
    return RotateBodyPoint(poses[i],local,frame[@"r"][i]);
  }
  if ([type isEqual:@"mesh"]) {
    NSDictionary *mesh=meshes[identifier];NSArray *vertices=frame[@"m"];
    NSUInteger start,count,vertex;
    if (!mesh || [endpoint count]!=2 || ![vertices isKindOfClass:[NSArray class]] ||
        !MeshIndex(mesh[@"vertex_start"],vertices.count,&start) ||
        !MeshIndex(mesh[@"vertex_count"],vertices.count-start,&count) || count==0 ||
        !MeshIndex(endpoint[@"vertex"],count-1,&vertex) || !MeshVector(vertices[start+vertex])) return nil;
    return vertices[start+vertex];
  }
  return nil;
}
/* An ideal pivot is a constraint annotation, never display contact geometry. */
static BOOL DrawPivotAnnotations(CGContextRef context, NSDictionary *frame,
    NSDictionary *entities, NSDictionary *rigids, PhyVideoCamera camera) {
  if (!frame[@"q"]) return YES;
  for (NSString *identifier in rigids) {
    id pivot=entities[identifier][@"pivot"];
    if (pivot && (![pivot isKindOfClass:[NSDictionary class]] || !MeshVector(pivot[@"point"])))
      return NO;
  }
  CGContextSaveGState(context);
  CGContextSetLineCap(context,kCGLineCapRound);
  for (NSString *identifier in rigids) {
    NSDictionary *pivot=entities[identifier][@"pivot"];
    if (!pivot) continue;
    NSUInteger index=[rigids[identifier] unsignedIntegerValue];
    CameraPoint anchor=Project(pivot[@"point"],camera),center=Project(frame[@"r"][index],camera);
    CGContextSetRGBStrokeColor(context,.83,.88,.96,.92);
    CGContextSetLineWidth(context,1.6);
    CGContextBeginPath(context);CGContextMoveToPoint(context,anchor.u,anchor.v);
    CGContextAddLineToPoint(context,center.u,center.v);CGContextStrokePath(context);
    CGContextSetRGBFillColor(context,.97,.73,.25,1);
    CGContextFillEllipseInRect(context,CGRectMake(anchor.u-3,anchor.v-3,6,6));
    CGContextSetRGBStrokeColor(context,.99,.88,.59,1);
    CGContextSetLineWidth(context,1.2);
    CGContextStrokeEllipseInRect(context,CGRectMake(anchor.u-6,anchor.v-6,12,12));
    CGContextBeginPath(context);CGContextMoveToPoint(context,anchor.u-9,anchor.v);
    CGContextAddLineToPoint(context,anchor.u+9,anchor.v);
    CGContextMoveToPoint(context,anchor.u,anchor.v-9);
    CGContextAddLineToPoint(context,anchor.u,anchor.v+9);CGContextStrokePath(context);
  }
  CGContextRestoreGState(context);
  return YES;
}
BOOL PhyVideoDrawTrajectoryFrame(CGContextRef context,NSDictionary *frame,
    NSDictionary *previousFrame,NSDictionary *scene,NSDictionary *trajectory,
    PhyVideoCamera camera,size_t width,size_t height) {
  NSArray *materials=trajectory[@"particle_materials"] ?: @[],
          *shapes=trajectory[@"rigid_shapes"] ?: @[],*colliders=scene[@"colliders"] ?: @[],
          *meshObjects=trajectory[@"mesh_objects"] ?: @[],*links=scene[@"connections"];
  NSArray *minimum=Vector(scene[@"world"][@"bounds"][@"min"]),
          *maximum=Vector(scene[@"world"][@"bounds"][@"max"]);
  double radius=[trajectory[@"particle_radius"] doubleValue];
  BOOL mixed=frame[@"q"]!=nil || scene[@"coupling"]!=nil;
  if ([links isKindOfClass:[NSArray class]]) for (id link in links)
    if ([link isKindOfClass:[NSDictionary class]] && (link[@"endpoints"] || link[@"solid"])) mixed=YES;
  if (!mixed) {
    NSArray *vertices=frame[@"m"];
    BOOL noMesh=(!vertices || ([vertices isKindOfClass:[NSArray class]] && vertices.count==0)) &&
        [meshObjects isKindOfClass:[NSArray class]] && meshObjects.count==0;
    BOOL waterOnly=[materials isKindOfClass:[NSArray class]];
    for (id material in materials)
      if (![material isKindOfClass:[NSString class]] || ![material isEqual:@"water"])
        waterOnly=NO;
    BOOL legacyWater=noMesh && waterOnly &&
        [scene[@"__presentation_water_renderer"] isEqual:@"legacy_v2"];
    BOOL rendered=DrawFrameWithWaterStyle(context,frame,previousFrame,materials,shapes,
        colliders,meshObjects,minimum,maximum,camera,radius,width,height,legacyWater);
    return rendered && (!links || PhyVideoDrawConnections(context,frame,scene,
                        trajectory[@"gravity_body_ids"],camera));
  }
  if ((links && ![links isKindOfClass:[NSArray class]]) || [links count]>256 ||
      ![meshObjects isKindOfClass:[NSArray class]] ||
      ![scene[@"entities"] isKindOfClass:[NSArray class]]) return NO;
  NSMutableDictionary *entities=[NSMutableDictionary dictionary],*meshes=[NSMutableDictionary dictionary];
  for (id entity in scene[@"entities"]) {
    if (![entity isKindOfClass:[NSDictionary class]] || ![entity[@"id"] isKindOfClass:[NSString class]] ||
        entities[entity[@"id"]]) return NO;
    entities[entity[@"id"]]=entity;
  }
  for (id mesh in meshObjects) {
    if (![mesh isKindOfClass:[NSDictionary class]] || ![mesh[@"id"] isKindOfClass:[NSString class]] ||
        meshes[mesh[@"id"]]) return NO;
    meshes[mesh[@"id"]]=mesh;
  }
  NSDictionary *points=DisplayIndexMap(trajectory[@"gravity_body_ids"] ?: @[],frame[@"g"] ?: @[]),
               *rigids=DisplayIndexMap(trajectory[@"rigid_ids"] ?: @[],frame[@"r"] ?: @[]);
  if (!points || !rigids || ![(frame[@"m"] ?: @[]) isKindOfClass:[NSArray class]]) return NO;
  NSMutableDictionary *displayFrame=[frame mutableCopy];displayFrame[@"q"]=frame[@"q"] ?: @[];
  BOOL waterOnly=[materials isKindOfClass:[NSArray class]];
  for (id material in materials)
    if (![material isKindOfClass:[NSString class]] || ![material isEqual:@"water"])
      waterOnly=NO;
  if (waterOnly && meshObjects.count>0 && [frame[@"m"] count]>0 &&
      [scene[@"__presentation_water_renderer"] isEqual:@"mesh_hybrid"]) {
    displayFrame[@"__separate_sparse_water"]=@YES;
    displayFrame[@"__previous_p"]=previousFrame[@"p"] ?: @[];
  }
  NSMutableArray *vertices=[(frame[@"m"] ?: @[]) mutableCopy],*objects=[meshObjects mutableCopy],
                 *overlayLinks=[NSMutableArray array],*overlayPositions=[NSMutableArray array],
                 *overlayEntities=[NSMutableArray array],*overlayIDs=[NSMutableArray array],
                 *pointRadii=[NSMutableArray array];
  for (id identifier in trajectory[@"gravity_body_ids"] ?: @[]) {
    NSDictionary *entity=entities[identifier];id collisionRadius=entity[@"collision_radius"] ?: @0;
    if (![entity[@"type"] isEqual:@"point_mass"] || ![collisionRadius isKindOfClass:[NSNumber class]] ||
        !isfinite([collisionRadius doubleValue]) || [collisionRadius doubleValue]<0 ||
        [collisionRadius doubleValue]>10) return NO;
    [pointRadii addObject:collisionRadius];
  }
  displayFrame[@"__point_radii"]=pointRadii;
  for (id link in links) {
    if (![link isKindOfClass:[NSDictionary class]]) return NO;
    NSString *kind=link[@"type"];id rest=link[@"rest_length"];
    if (!([kind isEqual:@"spring"] || [kind isEqual:@"rod"] || [kind isEqual:@"rope"]) ||
        ![rest isKindOfClass:[NSNumber class]] || !isfinite([rest doubleValue]) ||
        [rest doubleValue]<=0 || [rest doubleValue]>1000) return NO;
    NSArray *ends=link[@"endpoints"];
    if (!ends) {
      NSArray *ids=link[@"entities"];
      if (![ids isKindOfClass:[NSArray class]] || ids.count!=2 ||
          ![ids[0] isKindOfClass:[NSString class]] || ![ids[1] isKindOfClass:[NSString class]]) return NO;
      ends=@[@{@"entity":ids[0]},@{@"entity":ids[1]}];
    } else if (link[@"entities"]) return NO;
    if (![ends isKindOfClass:[NSArray class]] || ends.count!=2 || [ends[0] isEqual:ends[1]]) return NO;
    NSArray *a=DisplayAttachment(ends[0],entities,points,rigids,meshes,displayFrame),
            *b=DisplayAttachment(ends[1],entities,points,rigids,meshes,displayFrame);
    if (!a || !b) return NO;
    if (link[@"solid"]) {
      NSDictionary *solid=link[@"solid"];
      if (![solid isKindOfClass:[NSDictionary class]]) return NO;
      id value=solid[@"radius"];if (!PositiveDimension(value)) return NO;
      NSArray *color=[kind isEqual:@"rod"]?@[@0.77,@0.83,@0.93]:
          ([kind isEqual:@"rope"]?@[@1.0,@0.69,@0.30]:@[@0.27,@0.85,@0.96]);
      if (!AppendCapsuleGeometry(vertices,objects,a,b,[value doubleValue],color)) return NO;
    } else {
      NSMutableDictionary *annotation=[link mutableCopy];NSMutableArray *ids=[NSMutableArray array];
      for (NSArray *position in @[a,b]) {
        NSString *identifier=[NSString stringWithFormat:@"attachment-%lu",(unsigned long)overlayIDs.count];
        [ids addObject:identifier];[overlayIDs addObject:identifier];[overlayPositions addObject:position];
        [overlayEntities addObject:@{@"id":identifier,@"type":@"point_mass",@"fixed":@NO}];
      }
      [annotation removeObjectForKey:@"endpoints"];annotation[@"entities"]=ids;
      [overlayLinks addObject:annotation];
    }
  }
  displayFrame[@"m"]=vertices;
  if (!PhyVideoDrawFrame(context,displayFrame,previousFrame,materials,shapes,colliders,objects,
                         minimum,maximum,camera,radius,width,height)) return NO;
  if (!DrawPivotAnnotations(context,displayFrame,entities,rigids,camera)) return NO;
  if (!overlayLinks.count) return YES;
  NSDictionary *overlayScene=@{@"connections":overlayLinks,@"entities":overlayEntities,@"__attachment_overlay":@YES};
  return PhyVideoDrawConnections(context,@{@"g":overlayPositions},overlayScene,overlayIDs,camera);
}

static void CameraSphereSamples(NSMutableArray *samples,NSArray *center,double radius) {
  for (int x=-1;x<=1;x+=2) for (int y=-1;y<=1;y+=2) for (int z=-1;z<=1;z+=2)
    [samples addObject:@[@(VectorValue(center,0)+x*radius),@(VectorValue(center,1)+y*radius),
                        @(VectorValue(center,2)+z*radius)]];
}
static PhyVideoCamera ApplyPresentationZoom(NSDictionary *scene,PhyVideoCamera camera) {
  id value=scene[@"__presentation_camera_zoom"];
  if([value isKindOfClass:[NSNumber class]]) {
    double zoom=[value doubleValue];
    if(isfinite(zoom) && zoom>=1.0 && zoom<=3.0) camera.scale*=zoom;
  }
  return camera;
}
PhyVideoCamera PhyVideoBuildTrajectoryCamera(NSDictionary *scene,NSDictionary *trajectory,
                                            size_t width,size_t height) {
  NSArray *frames=trajectory[@"frames"] ?: @[],*shapes=trajectory[@"rigid_shapes"] ?: @[],
          *colliders=scene[@"colliders"] ?: @[],*minimum=Vector(scene[@"world"][@"bounds"][@"min"]),
          *maximum=Vector(scene[@"world"][@"bounds"][@"max"]);
  double particleRadius=[trajectory[@"particle_radius"] doubleValue];
  id focus=scene[@"__presentation_camera_corners"];
  if([focus isKindOfClass:[NSArray class]] && [focus count]==8) {
    return ApplyPresentationZoom(scene,PhyVideoBuildCamera(
        @[@{@"p":focus}],@[],colliders,minimum,maximum,particleRadius,width,height));
  }
  BOOL mixed=scene[@"coupling"]!=nil;
  for (NSDictionary *frame in frames) if(frame[@"q"]) {mixed=YES;break;}
  if(!mixed) return ApplyPresentationZoom(scene,PhyVideoBuildCamera(
      frames,shapes,colliders,minimum,maximum,particleRadius,width,height));
  NSMutableDictionary *entities=[NSMutableDictionary dictionary],*meshes=[NSMutableDictionary dictionary];
  for(id entity in scene[@"entities"] ?: @[])
    if([entity isKindOfClass:[NSDictionary class]] && [entity[@"id"] isKindOfClass:[NSString class]])
      entities[entity[@"id"]]=entity;
  for(id mesh in trajectory[@"mesh_objects"] ?: @[])
    if([mesh isKindOfClass:[NSDictionary class]] && [mesh[@"id"] isKindOfClass:[NSString class]])
      meshes[mesh[@"id"]]=mesh;
  NSMutableArray *expandedFrames=[NSMutableArray arrayWithCapacity:frames.count];
  for(NSDictionary *frame in frames) {
    NSMutableArray *samples=[NSMutableArray array];
    NSDictionary *points=DisplayIndexMap(trajectory[@"gravity_body_ids"] ?: @[],frame[@"g"] ?: @[]),
                 *rigids=DisplayIndexMap(trajectory[@"rigid_ids"] ?: @[],frame[@"r"] ?: @[]);
    if(points && rigids) {
      for(NSString *identifier in rigids) {
        id pivot=entities[identifier][@"pivot"];
        if(frame[@"q"] && [pivot isKindOfClass:[NSDictionary class]] && MeshVector(pivot[@"point"]))
          [samples addObject:pivot[@"point"]];
      }
      for(NSString *identifier in points) {
        double radius=[entities[identifier][@"collision_radius"] doubleValue];
        if(isfinite(radius) && radius>0 && radius<=10)
          CameraSphereSamples(samples,frame[@"g"][[points[identifier] unsignedIntegerValue]],radius);
      }
      id links=scene[@"connections"];
      if([links isKindOfClass:[NSArray class]]) for(id link in links) {
        if(![link isKindOfClass:[NSDictionary class]] || ![link[@"solid"] isKindOfClass:[NSDictionary class]]) continue;
        id radiusValue=link[@"solid"][@"radius"];
        if(!PositiveDimension(radiusValue)) continue;
        NSArray *ends=link[@"endpoints"];
        if(!ends) {
          NSArray *ids=link[@"entities"];
          if(![ids isKindOfClass:[NSArray class]] || ids.count!=2 ||
             ![ids[0] isKindOfClass:[NSString class]] || ![ids[1] isKindOfClass:[NSString class]]) continue;
          ends=@[@{@"entity":ids[0]},@{@"entity":ids[1]}];
        }
        if(![ends isKindOfClass:[NSArray class]] || ends.count!=2) continue;
        for(id end in ends) {
          NSArray *point=DisplayAttachment(end,entities,points,rigids,meshes,frame);
          if(point) CameraSphereSamples(samples,point,[radiusValue doubleValue]);
        }
      }
    }
    NSMutableDictionary *expanded=[frame mutableCopy];expanded[@"__camera_extra_points"]=samples;
    [expandedFrames addObject:expanded];
  }
  return ApplyPresentationZoom(scene,PhyVideoBuildCamera(
      expandedFrames,shapes,colliders,minimum,maximum,particleRadius,width,height));
}
