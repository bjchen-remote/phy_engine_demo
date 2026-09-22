#ifndef PHYSICS_CONNECTIONS_NATIVE_H
#define PHYSICS_CONNECTIONS_NATIVE_H
#include <stdint.h>

#define CONNECTIONS_ABI 1u
/* Synchronous borrowed buffers: caller owns all pointers until return. */
typedef struct { int32_t type, a, b; double rest, stiffness, damping; } Connection;
typedef struct {
    int32_t type;
    double origin[3], vector[3], strength, radius, secondary_strength, start_time, end_time;
} ConnectionField;
/* Types: centroid=0, speed=1, distance=2, length=3, extension=4, force=5, energy=6.
   For connection metrics a is a connection index; otherwise a/b are node indices. */
typedef struct { int32_t type, a, b, axis; } ConnectionMetric;
typedef struct {
    uint32_t abi;
    int32_t nodes, connections, fields, metrics;
    int32_t frame_capacity, observation_capacity, iterations, substeps;
    double dt, duration, fps, deadline_seconds;
    double *positions, *velocities, *mass;
    int32_t *fixed;
    uint32_t *field_mask;
    Connection *connection;
    ConnectionField *field;
    ConnectionMetric *metric;
    double *frames, *frame_times, *observation_times, *observation_values;
} ConnectionSimulation;
typedef struct {
    int32_t status, completed, finite, frames_written, observations_written;
    int32_t steps, substeps, max_substeps_used;
    double simulated_time_s, runtime_s, max_rod_error_m, max_rope_extension_m, max_constraint_error_ratio, max_speed_m_s;
    double initial_kinetic_energy, final_kinetic_energy, initial_spring_energy, final_spring_energy;
    double connection_peak_relative_energy_drift;
} ConnectionDiagnostics;

#if defined(__GNUC__)
#define CONNECTIONS_API __attribute__((visibility("default")))
#else
#define CONNECTIONS_API
#endif
CONNECTIONS_API uint32_t connections_abi_version(void);
CONNECTIONS_API int32_t connections_simulate(ConnectionSimulation *, ConnectionDiagnostics *);
#endif
