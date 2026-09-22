#ifndef PHYSICS_MESH_NATIVE_H
#define PHYSICS_MESH_NATIVE_H
#include <stdint.h>
#define MESH_ABI 1
typedef struct { int32_t type; double a[3], b[3], c[3], friction; } MeshCollider;
typedef struct { int32_t start, count, triangle_start, triangle_count, motion, closed;
    double velocity[3], compliance, damping, friction, thickness, rest_volume; } MeshObject;
typedef struct { int32_t a, b, bending; double rest, compliance; } MeshEdge;
typedef struct { int32_t type, a, b, axis0, axis1; double origin[3]; } MeshMetric;
typedef struct {
    uint32_t abi;
    int32_t vertices, triangles, objects, edges, colliders, metrics;
    int32_t frame_capacity, observation_capacity, iterations, substeps;
    double dt, duration, fps, deadline_seconds, gravity[3], bounds_min[3], bounds_max[3];
    double *positions, *velocities, *inverse_mass;
    int32_t *indices;
    MeshObject *object;
    MeshEdge *edge;
    MeshCollider *collider;
    MeshMetric *metric;
    float *frames;
    double *frame_times, *observation_times, *observation_values;
} MeshSimulation;
typedef struct {
    int32_t status, completed, finite, frames_written, observations_written, steps, substeps;
    int32_t contact_count, inverted_objects, closed_objects, max_substeps_used;
    double simulated_time_s, runtime_s, max_edge_strain, min_volume_ratio, max_volume_ratio;
    double residual_penetration_m, max_contact_correction_m, maximum_speed_m_s;
} MeshDiagnostics;
#if defined(__GNUC__)
#define MESH_API __attribute__((visibility("default")))
#else
#define MESH_API
#endif
MESH_API uint32_t mesh_abi_version(void);
MESH_API int32_t mesh_simulate(MeshSimulation *, MeshDiagnostics *);
/* Persistent shared-buffer solver. Creation performs the same full validation
 * as mesh_simulate, shallow-copies parameters, and retains the initial/rest
 * state until destroy. The caller owns all pointed-to buffers throughout the
 * context lifetime and may update position/velocity contents between calls.
 * Topology, object/edge parameters, counts and buffer pointers remain fixed.
 * Status: 0=success, 1=invalid input, 2=allocation, 3=deadline, 4=nonfinite.
 */
typedef struct MeshContext MeshContext;
MESH_API int32_t mesh_context_create(const MeshSimulation *, MeshContext **, MeshDiagnostics *);
MESH_API int32_t mesh_context_step(MeshContext *, double h, MeshDiagnostics *);
/* One additional internal constraint/contact sweep, without time integration
 * or gravity; resulting position corrections are added to velocity as dx/h. */
MESH_API int32_t mesh_context_project(MeshContext *, double h, MeshDiagnostics *);
MESH_API int32_t mesh_context_refit(MeshContext *);
/* Discrete, two-sided sphere/triangle contact. Refit once before each batch.
 * The barycentric reaction updates dynamic triangle vertices as well as the
 * point. Position correction is split from an inelastic/frictional velocity
 * impulse to avoid launching initially overlapping resting bodies. This is
 * discrete contact, not complete CCD; the caller must enforce a shared CFL.
 */
MESH_API int32_t mesh_context_contact_point(MeshContext *, double position[3], double velocity[3],
    double inverse_mass, double radius, double h, double friction, int32_t *contact_count);
MESH_API int32_t mesh_context_diagnostics(MeshContext *, MeshDiagnostics *);
MESH_API void mesh_context_destroy(MeshContext *);
#endif
