#ifndef PHYSICS_DEMO_VIDEO_RENDER_CORE_H
#define PHYSICS_DEMO_VIDEO_RENDER_CORE_H

#import <CoreGraphics/CoreGraphics.h>
#import <Foundation/Foundation.h>

typedef struct {
  double cosineYaw, sineYaw, cosinePitch, sinePitch;
  double centerU, centerV, scale, originX, originY;
  double minimumDepth, maximumDepth;
} PhyVideoCamera;

double PhyVideoHorizontalFloor(NSArray *colliders, NSArray *minimum);

PhyVideoCamera PhyVideoBuildCamera(
    NSArray *frames, NSArray *rigidShapes, NSArray *colliders,
    NSArray *minimum, NSArray *maximum, double particleRadius,
    size_t width, size_t height);

BOOL PhyVideoDrawFrame(
    CGContextRef context, NSDictionary *frame, NSDictionary *previousFrame,
    NSArray *materials, NSArray *rigidShapes, NSArray *colliders,
    NSArray *meshObjects, NSArray *minimum, NSArray *maximum, PhyVideoCamera camera,
    double particleRadius, size_t width, size_t height);

/* Optional standalone point-mass network overlay. Endpoint positions come
 * exclusively from frame.g, indexed by trajectory.gravity_body_ids. */
BOOL PhyVideoDrawConnections(
    CGContextRef context, NSDictionary *frame, NSDictionary *scene,
    NSArray *bodyIDs, PhyVideoCamera camera);

/* Complete trajectory entry point. Legacy frames retain the original drawing
 * path. Quaternion frames share a depth buffer across mesh/rigid geometry,
 * particles and physical capsule links, then draw non-solid link annotations.
 * Endpoint metadata is read from gravity_body_ids, rigid_ids and mesh_objects.
 */
BOOL PhyVideoDrawTrajectoryFrame(
    CGContextRef context, NSDictionary *frame, NSDictionary *previousFrame,
    NSDictionary *scene, NSDictionary *trajectory, PhyVideoCamera camera,
    size_t width, size_t height);
PhyVideoCamera PhyVideoBuildTrajectoryCamera(
    NSDictionary *scene, NSDictionary *trajectory, size_t width, size_t height);

#endif
