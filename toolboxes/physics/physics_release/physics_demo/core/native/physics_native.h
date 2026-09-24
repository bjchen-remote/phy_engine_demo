#ifndef PHYSICS_DEMO_NATIVE_H
#define PHYSICS_DEMO_NATIVE_H

#include <stdint.h>

#if defined(_WIN32)
#  define PHY_EXPORT __declspec(dllexport)
#else
#  define PHY_EXPORT __attribute__((visibility("default")))
#endif

#define PHY_ABI_VERSION 5u

enum {
    PHY_MATERIAL_WATER = 0,
    PHY_MATERIAL_SAND = 1
};

enum {
    PHY_COLLIDER_PLANE = 0,
    PHY_COLLIDER_SPHERE = 1,
    PHY_COLLIDER_BOX = 2,
    PHY_COLLIDER_CAPSULE = 3
};

enum {
    PHY_FIELD_UNIFORM = 0,
    PHY_FIELD_RADIAL = 1,
    PHY_FIELD_VORTEX = 2
};

enum {
    PHY_STATUS_OK = 0,
    PHY_STATUS_INVALID_ARGUMENT = 1,
    PHY_STATUS_OUT_OF_MEMORY = 2,
    PHY_STATUS_TIMED_OUT = 3,
    PHY_STATUS_NONFINITE = 4,
    PHY_STATUS_INTERNAL_ERROR = 5
};

typedef struct {
    int32_t type;
    double a[3];                 /* plane normal, sphere/box centre, or capsule endpoint A */
    double b[3];                 /* plane/sphere scalar, box size, or capsule endpoint B */
    double c[3];                 /* plane water adhesion or capsule radius in c[0] */
    double friction;             /* position-level Coulomb friction coefficient */
} PhyCollider;

typedef struct {
    int32_t type;
    double origin[3];            /* radial/vortex centre */
    double vector[3];            /* uniform acceleration or unit vortex axis */
    double strength;             /* signed radial/tangential acceleration */
    double radius;               /* radial/vortex support radius */
    double secondary_strength;   /* inward acceleration for vortex */
    double start_time;
    double end_time;
} PhyForceField;

/* Passive float64 reducers: particle range, point mass, or static rigid. */
typedef struct {
    int32_t kind;
    int32_t start;
    int32_t count;
    int32_t shape;
    double position[3];
    double velocity[3];
    double size[3];
} PhyObservationEntity;

typedef struct {
    int32_t type;                /* radius, centroid axis, speed, distance, gap */
    int32_t axes[2];
    double origin[3];
    PhyObservationEntity a;
    PhyObservationEntity b;
} PhyObservationMetric;

/*
 * Python owns every pointer for the duration of phy_simulate().  The solver
 * mutates state arrays in place and writes frame-major output buffers.  All
 * hot state is structure-of-arrays and double precision; frame particles are
 * float to keep the JSON/video hand-off bounded.
 */
typedef struct {
    uint32_t abi_version;
    int32_t particle_count;
    int32_t body_count;
    int32_t collider_count;
    int32_t field_count;
    int32_t render_count;
    int32_t frame_capacity;
    int32_t density_iterations;
    int32_t divergence_iterations;
    int32_t thread_count;
    int32_t max_substeps;

    double spacing;
    double dt;
    double duration;
    double output_fps;
    double gravity[3];
    double bounds_min[3];
    double bounds_max[3];
    double gravity_G;
    double softening;
    double water_sand_drag;
    double wetting_rate;
    double deadline_seconds;

    int32_t *material;
    double *x;
    double *y;
    double *z;
    double *vx;
    double *vy;
    double *vz;
    double *anchor_x;
    double *anchor_y;
    double *anchor_z;
    double *viscosity;
    double *surface_tension;
    double *friction;
    double *cohesion;
    double *wetness;

    int32_t *body_fixed;
    double *body_mass;
    double *body_x;
    double *body_y;
    double *body_z;
    double *body_vx;
    double *body_vy;
    double *body_vz;

    PhyCollider *colliders;
    PhyForceField *fields;
    uint32_t *particle_field_mask;
    uint32_t *body_field_mask;
    int32_t *render_indices;
    float *frame_particles;      /* frame_capacity * render_count * 3 */
    double *frame_bodies;        /* frame_capacity * body_count * 3 */
    double *frame_times;         /* frame_capacity */

    int32_t observation_metric_count;
    int32_t observation_nbody;
    int32_t observation_capacity;
    PhyObservationMetric *observation_metrics;
    double *observation_times;
    double *observation_values;  /* sample-major scalar metrics */
    double *observation_nbody_values; /* sample-major: radius, min distance, E, px,py,pz */
} PhySimulation;

typedef struct {
    int32_t status;
    int32_t completed;
    int32_t finite;
    int32_t frames_written;
    int32_t macro_steps;
    int32_t substeps;
    int32_t max_substeps_used;
    int32_t max_neighbors;
    int32_t threads_used;
    int32_t density_iterations_total;
    int32_t divergence_iterations_total;
    int32_t density_iteration_limit_hits;
    int32_t divergence_iteration_limit_hits;
    int32_t cfl_limited_steps;
    double simulated_time_s;
    double runtime_s;
    double max_projection_correction_m;
    double max_particle_contact_correction_m;
    double peak_mean_density_excess;
    double peak_mean_divergence_error;
    double peak_max_density_error;
    double peak_max_divergence_error;
    double minimum_substep_s;
    double maximum_particle_speed_m_s;
    double mean_sand_displacement_m;
    double mean_sand_wetness;
    double final_mean_water_density_ratio;
    double final_max_water_density_ratio;
    double minimum_water_separation_ratio;
    double water_separation_p01_ratio;
    double close_water_particle_fraction;
    double water_density_p50_ratio;
    double water_density_p95_ratio;
    double water_density_p99_ratio;
    double planar_boundary_support_fraction;
    double represented_water_volume_m3;
    int32_t observations_written;
} PhyDiagnostics;

PHY_EXPORT uint32_t phy_abi_version(void);
PHY_EXPORT const char *phy_build_string(void);
PHY_EXPORT int phy_simulate(PhySimulation *simulation, PhyDiagnostics *diagnostics);

/* Persistent particle-only stepping for a coupled world. ABI 5 structs stay
 * unchanged. create borrows the arrays and diagnostics until destroy, and
 * snapshots the descriptor (counts, settings and pointers must stay fixed).
 * Array contents, including positions/velocities and collider poses, may be
 * changed between calls. No frames, observations or N-body motion are written.
 * Each step advances exactly dt; the world owns CFL/event subdivision and
 * completion. The existing simulate-compatible validation/buffers are required.
 * Failures return a PHY_STATUS_* code, or NULL at creation with diagnostics set.
 */
typedef struct PhyContext PhyContext;
PHY_EXPORT PhyContext *phy_context_create(PhySimulation *simulation, PhyDiagnostics *diagnostics);
PHY_EXPORT int phy_context_step(PhyContext *context, double absolute_time, double dt);
/* Recompute final particle diagnostics after external contact corrections;
 * does not integrate, sample frames, or change physical state. */
PHY_EXPORT int phy_context_refresh(PhyContext *context);
/* Explicit rate >=0 (s^-1) overrides legacy background water velocity decay.
 * Coupled worlds use zero; viscosity and material damping remain unchanged. */
PHY_EXPORT int phy_context_set_velocity_decay(PhyContext *context, double rate_s_inverse);
PHY_EXPORT void phy_context_destroy(PhyContext *context);

#endif
