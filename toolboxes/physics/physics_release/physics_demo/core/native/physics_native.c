#define _POSIX_C_SOURCE 200809L

#include "physics_native.h"
#include "particle_gravity.h"

#include <float.h>
#include <math.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846264338327950288
#endif

#define PHY_MAX_ACCELERATION 250.0

typedef void (*ParallelJob)(void *context, int begin, int end);

typedef struct ThreadPool ThreadPool;
typedef struct {
    ThreadPool *pool;
    int worker_id;
} WorkerArgument;

struct ThreadPool {
    int threads;
    int created;
    int stopping;
    unsigned generation;
    int finished;
    int item_count;
    ParallelJob job;
    void *job_context;
    pthread_t *handles;
    WorkerArgument *arguments;
    pthread_mutex_t mutex;
    pthread_cond_t start_condition;
    pthread_cond_t finish_condition;
};

typedef struct {
    int particle_count;
    size_t slot_capacity;
    uint8_t *slot_used;
    int64_t *slot_x;
    int64_t *slot_y;
    int64_t *slot_z;
    int32_t *slot_count;
    int32_t *slot_start;
    int32_t *slot_cursor;
    int64_t *particle_x;
    int64_t *particle_y;
    int64_t *particle_z;
    int32_t *ordered_particles;
    int32_t *neighbor_offsets;
    int32_t *neighbors;
    size_t neighbor_capacity;
    int max_neighbors;
} CompactGrid;

typedef struct {
    PhySimulation *input;
    PhyDiagnostics *diagnostics;
    ThreadPool pool;
    CompactGrid grid;
    PGTree gravity_tree;
    double *gravity_ax, *gravity_ay, *gravity_az;
    double particle_gravity_maximum;
    double h;
    double h2;
    double volume;
    double particle_radius;
    double poly6_coefficient;
    double spiky_coefficient;
    double self_kernel;
    double cohesion_coefficient;
    double cohesion_constant;
    double current_dt;
    double current_time;
    double velocity_decay_rate; /* negative preserves the legacy expression */
    double maximum_field_acceleration;
    double boundary_adhesion_acceleration_bound;
    double deadline;
    atomic_int deadline_exceeded;
    int pressure_mode; /* 0: divergence, 1: density */
    int contact_iterations;

    double *prev_x;
    double *prev_y;
    double *prev_z;
    double *density;
    double *factor;
    double *source;
    double *pressure;
    double *pressure_ax;
    double *pressure_ay;
    double *pressure_az;
    double *error;
    double *delta_x;
    double *delta_y;
    double *delta_z;
    double *velocity_delta_x;
    double *velocity_delta_y;
    double *velocity_delta_z;
    double *normal_x;
    double *normal_y;
    double *normal_z;
    double *boundary_density;
    double *boundary_grad_x;
    double *boundary_grad_y;
    double *boundary_grad_z;
    double *penetration;
    double *frame_start_particles;
    double *frame_start_bodies;

    double *body_ax;
    double *body_ay;
    double *body_az;
} Solver;

static double monotonic_seconds(void) {
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC, &value) != 0) {
        return 0.0;
    }
    return (double)value.tv_sec + 1.0e-9 * (double)value.tv_nsec;
}

static int solver_deadline_reached(Solver *solver) {
    if (atomic_load_explicit(&solver->deadline_exceeded, memory_order_relaxed)) return 1;
    if (monotonic_seconds() < solver->deadline) return 0;
    atomic_store_explicit(&solver->deadline_exceeded, 1, memory_order_relaxed);
    return 1;
}

static int clamp_int(int value, int lower, int upper) {
    if (value < lower) return lower;
    if (value > upper) return upper;
    return value;
}

static double clamp_double(double value, double lower, double upper) {
    if (value < lower) return lower;
    if (value > upper) return upper;
    return value;
}

static int finite3(double x, double y, double z) {
    return isfinite(x) && isfinite(y) && isfinite(z);
}

static void force_field_acceleration(
    const PhySimulation *input,
    uint32_t mask,
    double x,
    double y,
    double z,
    double time_value,
    double *ax,
    double *ay,
    double *az
) {
    for (int field_index = 0; field_index < input->field_count; ++field_index) {
        if ((mask & (UINT32_C(1) << (unsigned)field_index)) == 0u) continue;
        const PhyForceField *field = &input->fields[field_index];
        if (time_value < field->start_time || time_value >= field->end_time) continue;
        if (field->type == PHY_FIELD_UNIFORM) {
            *ax += field->vector[0];
            *ay += field->vector[1];
            *az += field->vector[2];
            continue;
        }
        double rx = x - field->origin[0];
        double ry = y - field->origin[1];
        double rz = z - field->origin[2];
        if (field->type == PHY_FIELD_VORTEX) {
            double axial = rx * field->vector[0] + ry * field->vector[1] + rz * field->vector[2];
            rx -= axial * field->vector[0];
            ry -= axial * field->vector[1];
            rz -= axial * field->vector[2];
        }
        double distance_squared = rx * rx + ry * ry + rz * rz;
        if (distance_squared <= 1.0e-24 || distance_squared >= field->radius * field->radius) continue;
        double distance = sqrt(distance_squared);
        double inverse_distance = 1.0 / distance;
        double dx = rx * inverse_distance;
        double dy = ry * inverse_distance;
        double dz = rz * inverse_distance;
        double falloff = 1.0 - distance / field->radius;
        if (field->type == PHY_FIELD_RADIAL) {
            double scale = field->strength * falloff;
            *ax += scale * dx;
            *ay += scale * dy;
            *az += scale * dz;
        } else {
            double tx = field->vector[1] * dz - field->vector[2] * dy;
            double ty = field->vector[2] * dx - field->vector[0] * dz;
            double tz = field->vector[0] * dy - field->vector[1] * dx;
            double tangent_length = sqrt(tx * tx + ty * ty + tz * tz);
            if (tangent_length > 1.0e-12) {
                double scale = field->strength * falloff / tangent_length;
                *ax += scale * tx;
                *ay += scale * ty;
                *az += scale * tz;
            }
            double inward = -field->secondary_strength * falloff;
            *ax += inward * dx;
            *ay += inward * dy;
            *az += inward * dz;
        }
    }
}

static void *worker_main(void *opaque) {
    WorkerArgument *argument = (WorkerArgument *)opaque;
    ThreadPool *pool = argument->pool;
    unsigned seen_generation = 0;
    for (;;) {
        pthread_mutex_lock(&pool->mutex);
        while (!pool->stopping && pool->generation == seen_generation) {
            pthread_cond_wait(&pool->start_condition, &pool->mutex);
        }
        if (pool->stopping) {
            pthread_mutex_unlock(&pool->mutex);
            return NULL;
        }
        seen_generation = pool->generation;
        ParallelJob job = pool->job;
        void *context = pool->job_context;
        int count = pool->item_count;
        int begin = (count * argument->worker_id) / pool->threads;
        int end = (count * (argument->worker_id + 1)) / pool->threads;
        pthread_mutex_unlock(&pool->mutex);

        job(context, begin, end);

        pthread_mutex_lock(&pool->mutex);
        pool->finished += 1;
        if (pool->finished == pool->threads - 1) {
            pthread_cond_signal(&pool->finish_condition);
        }
        pthread_mutex_unlock(&pool->mutex);
    }
}

static int pool_initialize(ThreadPool *pool, int requested_threads) {
    memset(pool, 0, sizeof(*pool));
    pool->threads = clamp_int(requested_threads, 1, 32);
    if (pthread_mutex_init(&pool->mutex, NULL) != 0 ||
        pthread_cond_init(&pool->start_condition, NULL) != 0 ||
        pthread_cond_init(&pool->finish_condition, NULL) != 0) {
        return 0;
    }
    if (pool->threads == 1) return 1;
    pool->handles = (pthread_t *)calloc((size_t)(pool->threads - 1), sizeof(pthread_t));
    pool->arguments = (WorkerArgument *)calloc((size_t)(pool->threads - 1), sizeof(WorkerArgument));
    if (!pool->handles || !pool->arguments) return 0;
    for (int index = 1; index < pool->threads; ++index) {
        WorkerArgument *argument = &pool->arguments[index - 1];
        argument->pool = pool;
        argument->worker_id = index;
        if (pthread_create(&pool->handles[index - 1], NULL, worker_main, argument) != 0) {
            pthread_mutex_lock(&pool->mutex);
            pool->stopping = 1;
            pthread_cond_broadcast(&pool->start_condition);
            pthread_mutex_unlock(&pool->mutex);
            for (int created = 0; created < pool->created; ++created) {
                pthread_join(pool->handles[created], NULL);
            }
            return 0;
        }
        pool->created += 1;
    }
    return 1;
}

static void pool_run(ThreadPool *pool, int item_count, ParallelJob job, void *context) {
    if (item_count <= 0) return;
    if (pool->threads <= 1) {
        job(context, 0, item_count);
        return;
    }
    pthread_mutex_lock(&pool->mutex);
    pool->item_count = item_count;
    pool->job = job;
    pool->job_context = context;
    pool->finished = 0;
    pool->generation += 1;
    pthread_cond_broadcast(&pool->start_condition);
    pthread_mutex_unlock(&pool->mutex);

    job(context, 0, item_count / pool->threads);

    pthread_mutex_lock(&pool->mutex);
    while (pool->finished < pool->threads - 1) {
        pthread_cond_wait(&pool->finish_condition, &pool->mutex);
    }
    pthread_mutex_unlock(&pool->mutex);
}

static void pool_destroy(ThreadPool *pool) {
    if (pool->created > 0) {
        pthread_mutex_lock(&pool->mutex);
        pool->stopping = 1;
        pthread_cond_broadcast(&pool->start_condition);
        pthread_mutex_unlock(&pool->mutex);
        for (int index = 0; index < pool->created; ++index) {
            pthread_join(pool->handles[index], NULL);
        }
    }
    free(pool->handles);
    free(pool->arguments);
    pthread_cond_destroy(&pool->finish_condition);
    pthread_cond_destroy(&pool->start_condition);
    pthread_mutex_destroy(&pool->mutex);
    memset(pool, 0, sizeof(*pool));
}

static uint64_t mix_u64(uint64_t value) {
    value ^= value >> 30;
    value *= UINT64_C(0xbf58476d1ce4e5b9);
    value ^= value >> 27;
    value *= UINT64_C(0x94d049bb133111eb);
    value ^= value >> 31;
    return value;
}

static uint64_t cell_hash(int64_t x, int64_t y, int64_t z) {
    uint64_t hx = mix_u64((uint64_t)x + UINT64_C(0x9e3779b97f4a7c15));
    uint64_t hy = mix_u64((uint64_t)y + UINT64_C(0x632be59bd9b4e019));
    uint64_t hz = mix_u64((uint64_t)z + UINT64_C(0x8cb92baa72f3d8dd));
    return hx ^ (hy << 1 | hy >> 63) ^ (hz << 7 | hz >> 57);
}

static size_t grid_slot(CompactGrid *grid, int64_t x, int64_t y, int64_t z, int create) {
    size_t mask = grid->slot_capacity - 1;
    size_t slot = (size_t)(cell_hash(x, y, z) & (uint64_t)mask);
    for (size_t probe = 0; probe < grid->slot_capacity; ++probe) {
        if (!grid->slot_used[slot]) {
            if (!create) return SIZE_MAX;
            grid->slot_used[slot] = 1;
            grid->slot_x[slot] = x;
            grid->slot_y[slot] = y;
            grid->slot_z[slot] = z;
            grid->slot_count[slot] = 0;
            return slot;
        }
        if (grid->slot_x[slot] == x && grid->slot_y[slot] == y && grid->slot_z[slot] == z) {
            return slot;
        }
        slot = (slot + 1) & mask;
    }
    return SIZE_MAX;
}

static int grid_initialize(CompactGrid *grid, int particle_count) {
    memset(grid, 0, sizeof(*grid));
    grid->particle_count = particle_count;
    size_t capacity = 64;
    size_t required = particle_count > 0 ? (size_t)particle_count * 4u : 64u;
    while (capacity < required) {
        if (capacity > SIZE_MAX / 2u) return 0;
        capacity *= 2u;
    }
    grid->slot_capacity = capacity;
    grid->slot_used = (uint8_t *)calloc(capacity, sizeof(uint8_t));
    grid->slot_x = (int64_t *)calloc(capacity, sizeof(int64_t));
    grid->slot_y = (int64_t *)calloc(capacity, sizeof(int64_t));
    grid->slot_z = (int64_t *)calloc(capacity, sizeof(int64_t));
    grid->slot_count = (int32_t *)calloc(capacity, sizeof(int32_t));
    grid->slot_start = (int32_t *)calloc(capacity, sizeof(int32_t));
    grid->slot_cursor = (int32_t *)calloc(capacity, sizeof(int32_t));
    size_t particle_capacity = particle_count > 0 ? (size_t)particle_count : 1u;
    grid->particle_x = (int64_t *)calloc(particle_capacity, sizeof(int64_t));
    grid->particle_y = (int64_t *)calloc(particle_capacity, sizeof(int64_t));
    grid->particle_z = (int64_t *)calloc(particle_capacity, sizeof(int64_t));
    grid->ordered_particles = (int32_t *)calloc(particle_capacity, sizeof(int32_t));
    grid->neighbor_offsets = (int32_t *)calloc(particle_capacity + 1u, sizeof(int32_t));
    return grid->slot_used && grid->slot_x && grid->slot_y && grid->slot_z &&
           grid->slot_count && grid->slot_start && grid->slot_cursor &&
           grid->particle_x && grid->particle_y && grid->particle_z &&
           grid->ordered_particles && grid->neighbor_offsets;
}

static void grid_destroy(CompactGrid *grid) {
    free(grid->slot_used);
    free(grid->slot_x);
    free(grid->slot_y);
    free(grid->slot_z);
    free(grid->slot_count);
    free(grid->slot_start);
    free(grid->slot_cursor);
    free(grid->particle_x);
    free(grid->particle_y);
    free(grid->particle_z);
    free(grid->ordered_particles);
    free(grid->neighbor_offsets);
    free(grid->neighbors);
    memset(grid, 0, sizeof(*grid));
}

static inline double cubic_kernel_distance(const Solver *solver, double distance) {
    if (distance >= solver->h) return 0.0;
    double q = distance / solver->h;
    if (q <= 0.5) {
        return solver->poly6_coefficient * (6.0 * q * q * q - 6.0 * q * q + 1.0);
    }
    double value = 1.0 - q;
    return solver->poly6_coefficient * 2.0 * value * value * value;
}

static inline double cubic_gradient_scale_distance(const Solver *solver, double distance) {
    if (distance <= 1.0e-12 || distance >= solver->h) return 0.0;
    double q = distance / solver->h;
    double derivative = q <= 0.5 ? 6.0 * q * (3.0 * q - 2.0)
                                 : -6.0 * (1.0 - q) * (1.0 - q);
    return solver->poly6_coefficient * derivative / (solver->h * distance);
}

static inline double poly6(const Solver *solver, double distance_squared) {
    return distance_squared >= solver->h2 ? 0.0 :
        cubic_kernel_distance(solver, sqrt(fmax(distance_squared, 0.0)));
}

static inline double spiky_gradient_scale(const Solver *solver, double distance_squared) {
    return distance_squared <= 1.0e-24 || distance_squared >= solver->h2 ? 0.0 :
        cubic_gradient_scale_distance(solver, sqrt(distance_squared));
}

static double cubic_first_moment_antiderivative_low(double q) {
    double q2 = q * q;
    double q4 = q2 * q2;
    double q5 = q4 * q;
    return 1.2 * q5 - 1.5 * q4 + 0.5 * q2;
}

static double cubic_second_moment_antiderivative_low(double q) {
    double q2 = q * q;
    double q3 = q2 * q;
    double q5 = q3 * q2;
    double q6 = q3 * q3;
    return q6 - 1.2 * q5 + q3 / 3.0;
}

static double cubic_first_moment_antiderivative_high(double q) {
    double q2 = q * q;
    double q3 = q2 * q;
    double q4 = q2 * q2;
    double q5 = q4 * q;
    return q2 - 2.0 * q3 + 1.5 * q4 - 0.4 * q5;
}

static double cubic_second_moment_antiderivative_high(double q) {
    double q2 = q * q;
    double q3 = q2 * q;
    double q4 = q2 * q2;
    double q5 = q4 * q;
    double q6 = q3 * q3;
    return (2.0 / 3.0) * q3 - 1.5 * q4 + 1.2 * q5 - q6 / 3.0;
}

/* Exact integral of the normalized cubic kernel over a planar solid
   half-space.  The result is a density-ratio contribution and its spatial
   derivative along the plane's outward normal. */
static void planar_halfspace_kernel(
    const Solver *solver,
    double signed_distance,
    double *density,
    double *normal_derivative
) {
    if (signed_distance >= solver->h) return;
    double q = clamp_double(signed_distance / solver->h, 0.0, 1.0);
    double first;
    double second;
    if (q < 0.5) {
        first = cubic_first_moment_antiderivative_low(0.5) -
                cubic_first_moment_antiderivative_low(q) +
                cubic_first_moment_antiderivative_high(1.0) -
                cubic_first_moment_antiderivative_high(0.5);
        second = cubic_second_moment_antiderivative_low(0.5) -
                 cubic_second_moment_antiderivative_low(q) +
                 cubic_second_moment_antiderivative_high(1.0) -
                 cubic_second_moment_antiderivative_high(0.5);
    } else {
        first = cubic_first_moment_antiderivative_high(1.0) -
                cubic_first_moment_antiderivative_high(q);
        second = cubic_second_moment_antiderivative_high(1.0) -
                 cubic_second_moment_antiderivative_high(q);
    }
    *density += 16.0 * (second - q * first);
    *normal_derivative += -16.0 * first / solver->h;
}

static void add_planar_halfspace(
    const Solver *solver,
    double x,
    double y,
    double z,
    double nx,
    double ny,
    double nz,
    double offset,
    double *density,
    double *gradient_x,
    double *gradient_y,
    double *gradient_z
) {
    double signed_distance = nx * x + ny * y + nz * z - offset;
    double derivative = 0.0;
    planar_halfspace_kernel(solver, signed_distance, density, &derivative);
    *gradient_x += derivative * nx;
    *gradient_y += derivative * ny;
    *gradient_z += derivative * nz;
}

static void planar_boundary_contribution(
    const Solver *solver,
    double x,
    double y,
    double z,
    double *density,
    double *gradient_x,
    double *gradient_y,
    double *gradient_z
) {
    const PhySimulation *input = solver->input;
    *density = 0.0;
    *gradient_x = 0.0;
    *gradient_y = 0.0;
    *gradient_z = 0.0;
    for (int index = 0; index < input->collider_count; ++index) {
        const PhyCollider *collider = &input->colliders[index];
        if (collider->type != PHY_COLLIDER_PLANE) continue;
        add_planar_halfspace(
            solver, x, y, z,
            collider->a[0], collider->a[1], collider->a[2], collider->b[0],
            density, gradient_x, gradient_y, gradient_z
        );
    }
}

static void neighbor_count_job(void *opaque, int begin, int end) {
    Solver *solver = (Solver *)opaque;
    CompactGrid *grid = &solver->grid;
    PhySimulation *input = solver->input;
    for (int index = begin; index < end; ++index) {
        if (solver_deadline_reached(solver)) return;
        int count = 0;
        unsigned deadline_counter = 0u;
        int64_t cell_x = grid->particle_x[index];
        int64_t cell_y = grid->particle_y[index];
        int64_t cell_z = grid->particle_z[index];
        for (int dz = -1; dz <= 1; ++dz) {
            for (int dy = -1; dy <= 1; ++dy) {
                for (int dx = -1; dx <= 1; ++dx) {
                    size_t slot = grid_slot(grid, cell_x + dx, cell_y + dy, cell_z + dz, 0);
                    if (slot == SIZE_MAX) continue;
                    int start = grid->slot_start[slot];
                    int stop = start + grid->slot_count[slot];
                    for (int cursor = start; cursor < stop; ++cursor) {
                        deadline_counter += 1u;
                        if ((deadline_counter & 255u) == 0u && solver_deadline_reached(solver)) return;
                        int other = grid->ordered_particles[cursor];
                        if (other == index) continue;
                        double rx = input->x[index] - input->x[other];
                        double ry = input->y[index] - input->y[other];
                        double rz = input->z[index] - input->z[other];
                        if (rx * rx + ry * ry + rz * rz < solver->h2) count += 1;
                    }
                }
            }
        }
        grid->neighbor_offsets[index + 1] = count;
    }
}

static void neighbor_fill_job(void *opaque, int begin, int end) {
    Solver *solver = (Solver *)opaque;
    CompactGrid *grid = &solver->grid;
    PhySimulation *input = solver->input;
    for (int index = begin; index < end; ++index) {
        if (solver_deadline_reached(solver)) return;
        int write = grid->neighbor_offsets[index];
        unsigned deadline_counter = 0u;
        int64_t cell_x = grid->particle_x[index];
        int64_t cell_y = grid->particle_y[index];
        int64_t cell_z = grid->particle_z[index];
        for (int dz = -1; dz <= 1; ++dz) {
            for (int dy = -1; dy <= 1; ++dy) {
                for (int dx = -1; dx <= 1; ++dx) {
                    size_t slot = grid_slot(grid, cell_x + dx, cell_y + dy, cell_z + dz, 0);
                    if (slot == SIZE_MAX) continue;
                    int start = grid->slot_start[slot];
                    int stop = start + grid->slot_count[slot];
                    for (int cursor = start; cursor < stop; ++cursor) {
                        deadline_counter += 1u;
                        if ((deadline_counter & 255u) == 0u && solver_deadline_reached(solver)) return;
                        int other = grid->ordered_particles[cursor];
                        if (other == index) continue;
                        double rx = input->x[index] - input->x[other];
                        double ry = input->y[index] - input->y[other];
                        double rz = input->z[index] - input->z[other];
                        if (rx * rx + ry * ry + rz * rz < solver->h2) {
                            grid->neighbors[write++] = other;
                        }
                    }
                }
            }
        }
    }
}

static int grid_rebuild(Solver *solver) {
    CompactGrid *grid = &solver->grid;
    PhySimulation *input = solver->input;
    int count = input->particle_count;
    if (count <= 0) return 1;
    memset(grid->slot_used, 0, grid->slot_capacity * sizeof(uint8_t));
    memset(grid->slot_count, 0, grid->slot_capacity * sizeof(int32_t));
    for (int index = 0; index < count; ++index) {
        if ((index & 255) == 0 && solver_deadline_reached(solver)) return 0;
        /* External coupling may change positions between steps. Reject cells
         * outside a safe integer range before the floating-to-integer cast;
         * leave room for the neighboring-cell +/-1 arithmetic as well. */
        double cell_x = input->x[index] / solver->h;
        double cell_y = input->y[index] / solver->h;
        double cell_z = input->z[index] / solver->h;
        if (!finite3(cell_x, cell_y, cell_z) || fabs(cell_x) > 0x1p62 ||
            fabs(cell_y) > 0x1p62 || fabs(cell_z) > 0x1p62) return 0;
        int64_t cx = (int64_t)floor(input->x[index] / solver->h);
        int64_t cy = (int64_t)floor(input->y[index] / solver->h);
        int64_t cz = (int64_t)floor(input->z[index] / solver->h);
        grid->particle_x[index] = cx;
        grid->particle_y[index] = cy;
        grid->particle_z[index] = cz;
        size_t slot = grid_slot(grid, cx, cy, cz, 1);
        if (slot == SIZE_MAX || grid->slot_count[slot] >= 512) return 0;
        grid->slot_count[slot] += 1;
    }
    int offset = 0;
    for (size_t slot = 0; slot < grid->slot_capacity; ++slot) {
        if (!grid->slot_used[slot]) continue;
        grid->slot_start[slot] = offset;
        grid->slot_cursor[slot] = 0;
        offset += grid->slot_count[slot];
    }
    for (int index = 0; index < count; ++index) {
        size_t slot = grid_slot(grid, grid->particle_x[index], grid->particle_y[index], grid->particle_z[index], 0);
        int target = grid->slot_start[slot] + grid->slot_cursor[slot]++;
        grid->ordered_particles[target] = index;
    }

    grid->neighbor_offsets[0] = 0;
    pool_run(&solver->pool, count, neighbor_count_job, solver);
    if (atomic_load_explicit(&solver->deadline_exceeded, memory_order_relaxed)) return 0;
    int max_neighbors = 0;
    for (int index = 0; index < count; ++index) {
        int local = grid->neighbor_offsets[index + 1];
        if (local > max_neighbors) max_neighbors = local;
        if (grid->neighbor_offsets[index] > INT32_MAX - local) return 0;
        grid->neighbor_offsets[index + 1] = grid->neighbor_offsets[index] + local;
    }
    int total_neighbors = grid->neighbor_offsets[count];
    size_t neighbor_link_limit = (size_t)count * 512u;
    if ((size_t)total_neighbors > neighbor_link_limit) return 0;
    if ((size_t)total_neighbors > grid->neighbor_capacity) {
        size_t capacity = grid->neighbor_capacity ? grid->neighbor_capacity : 1024u;
        while (capacity < (size_t)total_neighbors) {
            if (capacity > SIZE_MAX / 2u) return 0;
            capacity *= 2u;
        }
        int32_t *replacement = (int32_t *)realloc(grid->neighbors, capacity * sizeof(int32_t));
        if (!replacement) return 0;
        grid->neighbors = replacement;
        grid->neighbor_capacity = capacity;
    }
    pool_run(&solver->pool, count, neighbor_fill_job, solver);
    if (atomic_load_explicit(&solver->deadline_exceeded, memory_order_relaxed)) return 0;
    grid->max_neighbors = max_neighbors;
    if (max_neighbors > solver->diagnostics->max_neighbors) solver->diagnostics->max_neighbors = max_neighbors;
    return 1;
}

static int allocate_double_array(double **pointer, size_t count) {
    *pointer = (double *)calloc(count > 0 ? count : 1u, sizeof(double));
    return *pointer != NULL;
}

static int solver_initialize(Solver *solver, PhySimulation *input, PhyDiagnostics *diagnostics) {
    memset(solver, 0, sizeof(*solver));
    solver->input = input;
    solver->diagnostics = diagnostics;
    solver->velocity_decay_rate = -1.0;
    solver->h = 2.0 * input->spacing;
    solver->h2 = solver->h * solver->h;
    solver->volume = input->spacing * input->spacing * input->spacing;
    solver->particle_radius = 0.46 * input->spacing;
    /* Three-dimensional cubic spline kernel.  Density and gradient use the
       same kernel, which is required by the DFSPH pressure operator. */
    solver->poly6_coefficient = 8.0 / (M_PI * pow(solver->h, 3.0));
    solver->spiky_coefficient = 0.0;
    solver->self_kernel = poly6(solver, 0.0);
    solver->cohesion_coefficient = 32.0 / (M_PI * pow(solver->h, 9.0));
    solver->cohesion_constant = pow(solver->h, 6.0) / 64.0;
    for (int index = 0; index < input->field_count; ++index) {
        const PhyForceField *field = &input->fields[index];
        double magnitude = 0.0;
        if (field->type == PHY_FIELD_UNIFORM) {
            magnitude = sqrt(
                field->vector[0] * field->vector[0] +
                field->vector[1] * field->vector[1] +
                field->vector[2] * field->vector[2]
            );
        } else {
            magnitude = fabs(field->strength) + fabs(field->secondary_strength);
        }
        solver->maximum_field_acceleration += magnitude;
    }
    for (int index = 0; index < input->collider_count; ++index) {
        const PhyCollider *collider = &input->colliders[index];
        if (collider->type == PHY_COLLIDER_PLANE) {
            solver->boundary_adhesion_acceleration_bound += collider->c[0];
        }
    }
    if (!pool_initialize(&solver->pool, input->thread_count)) return 0;
    if (!grid_initialize(&solver->grid, input->particle_count)) return 0;
    size_t count = input->particle_count > 0 ? (size_t)input->particle_count : 1u;
    if (input->gravity_G > 0 && input->particle_count > 0) {
        if ((input->gravity_theta > 0 && !pg_init(&solver->gravity_tree, input->particle_count)) ||
            !allocate_double_array(&solver->gravity_ax, count) ||
            !allocate_double_array(&solver->gravity_ay, count) ||
            !allocate_double_array(&solver->gravity_az, count)) return 0;
    }
    return allocate_double_array(&solver->prev_x, count) &&
           allocate_double_array(&solver->prev_y, count) &&
           allocate_double_array(&solver->prev_z, count) &&
           allocate_double_array(&solver->density, count) &&
           allocate_double_array(&solver->factor, count) &&
           allocate_double_array(&solver->source, count) &&
           allocate_double_array(&solver->pressure, count) &&
           allocate_double_array(&solver->pressure_ax, count) &&
           allocate_double_array(&solver->pressure_ay, count) &&
           allocate_double_array(&solver->pressure_az, count) &&
           allocate_double_array(&solver->error, count) &&
           allocate_double_array(&solver->delta_x, count) &&
           allocate_double_array(&solver->delta_y, count) &&
           allocate_double_array(&solver->delta_z, count) &&
           allocate_double_array(&solver->velocity_delta_x, count) &&
           allocate_double_array(&solver->velocity_delta_y, count) &&
           allocate_double_array(&solver->velocity_delta_z, count) &&
           allocate_double_array(&solver->normal_x, count) &&
           allocate_double_array(&solver->normal_y, count) &&
           allocate_double_array(&solver->normal_z, count) &&
           allocate_double_array(&solver->boundary_density, count) &&
           allocate_double_array(&solver->boundary_grad_x, count) &&
           allocate_double_array(&solver->boundary_grad_y, count) &&
           allocate_double_array(&solver->boundary_grad_z, count) &&
           allocate_double_array(&solver->penetration, count) &&
           allocate_double_array(
               &solver->frame_start_particles,
               (size_t)(input->render_count > 0 ? input->render_count : 1) * 3u
           ) &&
           allocate_double_array(
               &solver->frame_start_bodies,
               (size_t)(input->body_count > 0 ? input->body_count : 1) * 3u
           ) &&
           allocate_double_array(&solver->body_ax, input->body_count > 0 ? (size_t)input->body_count : 1u) &&
           allocate_double_array(&solver->body_ay, input->body_count > 0 ? (size_t)input->body_count : 1u) &&
           allocate_double_array(&solver->body_az, input->body_count > 0 ? (size_t)input->body_count : 1u);
}

static void solver_destroy(Solver *solver) {
    pg_destroy(&solver->gravity_tree);
    free(solver->gravity_ax); free(solver->gravity_ay); free(solver->gravity_az);
    free(solver->prev_x);
    free(solver->prev_y);
    free(solver->prev_z);
    free(solver->density);
    free(solver->factor);
    free(solver->source);
    free(solver->pressure);
    free(solver->pressure_ax);
    free(solver->pressure_ay);
    free(solver->pressure_az);
    free(solver->error);
    free(solver->delta_x);
    free(solver->delta_y);
    free(solver->delta_z);
    free(solver->velocity_delta_x);
    free(solver->velocity_delta_y);
    free(solver->velocity_delta_z);
    free(solver->normal_x);
    free(solver->normal_y);
    free(solver->normal_z);
    free(solver->boundary_density);
    free(solver->boundary_grad_x);
    free(solver->boundary_grad_y);
    free(solver->boundary_grad_z);
    free(solver->penetration);
    free(solver->frame_start_particles);
    free(solver->frame_start_bodies);
    free(solver->body_ax);
    free(solver->body_ay);
    free(solver->body_az);
    grid_destroy(&solver->grid);
    pool_destroy(&solver->pool);
    memset(solver, 0, sizeof(*solver));
}

static void particle_gravity_job(void *opaque, int begin, int end) {
    Solver *s = opaque;
    PhySimulation *in = s->input;
    double gm = in->gravity_G * in->particle_gravity_density * s->volume;
    for (int i = begin; i < end; ++i) {
        if ((i & 7) == 0 && solver_deadline_reached(s)) return;
        double a[3];
        pg_acceleration(&s->gravity_tree, i, in->gravity_theta, in->softening, gm, a);
        s->gravity_ax[i] = a[0]; s->gravity_ay[i] = a[1]; s->gravity_az[i] = a[2];
    }
}

static int prepare_particle_gravity(Solver *s) {
    if (!s->gravity_ax) return 1;
    PhySimulation *in = s->input;
    if (!finite3(in->x[0], in->y[0], in->z[0])) return 0;
    if (in->gravity_theta > 0) pg_build(&s->gravity_tree, in->x, in->y, in->z);
    else pg_bind_positions(&s->gravity_tree, in->particle_count, in->x, in->y, in->z);
    pool_run(&s->pool, in->particle_count, particle_gravity_job, s);
    if (solver_deadline_reached(s)) return 0;
    double mean[3] = {0};
    for (int i = 0; i < in->particle_count; ++i) {
        mean[0] += s->gravity_ax[i] / in->particle_count;
        mean[1] += s->gravity_ay[i] / in->particle_count;
        mean[2] += s->gravity_az[i] / in->particle_count;
    }
    s->particle_gravity_maximum = 0;
    for (int i = 0; i < in->particle_count; ++i) {
        /* Equal inertial masses: remove the tree's net internal-force error.
         * This preserves linear, but not exact angular, momentum. */
        s->gravity_ax[i] -= mean[0]; s->gravity_ay[i] -= mean[1]; s->gravity_az[i] -= mean[2];
        double a = sqrt(s->gravity_ax[i]*s->gravity_ax[i] +
            s->gravity_ay[i]*s->gravity_ay[i] + s->gravity_az[i]*s->gravity_az[i]);
        if (!isfinite(a)) return 0;
        s->particle_gravity_maximum = fmax(s->particle_gravity_maximum, a);
    }
    return 1;
}

static void save_previous_and_apply_gravity_job(void *opaque, int begin, int end) {
    Solver *solver = (Solver *)opaque;
    PhySimulation *input = solver->input;
    double dt = solver->current_dt;
    for (int index = begin; index < end; ++index) {
        solver->prev_x[index] = input->x[index];
        solver->prev_y[index] = input->y[index];
        solver->prev_z[index] = input->z[index];
        double ax = input->gravity[0];
        double ay = input->gravity[1];
        double az = input->gravity[2];
        if (solver->gravity_ax) {
            ax += solver->gravity_ax[index]; ay += solver->gravity_ay[index]; az += solver->gravity_az[index];
        }
        if (input->field_count > 0) {
            force_field_acceleration(
                input, input->particle_field_mask[index],
                input->x[index], input->y[index], input->z[index],
                solver->current_time + 0.5 * dt, &ax, &ay, &az
            );
        }
        input->vx[index] += dt * ax;
        input->vy[index] += dt * ay;
        input->vz[index] += dt * az;
        if (input->material[index] == PHY_MATERIAL_SAND) {
            double strength = clamp_double(
                input->cohesion[index] * (1.0 - input->wetness[index]), 0.0, 0.35
            );
            if (strength > 0.0) {
                double omega = 50.0 * sqrt(strength);
                double denominator = 1.0 + 2.0 * omega * dt + omega * omega * dt * dt;
                double spring_scale = dt * omega * omega;
                input->vx[index] = (
                    input->vx[index] - spring_scale * (input->x[index] - input->anchor_x[index])
                ) / denominator;
                input->vy[index] = (
                    input->vy[index] - spring_scale * (input->y[index] - input->anchor_y[index])
                ) / denominator;
                input->vz[index] = (
                    input->vz[index] - spring_scale * (input->z[index] - input->anchor_z[index])
                ) / denominator;
            }
        }
    }
}

static void velocity_filter_job(void *opaque, int begin, int end) {
    Solver *solver = (Solver *)opaque;
    PhySimulation *input = solver->input;
    CompactGrid *grid = &solver->grid;
    double time_ratio = solver->current_dt / (1.0 / 90.0);
    for (int index = begin; index < end; ++index) {
        double update_x = 0.0;
        double update_y = 0.0;
        double update_z = 0.0;
        int start = grid->neighbor_offsets[index];
        int stop = grid->neighbor_offsets[index + 1];
        for (int cursor = start; cursor < stop; ++cursor) {
            int other = grid->neighbors[cursor];
            double rx = input->x[index] - input->x[other];
            double ry = input->y[index] - input->y[other];
            double rz = input->z[index] - input->z[other];
            double weight = solver->volume * poly6(solver, rx * rx + ry * ry + rz * rz);
            double coefficient = 0.0;
            if (input->material[index] == PHY_MATERIAL_WATER &&
                input->material[other] == PHY_MATERIAL_WATER) {
                double blend = -expm1(-input->viscosity[index] * time_ratio);
                coefficient = blend * weight;
            } else if (input->material[index] != input->material[other]) {
                double blend = -expm1(-input->water_sand_drag * time_ratio);
                coefficient = blend * fmin(weight * 8.0, 0.08);
            }
            update_x += coefficient * (input->vx[other] - input->vx[index]);
            update_y += coefficient * (input->vy[other] - input->vy[index]);
            update_z += coefficient * (input->vz[other] - input->vz[index]);
        }
        solver->velocity_delta_x[index] = update_x;
        solver->velocity_delta_y[index] = update_y;
        solver->velocity_delta_z[index] = update_z;
    }
}

static void apply_velocity_filter_job(void *opaque, int begin, int end) {
    Solver *solver = (Solver *)opaque;
    PhySimulation *input = solver->input;
    for (int index = begin; index < end; ++index) {
        input->vx[index] += solver->velocity_delta_x[index];
        input->vy[index] += solver->velocity_delta_y[index];
        input->vz[index] += solver->velocity_delta_z[index];
    }
}

static void density_factor_job(void *opaque, int begin, int end) {
    Solver *solver = (Solver *)opaque;
    PhySimulation *input = solver->input;
    CompactGrid *grid = &solver->grid;
    for (int index = begin; index < end; ++index) {
        if (input->material[index] != PHY_MATERIAL_WATER) {
            solver->density[index] = 0.0;
            solver->factor[index] = 0.0;
            continue;
        }
        double density = solver->volume * solver->self_kernel;
        double gradient_i_x = 0.0;
        double gradient_i_y = 0.0;
        double gradient_i_z = 0.0;
        double sum_gradient_squared = 0.0;
        double boundary_density = 0.0;
        double boundary_gradient_x = 0.0;
        double boundary_gradient_y = 0.0;
        double boundary_gradient_z = 0.0;
        planar_boundary_contribution(
            solver,
            input->x[index], input->y[index], input->z[index],
            &boundary_density,
            &boundary_gradient_x, &boundary_gradient_y, &boundary_gradient_z
        );
        int start = grid->neighbor_offsets[index];
        int stop = grid->neighbor_offsets[index + 1];
        for (int cursor = start; cursor < stop; ++cursor) {
            int other = grid->neighbors[cursor];
            if (input->material[other] != PHY_MATERIAL_WATER) continue;
            double rx = input->x[index] - input->x[other];
            double ry = input->y[index] - input->y[other];
            double rz = input->z[index] - input->z[other];
            double distance_squared = rx * rx + ry * ry + rz * rz;
            double distance = sqrt(distance_squared);
            density += solver->volume * cubic_kernel_distance(solver, distance);
            double gradient_scale = solver->volume * cubic_gradient_scale_distance(solver, distance);
            double gx = gradient_scale * rx;
            double gy = gradient_scale * ry;
            double gz = gradient_scale * rz;
            gradient_i_x += gx;
            gradient_i_y += gy;
            gradient_i_z += gz;
            sum_gradient_squared += gx * gx + gy * gy + gz * gz;
        }
        density += boundary_density;
        gradient_i_x += boundary_gradient_x;
        gradient_i_y += boundary_gradient_y;
        gradient_i_z += boundary_gradient_z;
        sum_gradient_squared += gradient_i_x * gradient_i_x +
                                gradient_i_y * gradient_i_y +
                                gradient_i_z * gradient_i_z;
        solver->density[index] = density;
        solver->factor[index] = sum_gradient_squared > 1.0e-18 ? 1.0 / sum_gradient_squared : 0.0;
        solver->boundary_density[index] = boundary_density;
        solver->boundary_grad_x[index] = boundary_gradient_x;
        solver->boundary_grad_y[index] = boundary_gradient_y;
        solver->boundary_grad_z[index] = boundary_gradient_z;
    }
}

static inline double cohesion_kernel(const Solver *solver, double distance) {
    if (distance <= 0.0 || distance > solver->h) return 0.0;
    double h = solver->h;
    double r3 = distance * distance * distance;
    double value = (h - distance) * (h - distance) * (h - distance) * r3;
    if (distance > 0.5 * h) return solver->cohesion_coefficient * value;
    /* Akinci et al. place both terms inside the normalized kernel.  Keeping
       the constant outside the normalization removes almost the complete
       short-range repulsive branch and lets surface particles pair/clump. */
    return solver->cohesion_coefficient * (2.0 * value - solver->cohesion_constant);
}

static void surface_normal_job(void *opaque, int begin, int end) {
    Solver *solver = (Solver *)opaque;
    PhySimulation *input = solver->input;
    CompactGrid *grid = &solver->grid;
    for (int index = begin; index < end; ++index) {
        double nx = 0.0;
        double ny = 0.0;
        double nz = 0.0;
        if (input->material[index] == PHY_MATERIAL_WATER && input->surface_tension[index] > 0.0) {
            int start = grid->neighbor_offsets[index];
            int stop = grid->neighbor_offsets[index + 1];
            for (int cursor = start; cursor < stop; ++cursor) {
                int other = grid->neighbors[cursor];
                if (input->material[other] != PHY_MATERIAL_WATER) continue;
                double rx = input->x[index] - input->x[other];
                double ry = input->y[index] - input->y[other];
                double rz = input->z[index] - input->z[other];
                double distance_squared = rx * rx + ry * ry + rz * rz;
                double density = fmax(solver->density[other], 0.1);
                double scale = solver->h * solver->volume *
                               spiky_gradient_scale(solver, distance_squared) / density;
                nx += scale * rx;
                ny += scale * ry;
                nz += scale * rz;
            }
        }
        solver->normal_x[index] = nx;
        solver->normal_y[index] = ny;
        solver->normal_z[index] = nz;
    }
}

static void surface_force_job(void *opaque, int begin, int end) {
    Solver *solver = (Solver *)opaque;
    PhySimulation *input = solver->input;
    CompactGrid *grid = &solver->grid;
    double particle_mass = 1000.0 * solver->volume;
    double dt = solver->current_dt;
    for (int index = begin; index < end; ++index) {
        double ax = 0.0;
        double ay = 0.0;
        double az = 0.0;
        double tension = input->surface_tension[index];
        if (input->material[index] == PHY_MATERIAL_WATER && tension > 0.0) {
            int start = grid->neighbor_offsets[index];
            int stop = grid->neighbor_offsets[index + 1];
            for (int cursor = start; cursor < stop; ++cursor) {
                int other = grid->neighbors[cursor];
                if (input->material[other] != PHY_MATERIAL_WATER) continue;
                double rx = input->x[index] - input->x[other];
                double ry = input->y[index] - input->y[other];
                double rz = input->z[index] - input->z[other];
                double distance_squared = rx * rx + ry * ry + rz * rz;
                if (distance_squared <= 1.0e-18) continue;
                double distance = sqrt(distance_squared);
                double pair_tension = 0.5 * (tension + input->surface_tension[other]);
                double density_scale = 2.0 / fmax(solver->density[index] + solver->density[other], 0.2);
                double cohesion = -pair_tension * particle_mass * cohesion_kernel(solver, distance) / distance;
                ax += density_scale * (
                    cohesion * rx - pair_tension * (solver->normal_x[index] - solver->normal_x[other])
                );
                ay += density_scale * (
                    cohesion * ry - pair_tension * (solver->normal_y[index] - solver->normal_y[other])
                );
                az += density_scale * (
                    cohesion * rz - pair_tension * (solver->normal_z[index] - solver->normal_z[other])
                );
            }
        }
        if (input->material[index] == PHY_MATERIAL_WATER) {
            for (int collider_index = 0; collider_index < input->collider_count; ++collider_index) {
                const PhyCollider *collider = &input->colliders[collider_index];
                double adhesion = collider->c[0];
                if (collider->type != PHY_COLLIDER_PLANE || adhesion <= 0.0) continue;
                double signed_distance =
                    collider->a[0] * input->x[index] +
                    collider->a[1] * input->y[index] +
                    collider->a[2] * input->z[index] - collider->b[0];
                double surface_gap = signed_distance - solver->particle_radius;
                if (surface_gap >= solver->h) continue;
                double proximity = 1.0 - clamp_double(surface_gap / solver->h, 0.0, 1.0);
                double profile = proximity * proximity * (3.0 - 2.0 * proximity);
                ax -= adhesion * profile * collider->a[0];
                ay -= adhesion * profile * collider->a[1];
                az -= adhesion * profile * collider->a[2];
            }
        }
        solver->velocity_delta_x[index] = dt * ax;
        solver->velocity_delta_y[index] = dt * ay;
        solver->velocity_delta_z[index] = dt * az;
    }
}

static void pressure_source_job(void *opaque, int begin, int end) {
    Solver *solver = (Solver *)opaque;
    PhySimulation *input = solver->input;
    CompactGrid *grid = &solver->grid;
    double dt = solver->current_dt;
    for (int index = begin; index < end; ++index) {
        if (input->material[index] != PHY_MATERIAL_WATER) {
            solver->source[index] = 0.0;
            solver->pressure[index] = 0.0;
            continue;
        }
        double derivative = 0.0;
        int water_neighbors = 0;
        int start = grid->neighbor_offsets[index];
        int stop = grid->neighbor_offsets[index + 1];
        for (int cursor = start; cursor < stop; ++cursor) {
            int other = grid->neighbors[cursor];
            if (input->material[other] != PHY_MATERIAL_WATER) continue;
            water_neighbors += 1;
            double rx = input->x[index] - input->x[other];
            double ry = input->y[index] - input->y[other];
            double rz = input->z[index] - input->z[other];
            double distance_squared = rx * rx + ry * ry + rz * rz;
            double gradient_scale = solver->volume * spiky_gradient_scale(solver, distance_squared);
            derivative += gradient_scale * (
                (input->vx[index] - input->vx[other]) * rx +
                (input->vy[index] - input->vy[other]) * ry +
                (input->vz[index] - input->vz[other]) * rz
            );
        }
        derivative += input->vx[index] * solver->boundary_grad_x[index] +
                      input->vy[index] * solver->boundary_grad_y[index] +
                      input->vz[index] * solver->boundary_grad_z[index];
        if (solver->pressure_mode == 0) {
            /* Free-surface particles have insufficient support for a reliable
               divergence estimate, as in the reference DFSPH method. */
            int boundary_neighbor_equivalent = (int)lrint(40.0 * solver->boundary_density[index]);
            if (water_neighbors + boundary_neighbor_equivalent < 20) derivative = 0.0;
            solver->source[index] = derivative;
            solver->pressure[index] = fmax(derivative, 0.0) * solver->factor[index] / dt;
        } else {
            double advected_density = solver->density[index] + dt * derivative;
            solver->source[index] = advected_density;
            solver->pressure[index] = fmax(advected_density - 1.0, 0.0) *
                                      solver->factor[index] / (dt * dt);
        }
    }
}

static void pressure_acceleration_job(void *opaque, int begin, int end) {
    Solver *solver = (Solver *)opaque;
    PhySimulation *input = solver->input;
    CompactGrid *grid = &solver->grid;
    for (int index = begin; index < end; ++index) {
        double ax = 0.0;
        double ay = 0.0;
        double az = 0.0;
        if (input->material[index] == PHY_MATERIAL_WATER) {
            int start = grid->neighbor_offsets[index];
            int stop = grid->neighbor_offsets[index + 1];
            for (int cursor = start; cursor < stop; ++cursor) {
                int other = grid->neighbors[cursor];
                if (input->material[other] != PHY_MATERIAL_WATER) continue;
                double rx = input->x[index] - input->x[other];
                double ry = input->y[index] - input->y[other];
                double rz = input->z[index] - input->z[other];
                double distance_squared = rx * rx + ry * ry + rz * rz;
                double scale = -solver->volume *
                    (solver->pressure[index] + solver->pressure[other]) *
                    spiky_gradient_scale(solver, distance_squared);
                ax += scale * rx;
                ay += scale * ry;
                az += scale * rz;
            }
            ax -= solver->pressure[index] * solver->boundary_grad_x[index];
            ay -= solver->pressure[index] * solver->boundary_grad_y[index];
            az -= solver->pressure[index] * solver->boundary_grad_z[index];
        }
        solver->pressure_ax[index] = ax;
        solver->pressure_ay[index] = ay;
        solver->pressure_az[index] = az;
    }
}

static void pressure_update_job(void *opaque, int begin, int end) {
    Solver *solver = (Solver *)opaque;
    PhySimulation *input = solver->input;
    CompactGrid *grid = &solver->grid;
    double dt = solver->current_dt;
    double time_scale = solver->pressure_mode == 0 ? dt : dt * dt;
    double inverse_scale = 1.0 / time_scale;
    for (int index = begin; index < end; ++index) {
        if (input->material[index] != PHY_MATERIAL_WATER) {
            solver->error[index] = 0.0;
            continue;
        }
        double aij = 0.0;
        int start = grid->neighbor_offsets[index];
        int stop = grid->neighbor_offsets[index + 1];
        for (int cursor = start; cursor < stop; ++cursor) {
            int other = grid->neighbors[cursor];
            if (input->material[other] != PHY_MATERIAL_WATER) continue;
            double rx = input->x[index] - input->x[other];
            double ry = input->y[index] - input->y[other];
            double rz = input->z[index] - input->z[other];
            double distance_squared = rx * rx + ry * ry + rz * rz;
            double gradient_scale = solver->volume * spiky_gradient_scale(solver, distance_squared);
            aij += gradient_scale * (
                (solver->pressure_ax[index] - solver->pressure_ax[other]) * rx +
                (solver->pressure_ay[index] - solver->pressure_ay[other]) * ry +
                (solver->pressure_az[index] - solver->pressure_az[other]) * rz
            );
        }
        aij += solver->pressure_ax[index] * solver->boundary_grad_x[index] +
                solver->pressure_ay[index] * solver->boundary_grad_y[index] +
                solver->pressure_az[index] * solver->boundary_grad_z[index];
        aij *= time_scale;
        double source_term = solver->pressure_mode == 0 ? -solver->source[index]
                                                        : 1.0 - solver->source[index];
        double residual = fmin(source_term - aij, 0.0);
        solver->pressure[index] = fmax(
            solver->pressure[index] - 0.5 * (source_term - aij) * solver->factor[index] * inverse_scale,
            0.0
        );
        solver->error[index] = -residual;
    }
}

static void apply_pressure_velocity_job(void *opaque, int begin, int end) {
    Solver *solver = (Solver *)opaque;
    PhySimulation *input = solver->input;
    double dt = solver->current_dt;
    for (int index = begin; index < end; ++index) {
        if (input->material[index] != PHY_MATERIAL_WATER) continue;
        input->vx[index] += dt * solver->pressure_ax[index];
        input->vy[index] += dt * solver->pressure_ay[index];
        input->vz[index] += dt * solver->pressure_az[index];
    }
}

static double solve_pressure(Solver *solver, int mode, int maximum_iterations, int *iterations_used) {
    PhySimulation *input = solver->input;
    solver->pressure_mode = mode;
    pool_run(&solver->pool, input->particle_count, pressure_source_job, solver);
    double mean_error = 0.0;
    int water_count = 0;
    for (int index = 0; index < input->particle_count; ++index) {
        if (input->material[index] == PHY_MATERIAL_WATER) water_count += 1;
    }
    int minimum_iterations = mode == 0 ? 1 : 2;
    double threshold = mode == 0 ? 0.001 / solver->current_dt : 0.0001;
    int iteration = 0;
    for (; iteration < maximum_iterations; ++iteration) {
        pool_run(&solver->pool, input->particle_count, pressure_acceleration_job, solver);
        pool_run(&solver->pool, input->particle_count, pressure_update_job, solver);
        double total_error = 0.0;
        double maximum_error = 0.0;
        for (int index = 0; index < input->particle_count; ++index) {
            if (input->material[index] == PHY_MATERIAL_WATER) {
                total_error += solver->error[index];
                if (solver->error[index] > maximum_error) maximum_error = solver->error[index];
            }
        }
        mean_error = total_error / (double)(water_count > 0 ? water_count : 1);
        if (mode == 0) {
            if (maximum_error > solver->diagnostics->peak_max_divergence_error) {
                solver->diagnostics->peak_max_divergence_error = maximum_error;
            }
        } else if (maximum_error > solver->diagnostics->peak_max_density_error) {
            solver->diagnostics->peak_max_density_error = maximum_error;
        }
        if (iteration + 1 >= minimum_iterations && mean_error <= threshold) {
            iteration += 1;
            break;
        }
    }
    pool_run(&solver->pool, input->particle_count, pressure_acceleration_job, solver);
    pool_run(&solver->pool, input->particle_count, apply_pressure_velocity_job, solver);
    *iterations_used = iteration;
    return mean_error;
}

static void integrate_positions_job(void *opaque, int begin, int end) {
    Solver *solver = (Solver *)opaque;
    PhySimulation *input = solver->input;
    double dt = solver->current_dt;
    for (int index = begin; index < end; ++index) {
        input->x[index] += dt * input->vx[index];
        input->y[index] += dt * input->vy[index];
        input->z[index] += dt * input->vz[index];
    }
}

static void contact_delta_job(void *opaque, int begin, int end) {
    Solver *solver = (Solver *)opaque;
    PhySimulation *input = solver->input;
    CompactGrid *grid = &solver->grid;
    double solid_minimum_distance = 2.0 * solver->particle_radius;
    double water_minimum_distance = 0.82 * input->spacing;
    for (int index = begin; index < end; ++index) {
        double correction_x = 0.0;
        double correction_y = 0.0;
        double correction_z = 0.0;
        int touches_water = 0;
        int start = grid->neighbor_offsets[index];
        int stop = grid->neighbor_offsets[index + 1];
        for (int cursor = start; cursor < stop; ++cursor) {
            int other = grid->neighbors[cursor];
            int water_pair = input->material[index] == PHY_MATERIAL_WATER &&
                             input->material[other] == PHY_MATERIAL_WATER;
            double minimum_distance = water_pair ? water_minimum_distance : solid_minimum_distance;
            double minimum_squared = minimum_distance * minimum_distance;
            double rx = input->x[index] - input->x[other];
            double ry = input->y[index] - input->y[other];
            double rz = input->z[index] - input->z[other];
            double distance_squared = rx * rx + ry * ry + rz * rz;
            if (distance_squared >= minimum_squared) continue;
            double distance = sqrt(distance_squared);
            double scale = 0.0;
            if (distance <= 1.0e-12) {
                /* A deterministic pair normal avoids an x-axis streak when
                   a boundary projection puts many particles at one point. */
                uint64_t lo = (uint64_t)(index < other ? index : other) + UINT64_C(1);
                uint64_t hi = (uint64_t)(index < other ? other : index) + UINT64_C(1);
                uint64_t bits = lo * UINT64_C(0x9e3779b97f4a7c15) ^
                                hi * UINT64_C(0xbf58476d1ce4e5b9);
                double sign = index < other ? -1.0 : 1.0;
                rx = sign * ((double)((bits >> 0u) & UINT64_C(1023)) / 511.5 - 1.0);
                ry = sign * ((double)((bits >> 10u) & UINT64_C(1023)) / 511.5 - 1.0);
                rz = sign * ((double)((bits >> 20u) & UINT64_C(1023)) / 511.5 - 1.0);
                double direction_length = sqrt(rx * rx + ry * ry + rz * rz);
                if (direction_length <= 1.0e-12) {
                    rx = sign;
                    ry = 0.0;
                    rz = 0.0;
                    direction_length = 1.0;
                }
                rx /= direction_length;
                ry /= direction_length;
                rz /= direction_length;
                scale = 0.5 * minimum_distance;
            } else {
                scale = 0.5 * (minimum_distance - distance) / distance;
            }
            correction_x += scale * rx;
            correction_y += scale * ry;
            correction_z += scale * rz;
            if (input->material[index] == PHY_MATERIAL_SAND &&
                input->material[other] == PHY_MATERIAL_WATER) {
                touches_water = 1;
            }
        }
        if (touches_water) {
            input->wetness[index] = clamp_double(
                input->wetness[index] + input->wetting_rate * solver->current_dt /
                    (double)solver->contact_iterations,
                0.0, 1.0
            );
        }
        if (input->material[index] == PHY_MATERIAL_WATER) {
            double correction_length = sqrt(
                correction_x * correction_x +
                correction_y * correction_y +
                correction_z * correction_z
            );
            double maximum_correction = 0.18 * input->spacing;
            if (correction_length > maximum_correction) {
                double scale = maximum_correction / correction_length;
                correction_x *= scale;
                correction_y *= scale;
                correction_z *= scale;
            }
        }
        solver->delta_x[index] = correction_x;
        solver->delta_y[index] = correction_y;
        solver->delta_z[index] = correction_z;
    }
}

static double project_plane(double *x, double *y, double *z, double radius, const PhyCollider *collider) {
    double signed_distance = collider->a[0] * *x + collider->a[1] * *y + collider->a[2] * *z - collider->b[0];
    double penetration = radius - signed_distance;
    if (penetration > 0.0) {
        *x += collider->a[0] * penetration;
        *y += collider->a[1] * penetration;
        *z += collider->a[2] * penetration;
        return penetration;
    }
    return 0.0;
}

static double project_sphere(double *x, double *y, double *z, double radius, const PhyCollider *collider) {
    double rx = *x - collider->a[0];
    double ry = *y - collider->a[1];
    double rz = *z - collider->a[2];
    double distance = sqrt(rx * rx + ry * ry + rz * rz);
    double minimum = radius + collider->b[0];
    if (distance < minimum) {
        if (distance <= 1.0e-12) {
            *y += minimum;
            return minimum;
        }
        double penetration = minimum - distance;
        double scale = penetration / distance;
        *x += rx * scale;
        *y += ry * scale;
        *z += rz * scale;
        return penetration;
    }
    return 0.0;
}

static double project_box(
    double *x,
    double *y,
    double *z,
    double radius,
    const PhyCollider *collider,
    double previous_x,
    double previous_y,
    double previous_z
) {
    double local[3] = {*x - collider->a[0], *y - collider->a[1], *z - collider->a[2]};
    double previous[3] = {
        previous_x - collider->a[0],
        previous_y - collider->a[1],
        previous_z - collider->a[2]
    };
    double half[3] = {
        0.5 * collider->b[0] + radius,
        0.5 * collider->b[1] + radius,
        0.5 * collider->b[2] + radius
    };
    int previous_inside =
        fabs(previous[0]) < half[0] &&
        fabs(previous[1]) < half[1] &&
        fabs(previous[2]) < half[2];
    if (previous_inside) {
        double previous_face_distance[3] = {
            half[0] - fabs(previous[0]),
            half[1] - fabs(previous[1]),
            half[2] - fabs(previous[2])
        };
        int axis = previous_face_distance[1] < previous_face_distance[0] ? 1 : 0;
        if (previous_face_distance[2] < previous_face_distance[axis]) axis = 2;
        double sign = previous[axis] >= 0.0 ? 1.0 : -1.0;
        double direction = local[axis] - previous[axis];
        if (direction * sign <= 0.0) {
            double epsilon = fmax(1.0e-12, radius * 1.0e-9);
            double target = sign * (half[axis] + epsilon);
            double *coordinate = axis == 0 ? x : (axis == 1 ? y : z);
            double world_target = collider->a[axis] + target;
            double correction = fabs(*coordinate - world_target);
            *coordinate = world_target;
            return correction;
        }
    }
    if (!previous_inside) {
        double direction[3] = {
            local[0] - previous[0],
            local[1] - previous[1],
            local[2] - previous[2]
        };
        double entry = 0.0;
        double exit_time = 1.0;
        int hit_axis = -1;
        double hit_sign = 0.0;
        int intersects = 1;
        for (int axis = 0; axis < 3; ++axis) {
            if (fabs(direction[axis]) <= 1.0e-15) {
                if (fabs(previous[axis]) >= half[axis]) {
                    intersects = 0;
                    break;
                }
                continue;
            }
            double near_time = (-half[axis] - previous[axis]) / direction[axis];
            double far_time = (half[axis] - previous[axis]) / direction[axis];
            double normal_sign = direction[axis] > 0.0 ? -1.0 : 1.0;
            if (near_time > far_time) {
                double swap = near_time;
                near_time = far_time;
                far_time = swap;
            }
            if (near_time > entry || (hit_axis < 0 && near_time >= entry - 1.0e-12)) {
                entry = near_time;
                hit_axis = axis;
                hit_sign = normal_sign;
            }
            if (far_time < exit_time) exit_time = far_time;
            if (entry > exit_time) {
                intersects = 0;
                break;
            }
        }
        if (intersects && hit_axis >= 0 && entry >= -1.0e-12 &&
            entry <= 1.0 + 1.0e-12 && exit_time >= 0.0) {
            entry = clamp_double(entry, 0.0, 1.0);
            double remaining_normal = direction[hit_axis] * (1.0 - entry) * hit_sign;
            if (remaining_normal < 0.0) {
                double epsilon = fmax(1.0e-12, radius * 1.0e-9);
                double correction = -remaining_normal + epsilon;
                double *coordinate = hit_axis == 0 ? x : (hit_axis == 1 ? y : z);
                *coordinate -= remaining_normal * hit_sign;
                *coordinate += epsilon * hit_sign;
                return correction;
            }
        }
    }
    if (fabs(local[0]) >= half[0] || fabs(local[1]) >= half[1] || fabs(local[2]) >= half[2]) return 0.0;
    double distance[3] = {half[0] - fabs(local[0]), half[1] - fabs(local[1]), half[2] - fabs(local[2])};
    int axis = distance[1] < distance[0] ? 1 : 0;
    if (distance[2] < distance[axis]) axis = 2;
    double sign = local[axis] >= 0.0 ? 1.0 : -1.0;
    if (axis == 0) *x += sign * distance[axis];
    else if (axis == 1) *y += sign * distance[axis];
    else *z += sign * distance[axis];
    return distance[axis];
}

static double project_capsule(double *x, double *y, double *z, double radius, const PhyCollider *collider) {
    double sx = collider->b[0] - collider->a[0];
    double sy = collider->b[1] - collider->a[1];
    double sz = collider->b[2] - collider->a[2];
    double segment_squared = sx * sx + sy * sy + sz * sz;
    double px = *x - collider->a[0];
    double py = *y - collider->a[1];
    double pz = *z - collider->a[2];
    double amount = clamp_double(
        (px * sx + py * sy + pz * sz) / fmax(segment_squared, 1.0e-24),
        0.0, 1.0
    );
    double rx = *x - (collider->a[0] + amount * sx);
    double ry = *y - (collider->a[1] + amount * sy);
    double rz = *z - (collider->a[2] + amount * sz);
    double distance = sqrt(rx * rx + ry * ry + rz * rz);
    double minimum = radius + collider->c[0];
    if (distance >= minimum) return 0.0;
    double penetration = minimum - distance;
    if (distance <= 1.0e-12) {
        rx = 1.0;
        ry = 0.0;
        rz = 0.0;
        distance = 1.0;
        penetration = minimum;
    }
    double scale = penetration / distance;
    *x += scale * rx;
    *y += scale * ry;
    *z += scale * rz;
    return penetration;
}

static void apply_positional_friction(
    double *x,
    double *y,
    double *z,
    double before_x,
    double before_y,
    double before_z,
    double previous_x,
    double previous_y,
    double previous_z,
    double normal_correction,
    double friction
) {
    if (normal_correction <= 1.0e-15 || friction <= 0.0) return;
    double nx = (*x - before_x) / normal_correction;
    double ny = (*y - before_y) / normal_correction;
    double nz = (*z - before_z) / normal_correction;
    double dx = *x - previous_x;
    double dy = *y - previous_y;
    double dz = *z - previous_z;
    double normal_displacement = dx * nx + dy * ny + dz * nz;
    double tx = dx - normal_displacement * nx;
    double ty = dy - normal_displacement * ny;
    double tz = dz - normal_displacement * nz;
    double tangent_length = sqrt(tx * tx + ty * ty + tz * tz);
    if (tangent_length <= 1.0e-15) return;
    double amount = fmin(1.0, friction * normal_correction / tangent_length);
    *x -= amount * tx;
    *y -= amount * ty;
    *z -= amount * tz;
}

static void apply_contacts_and_boundaries_job(void *opaque, int begin, int end) {
    Solver *solver = (Solver *)opaque;
    PhySimulation *input = solver->input;
    double radius = solver->particle_radius;
    for (int index = begin; index < end; ++index) {
        double x = input->x[index] + solver->delta_x[index];
        double y = input->y[index] + solver->delta_y[index];
        double z = input->z[index] + solver->delta_z[index];
        double maximum = 0.0;
        for (int collider_index = 0; collider_index < input->collider_count; ++collider_index) {
            const PhyCollider *collider = &input->colliders[collider_index];
            double before_x = x;
            double before_y = y;
            double before_z = z;
            double correction = 0.0;
            if (collider->type == PHY_COLLIDER_PLANE) correction = project_plane(&x, &y, &z, radius, collider);
            else if (collider->type == PHY_COLLIDER_SPHERE) correction = project_sphere(&x, &y, &z, radius, collider);
            else if (collider->type == PHY_COLLIDER_BOX) correction = project_box(
                &x, &y, &z, radius, collider,
                solver->prev_x[index], solver->prev_y[index], solver->prev_z[index]
            );
            else if (collider->type == PHY_COLLIDER_CAPSULE) correction = project_capsule(&x, &y, &z, radius, collider);
            apply_positional_friction(
                &x, &y, &z,
                before_x, before_y, before_z,
                solver->prev_x[index], solver->prev_y[index], solver->prev_z[index],
                correction, collider->friction
            );
            if (correction > maximum) maximum = correction;
        }
        for (int axis = 0; axis < 3; ++axis) {
            double *value = axis == 0 ? &x : (axis == 1 ? &y : &z);
            double lower = input->bounds_min[axis] + radius;
            double upper = input->bounds_max[axis] - radius;
            double before = *value;
            *value = clamp_double(*value, lower, upper);
            double correction = fabs(before - *value);
            if (correction > maximum) maximum = correction;
        }
        input->x[index] = x;
        input->y[index] = y;
        input->z[index] = z;
        solver->penetration[index] = maximum;
    }
}

static void reconstruct_velocity_job(void *opaque, int begin, int end) {
    Solver *solver = (Solver *)opaque;
    PhySimulation *input = solver->input;
    double inverse_dt = 1.0 / solver->current_dt;
    /* Viscosity already damps relative motion.  Retain only a very small
       global guard so splashes and vortices do not lose most of their speed. */
    double water_damping = solver->velocity_decay_rate < 0 ?
        exp(log(0.9995) * solver->current_dt / (1.0 / 90.0)) :
        exp(-solver->velocity_decay_rate * solver->current_dt);
    double sand_scale = solver->current_dt / (1.0 / 90.0);
    for (int index = begin; index < end; ++index) {
        input->vx[index] = (input->x[index] - solver->prev_x[index]) * inverse_dt;
        input->vy[index] = (input->y[index] - solver->prev_y[index]) * inverse_dt;
        input->vz[index] = (input->z[index] - solver->prev_z[index]) * inverse_dt;
        if (input->material[index] == PHY_MATERIAL_WATER) {
            input->vx[index] *= water_damping;
            input->vy[index] *= water_damping;
            input->vz[index] *= water_damping;
        } else {
            double reference = clamp_double(1.0 - 0.06 * input->friction[index], 1.0e-6, 1.0);
            double horizontal = exp(log(reference) * sand_scale);
            input->vx[index] *= horizontal;
            input->vz[index] *= horizontal;
        }
    }
}

static int particle_substep(Solver *solver) {
    PhySimulation *input = solver->input;
    if (input->particle_count <= 0) return 1;
    pool_run(&solver->pool, input->particle_count, save_previous_and_apply_gravity_job, solver);
    if (!grid_rebuild(solver)) return 0;
    pool_run(&solver->pool, input->particle_count, velocity_filter_job, solver);
    pool_run(&solver->pool, input->particle_count, apply_velocity_filter_job, solver);
    pool_run(&solver->pool, input->particle_count, density_factor_job, solver);
    pool_run(&solver->pool, input->particle_count, surface_normal_job, solver);
    pool_run(&solver->pool, input->particle_count, surface_force_job, solver);
    pool_run(&solver->pool, input->particle_count, apply_velocity_filter_job, solver);

    int divergence_iterations = 0;
    double divergence_error = solve_pressure(
        solver, 0, input->divergence_iterations, &divergence_iterations
    );
    int density_iterations = 0;
    double density_error = solve_pressure(
        solver, 1, input->density_iterations, &density_iterations
    );
    solver->diagnostics->divergence_iterations_total += divergence_iterations;
    solver->diagnostics->density_iterations_total += density_iterations;
    if (divergence_iterations >= input->divergence_iterations &&
        divergence_error > 0.001 / solver->current_dt) {
        solver->diagnostics->divergence_iteration_limit_hits += 1;
    }
    if (density_iterations >= input->density_iterations && density_error > 0.0001) {
        solver->diagnostics->density_iteration_limit_hits += 1;
    }
    if (divergence_error > solver->diagnostics->peak_mean_divergence_error) {
        solver->diagnostics->peak_mean_divergence_error = divergence_error;
    }
    if (density_error > solver->diagnostics->peak_mean_density_excess) {
        solver->diagnostics->peak_mean_density_excess = density_error;
    }

    pool_run(&solver->pool, input->particle_count, integrate_positions_job, solver);
    int contact_iterations = clamp_int(input->density_iterations, 2, 5);
    solver->contact_iterations = contact_iterations;
    for (int iteration = 0; iteration < contact_iterations; ++iteration) {
        if (!grid_rebuild(solver)) return 0;
        pool_run(&solver->pool, input->particle_count, contact_delta_job, solver);
        pool_run(&solver->pool, input->particle_count, apply_contacts_and_boundaries_job, solver);
        for (int index = 0; index < input->particle_count; ++index) {
            double contact_correction = sqrt(
                solver->delta_x[index] * solver->delta_x[index] +
                solver->delta_y[index] * solver->delta_y[index] +
                solver->delta_z[index] * solver->delta_z[index]
            );
            if (contact_correction > solver->diagnostics->max_particle_contact_correction_m) {
                solver->diagnostics->max_particle_contact_correction_m = contact_correction;
            }
            if (solver->penetration[index] > solver->diagnostics->max_projection_correction_m) {
                solver->diagnostics->max_projection_correction_m = solver->penetration[index];
            }
        }
    }
    pool_run(&solver->pool, input->particle_count, reconstruct_velocity_job, solver);
    return 1;
}

static void compute_body_accelerations(Solver *solver, double time_value) {
    PhySimulation *input = solver->input;
    int count = input->body_count;
    memset(solver->body_ax, 0, (size_t)(count > 0 ? count : 1) * sizeof(double));
    memset(solver->body_ay, 0, (size_t)(count > 0 ? count : 1) * sizeof(double));
    memset(solver->body_az, 0, (size_t)(count > 0 ? count : 1) * sizeof(double));
    if (input->field_count > 0) {
        for (int index = 0; index < count; ++index) {
            force_field_acceleration(
                input, input->body_field_mask[index],
                input->body_x[index], input->body_y[index], input->body_z[index],
                time_value,
                &solver->body_ax[index], &solver->body_ay[index], &solver->body_az[index]
            );
        }
    }
    double epsilon_squared = input->softening * input->softening;
    for (int first = 0; first < count; ++first) {
        for (int second = first + 1; second < count; ++second) {
            double rx = input->body_x[second] - input->body_x[first];
            double ry = input->body_y[second] - input->body_y[first];
            double rz = input->body_z[second] - input->body_z[first];
            double distance_squared = rx * rx + ry * ry + rz * rz + epsilon_squared;
            double inverse_distance = 1.0 / sqrt(distance_squared);
            double inverse_distance_cubed = inverse_distance / distance_squared;
            double common = input->gravity_G * inverse_distance_cubed;
            if (!input->body_fixed[first]) {
                double scale = common * input->body_mass[second];
                solver->body_ax[first] += scale * rx;
                solver->body_ay[first] += scale * ry;
                solver->body_az[first] += scale * rz;
            }
            if (!input->body_fixed[second]) {
                double scale = -common * input->body_mass[first];
                solver->body_ax[second] += scale * rx;
                solver->body_ay[second] += scale * ry;
                solver->body_az[second] += scale * rz;
            }
        }
    }
}

static void gravity_verlet(Solver *solver, double dt, double field_sample_time) {
    PhySimulation *input = solver->input;
    compute_body_accelerations(solver, field_sample_time);
    for (int index = 0; index < input->body_count; ++index) {
        if (input->body_fixed[index]) continue;
        input->body_vx[index] += 0.5 * dt * solver->body_ax[index];
        input->body_vy[index] += 0.5 * dt * solver->body_ay[index];
        input->body_vz[index] += 0.5 * dt * solver->body_az[index];
        input->body_x[index] += dt * input->body_vx[index];
        input->body_y[index] += dt * input->body_vy[index];
        input->body_z[index] += dt * input->body_vz[index];
    }
    compute_body_accelerations(solver, field_sample_time);
    for (int index = 0; index < input->body_count; ++index) {
        if (input->body_fixed[index]) continue;
        input->body_vx[index] += 0.5 * dt * solver->body_ax[index];
        input->body_vy[index] += 0.5 * dt * solver->body_ay[index];
        input->body_vz[index] += 0.5 * dt * solver->body_az[index];
    }
}

static int gravity_substep(Solver *solver) {
    PhySimulation *input = solver->input;
    if (input->body_count == 0) return 1;
    double elapsed = 0.0;
    while (elapsed < solver->current_dt) {
        if (monotonic_seconds() >= solver->deadline) {
            atomic_store_explicit(&solver->deadline_exceeded, 1, memory_order_relaxed);
            return 0;
        }
        double remaining = solver->current_dt - elapsed;
        double dt = remaining;
        for (int i = 0; input->gravity_G > 0 && i < input->body_count; ++i) {
            for (int j = i + 1; j < input->body_count; ++j) {
                if (input->body_fixed[i] && input->body_fixed[j]) continue;
                double x = input->body_x[j] - input->body_x[i];
                double y = input->body_y[j] - input->body_y[i];
                double z = input->body_z[j] - input->body_z[i];
                double r2 = x*x + y*y + z*z + input->softening*input->softening;
                double vx = input->body_vx[j] - input->body_vx[i];
                double vy = input->body_vy[j] - input->body_vy[i];
                double vz = input->body_vz[j] - input->body_vz[i];
                double mu = input->gravity_G * (input->body_mass[i] + input->body_mass[j]);
                // Resolve both the softened free-fall and pair-crossing times.
                dt = fmin(dt, 0.005 * sqrt(r2 * sqrt(r2) / mu));
                double speed2 = vx*vx + vy*vy + vz*vz;
                if (speed2 > 0) dt = fmin(dt, 0.005 * sqrt(r2 / speed2));
            }
        }
        if (!(dt > 0) || elapsed + dt == elapsed) return 0;
        gravity_verlet(solver, dt, solver->current_time + elapsed + 0.5 * dt);
        elapsed = dt == remaining ? solver->current_dt : elapsed + dt;
    }
    return 1;
}

static double next_force_boundary(
    const PhySimulation *input,
    double current_time,
    double nominal_end
) {
    double result = nominal_end;
    /* Decimal dt grids can arrive a few nanoseconds before an exact field
       event.  Treat that as the event itself instead of launching an
       ill-conditioned pressure solve for a near-zero segment. */
    double tolerance = fmax(1.0e-12, input->dt * 1.0e-5);
    for (int index = 0; index < input->field_count; ++index) {
        const PhyForceField *field = &input->fields[index];
        if (field->start_time > current_time + tolerance && field->start_time < result) {
            result = field->start_time;
        }
        if (field->end_time > current_time + tolerance && field->end_time < result) {
            result = field->end_time;
        }
    }
    return result;
}

static int state_is_finite(const PhySimulation *input) {
    for (int index = 0; index < input->particle_count; ++index) {
        if (!finite3(input->x[index], input->y[index], input->z[index]) ||
            !finite3(input->vx[index], input->vy[index], input->vz[index])) return 0;
    }
    for (int index = 0; index < input->body_count; ++index) {
        if (!finite3(input->body_x[index], input->body_y[index], input->body_z[index]) ||
            !finite3(input->body_vx[index], input->body_vy[index], input->body_vz[index])) return 0;
    }
    return 1;
}

static void update_maximum_particle_speed(const PhySimulation *input,
                                          PhyDiagnostics *diagnostics) {
    double maximum_speed_squared = diagnostics->maximum_particle_speed_m_s *
                                   diagnostics->maximum_particle_speed_m_s;
    for (int index = 0; index < input->particle_count; ++index) {
        double speed_squared = input->vx[index] * input->vx[index] +
                               input->vy[index] * input->vy[index] +
                               input->vz[index] * input->vz[index];
        if (speed_squared > maximum_speed_squared) maximum_speed_squared = speed_squared;
    }
    diagnostics->maximum_particle_speed_m_s = sqrt(maximum_speed_squared);
}

static void observation_entity_state(const PhySimulation *input,
                                     const PhyObservationEntity *entity,
                                     double position[3], double velocity[3]) {
    for (int axis = 0; axis < 3; ++axis) {
        position[axis] = 0.0;
        velocity[axis] = 0.0;
    }
    if (entity->kind == 0) {
        for (int index = entity->start; index < entity->start + entity->count; ++index) {
            position[0] += input->x[index]; position[1] += input->y[index]; position[2] += input->z[index];
            velocity[0] += input->vx[index]; velocity[1] += input->vy[index]; velocity[2] += input->vz[index];
        }
        for (int axis = 0; axis < 3; ++axis) {
            position[axis] /= (double)entity->count;
            velocity[axis] /= (double)entity->count;
        }
    } else if (entity->kind == 1) {
        int index = entity->start;
        position[0] = input->body_x[index]; position[1] = input->body_y[index]; position[2] = input->body_z[index];
        if (!input->body_fixed[index]) {
            velocity[0] = input->body_vx[index]; velocity[1] = input->body_vy[index]; velocity[2] = input->body_vz[index];
        }
    } else {
        for (int axis = 0; axis < 3; ++axis) {
            position[axis] = entity->position[axis];
        }
    }
}

static double observation_metric_value(const PhySimulation *input, const PhyObservationMetric *metric) {
    if (metric->type == 0) {
        double maximum = 0.0;
        const double *coordinates[3] = {input->x, input->y, input->z};
        for (int index = metric->a.start; index < metric->a.start + metric->a.count; ++index) {
            double squared = 0.0;
            for (int j = 0; j < 2; ++j) {
                int axis = metric->axes[j];
                double delta = coordinates[axis][index] - metric->origin[axis];
                squared += delta * delta;
            }
            maximum = fmax(maximum, squared);
        }
        return sqrt(maximum);
    }
    double pa[3], va[3], pb[3], vb[3];
    observation_entity_state(input, &metric->a, pa, va);
    if (metric->type == 1) return pa[metric->axes[0]];
    if (metric->type == 2) return sqrt(va[0] * va[0] + va[1] * va[1] + va[2] * va[2]);
    observation_entity_state(input, &metric->b, pb, vb);
    double squared = 0.0;
    double signed_box_gap = -DBL_MAX;
    for (int axis = 0; axis < 3; ++axis) {
        double delta = pa[axis] - pb[axis];
        squared += delta * delta;
        signed_box_gap = fmax(signed_box_gap,
            fabs(delta) - 0.5 * (metric->a.size[axis] + metric->b.size[axis]));
    }
    if (metric->type == 3) return sqrt(squared);
    return metric->a.shape == 1 ? sqrt(squared) - metric->a.size[0] - metric->b.size[0] : signed_box_gap;
}

static int store_observations(PhySimulation *input, PhyDiagnostics *diagnostics, double time_value) {
    if (input->observation_metric_count == 0 && !input->observation_nbody) return 1;
    int sample = diagnostics->observations_written;
    if (sample >= input->observation_capacity) return 0;
    input->observation_times[sample] = time_value;
    for (int column = 0; column < input->observation_metric_count; ++column) {
        double value = observation_metric_value(input, &input->observation_metrics[column]);
        if (!isfinite(value)) return 0;
        size_t target = (size_t)sample * (size_t)input->observation_metric_count + (size_t)column;
        input->observation_values[target] = value;
    }
    if (input->observation_nbody) {
        double center[3] = {0.0, 0.0, 0.0};
        double momentum[3] = {0.0, 0.0, 0.0};
        double total_mass = 0.0, energy = 0.0, maximum = 0.0, minimum = DBL_MAX;
        for (int index = 0; index < input->body_count; ++index) {
            double mass = input->body_mass[index];
            total_mass += mass;
            center[0] += mass * input->body_x[index]; center[1] += mass * input->body_y[index];
            center[2] += mass * input->body_z[index];
            momentum[0] += mass * input->body_vx[index]; momentum[1] += mass * input->body_vy[index];
            momentum[2] += mass * input->body_vz[index];
            energy += 0.5 * mass * (input->body_vx[index] * input->body_vx[index] +
                input->body_vy[index] * input->body_vy[index] + input->body_vz[index] * input->body_vz[index]);
        }
        for (int axis = 0; axis < 3; ++axis) center[axis] /= total_mass;
        for (int index = 0; index < input->body_count; ++index) {
            double dx = input->body_x[index] - center[0];
            double dy = input->body_y[index] - center[1];
            double dz = input->body_z[index] - center[2];
            maximum = fmax(maximum, dx * dx + dy * dy + dz * dz);
            for (int other = index + 1; other < input->body_count; ++other) {
                dx = input->body_x[index] - input->body_x[other];
                dy = input->body_y[index] - input->body_y[other];
                dz = input->body_z[index] - input->body_z[other];
                double squared = dx * dx + dy * dy + dz * dz;
                minimum = fmin(minimum, squared);
                energy -= input->gravity_G * input->body_mass[index] * input->body_mass[other] /
                          sqrt(squared + input->softening * input->softening);
            }
        }
        size_t offset = (size_t)sample * 6u;
        double values[6] = {sqrt(maximum), sqrt(minimum), energy, momentum[0], momentum[1], momentum[2]};
        for (int column = 0; column < 6; ++column) {
            if (!isfinite(values[column])) return 0;
            input->observation_nbody_values[offset + (size_t)column] = values[column];
        }
    }
    diagnostics->observations_written += 1;
    return 1;
}

static int store_frame(PhySimulation *input, PhyDiagnostics *diagnostics, double time_value) {
    int frame = diagnostics->frames_written;
    if (frame >= input->frame_capacity) return 0;
    input->frame_times[frame] = time_value;
    for (int render = 0; render < input->render_count; ++render) {
        int particle = input->render_indices[render];
        size_t target = ((size_t)frame * (size_t)input->render_count + (size_t)render) * 3u;
        input->frame_particles[target + 0u] = (float)input->x[particle];
        input->frame_particles[target + 1u] = (float)input->y[particle];
        input->frame_particles[target + 2u] = (float)input->z[particle];
    }
    for (int body = 0; body < input->body_count; ++body) {
        size_t target = ((size_t)frame * (size_t)input->body_count + (size_t)body) * 3u;
        input->frame_bodies[target + 0u] = input->body_x[body];
        input->frame_bodies[target + 1u] = input->body_y[body];
        input->frame_bodies[target + 2u] = input->body_z[body];
    }
    diagnostics->frames_written += 1;
    return 1;
}

static void capture_frame_start(Solver *solver) {
    PhySimulation *input = solver->input;
    for (int render = 0; render < input->render_count; ++render) {
        int particle = input->render_indices[render];
        size_t target = (size_t)render * 3u;
        solver->frame_start_particles[target + 0u] = input->x[particle];
        solver->frame_start_particles[target + 1u] = input->y[particle];
        solver->frame_start_particles[target + 2u] = input->z[particle];
    }
    for (int body = 0; body < input->body_count; ++body) {
        size_t target = (size_t)body * 3u;
        solver->frame_start_bodies[target + 0u] = input->body_x[body];
        solver->frame_start_bodies[target + 1u] = input->body_y[body];
        solver->frame_start_bodies[target + 2u] = input->body_z[body];
    }
}

static int store_interpolated_frame(
    Solver *solver,
    double time_value,
    double interpolation
) {
    PhySimulation *input = solver->input;
    PhyDiagnostics *diagnostics = solver->diagnostics;
    int frame = diagnostics->frames_written;
    if (frame >= input->frame_capacity) return 0;
    double alpha = clamp_double(interpolation, 0.0, 1.0);
    input->frame_times[frame] = time_value;
    for (int render = 0; render < input->render_count; ++render) {
        int particle = input->render_indices[render];
        size_t source = (size_t)render * 3u;
        size_t target = ((size_t)frame * (size_t)input->render_count + (size_t)render) * 3u;
        input->frame_particles[target + 0u] = (float)(
            solver->frame_start_particles[source + 0u] +
            alpha * (input->x[particle] - solver->frame_start_particles[source + 0u])
        );
        input->frame_particles[target + 1u] = (float)(
            solver->frame_start_particles[source + 1u] +
            alpha * (input->y[particle] - solver->frame_start_particles[source + 1u])
        );
        input->frame_particles[target + 2u] = (float)(
            solver->frame_start_particles[source + 2u] +
            alpha * (input->z[particle] - solver->frame_start_particles[source + 2u])
        );
    }
    for (int body = 0; body < input->body_count; ++body) {
        size_t source = (size_t)body * 3u;
        size_t target = ((size_t)frame * (size_t)input->body_count + (size_t)body) * 3u;
        input->frame_bodies[target + 0u] = solver->frame_start_bodies[source + 0u] +
            alpha * (input->body_x[body] - solver->frame_start_bodies[source + 0u]);
        input->frame_bodies[target + 1u] = solver->frame_start_bodies[source + 1u] +
            alpha * (input->body_y[body] - solver->frame_start_bodies[source + 1u]);
        input->frame_bodies[target + 2u] = solver->frame_start_bodies[source + 2u] +
            alpha * (input->body_z[body] - solver->frame_start_bodies[source + 2u]);
    }
    diagnostics->frames_written += 1;
    return 1;
}

static int compare_double_ascending(const void *left, const void *right) {
    double a = *(const double *)left;
    double b = *(const double *)right;
    return (a > b) - (a < b);
}

static double sorted_quantile(const double *values, int count, double quantile) {
    if (count <= 0) return 0.0;
    double position = clamp_double(quantile, 0.0, 1.0) * (double)(count - 1);
    int lower = (int)floor(position);
    int upper = (int)ceil(position);
    double amount = position - (double)lower;
    return values[lower] * (1.0 - amount) + values[upper] * amount;
}

static void finalize_water_diagnostics(Solver *solver) {
    PhySimulation *input = solver->input;
    PhyDiagnostics *diagnostics = solver->diagnostics;
    int water_count = 0;
    for (int index = 0; index < input->particle_count; ++index) {
        if (input->material[index] == PHY_MATERIAL_WATER) water_count += 1;
    }
    diagnostics->represented_water_volume_m3 = (double)water_count * solver->volume;
    if (water_count == 0) {
        diagnostics->minimum_water_separation_ratio = 1.0;
        return;
    }
    if (!grid_rebuild(solver)) {
        diagnostics->minimum_water_separation_ratio = -1.0;
        diagnostics->close_water_particle_fraction = -1.0;
        return;
    }
    pool_run(&solver->pool, input->particle_count, density_factor_job, solver);
    double density_total = 0.0;
    double density_maximum = 0.0;
    double minimum_distance_squared = DBL_MAX;
    double close_distance_squared = 0.75 * input->spacing * 0.75 * input->spacing;
    int close_particles = 0;
    int boundary_supported = 0;
    int water_write = 0;
    for (int index = 0; index < input->particle_count; ++index) {
        if (input->material[index] != PHY_MATERIAL_WATER) continue;
        density_total += solver->density[index];
        if (solver->density[index] > density_maximum) density_maximum = solver->density[index];
        int close = 0;
        int start = solver->grid.neighbor_offsets[index];
        int stop = solver->grid.neighbor_offsets[index + 1];
        double nearest_distance_squared = DBL_MAX;
        for (int cursor = start; cursor < stop; ++cursor) {
            int other = solver->grid.neighbors[cursor];
            if (input->material[other] != PHY_MATERIAL_WATER) continue;
            double dx = input->x[index] - input->x[other];
            double dy = input->y[index] - input->y[other];
            double dz = input->z[index] - input->z[other];
            double distance_squared = dx * dx + dy * dy + dz * dz;
            if (distance_squared < minimum_distance_squared) minimum_distance_squared = distance_squared;
            if (distance_squared < nearest_distance_squared) nearest_distance_squared = distance_squared;
            if (distance_squared < close_distance_squared) close = 1;
        }
        close_particles += close;
        if (solver->boundary_density[index] > 1.0e-6) boundary_supported += 1;
        solver->source[water_write] = nearest_distance_squared == DBL_MAX ? 1.0 :
            sqrt(fmax(nearest_distance_squared, 0.0)) / input->spacing;
        solver->pressure[water_write] = solver->density[index];
        water_write += 1;
    }
    qsort(solver->source, (size_t)water_count, sizeof(double), compare_double_ascending);
    qsort(solver->pressure, (size_t)water_count, sizeof(double), compare_double_ascending);
    diagnostics->final_mean_water_density_ratio = density_total / (double)water_count;
    diagnostics->final_max_water_density_ratio = density_maximum;
    diagnostics->minimum_water_separation_ratio =
        minimum_distance_squared == DBL_MAX ? 1.0 :
        sqrt(fmax(minimum_distance_squared, 0.0)) / input->spacing;
    diagnostics->water_separation_p01_ratio = sorted_quantile(solver->source, water_count, 0.01);
    diagnostics->close_water_particle_fraction = (double)close_particles / (double)water_count;
    diagnostics->water_density_p50_ratio = sorted_quantile(solver->pressure, water_count, 0.50);
    diagnostics->water_density_p95_ratio = sorted_quantile(solver->pressure, water_count, 0.95);
    diagnostics->water_density_p99_ratio = sorted_quantile(solver->pressure, water_count, 0.99);
    diagnostics->planar_boundary_support_fraction = (double)boundary_supported / (double)water_count;
}

static int adaptive_substeps(Solver *solver, double macro_dt) {
    const PhySimulation *input = solver->input;
    if (input->particle_count <= 0) return 1;
    double maximum_speed_squared = 0.0;
    for (int index = 0; index < input->particle_count; ++index) {
        double speed_squared = input->vx[index] * input->vx[index] +
                               input->vy[index] * input->vy[index] +
                               input->vz[index] * input->vz[index];
        if (speed_squared > maximum_speed_squared) maximum_speed_squared = speed_squared;
    }
    double gravity_magnitude = sqrt(
        input->gravity[0] * input->gravity[0] +
        input->gravity[1] * input->gravity[1] +
        input->gravity[2] * input->gravity[2]
    );
    double acceleration_bound = gravity_magnitude + solver->maximum_field_acceleration +
                                solver->boundary_adhesion_acceleration_bound;
    double maximum_speed = sqrt(maximum_speed_squared);
    if (maximum_speed > solver->diagnostics->maximum_particle_speed_m_s) {
        solver->diagnostics->maximum_particle_speed_m_s = maximum_speed;
    }
    double transport_speed = maximum_speed + sqrt(acceleration_bound * input->spacing);
    double displacement_limit = 0.4 * input->spacing;
    int count = (int)ceil(macro_dt * transport_speed / fmax(displacement_limit, 1.0e-9));
    /* Surface tension supports capillary waves whose stable time scale shrinks
       with h^(3/2).  A velocity-only CFL rule misses this regime completely:
       millimetre droplets can be nearly stationary yet still require much
       smaller steps than metre-scale previews.  Use the standard capillary
       scale sqrt(rho*h^3/sigma), with rho0=1000 kg/m^3 and a conservative
       0.4 factor.  The configured quality tier remains the hard work bound. */
    double maximum_surface_tension = 0.0;
    for (int index = 0; index < input->particle_count; ++index) {
        if (input->material[index] == PHY_MATERIAL_WATER &&
            input->surface_tension[index] > maximum_surface_tension) {
            maximum_surface_tension = input->surface_tension[index];
        }
    }
    if (maximum_surface_tension > 0.0) {
        double capillary_dt = 0.4 * sqrt(
            1000.0 * solver->h * solver->h * solver->h / maximum_surface_tension
        );
        int capillary_count = (int)ceil(macro_dt / fmax(capillary_dt, 1.0e-12));
        if (capillary_count > count) count = capillary_count;
    }
    return clamp_int(count, 1, clamp_int(input->max_substeps, 1, 32));
}

static int validate_observation_entity(const PhySimulation *input, const PhyObservationEntity *entity) {
    if (entity->kind < 0 || entity->kind > 2 || entity->start < 0 || entity->count < 1) return 0;
    if (entity->kind == 0 && (entity->start >= input->particle_count ||
        entity->count > input->particle_count - entity->start)) return 0;
    if (entity->kind == 1 && (entity->start >= input->body_count || entity->count != 1)) return 0;
    if (entity->kind == 2 && entity->count != 1) return 0;
    if (entity->shape < 0 || entity->shape > 2 ||
        !finite3(entity->position[0], entity->position[1], entity->position[2]) ||
        !finite3(entity->velocity[0], entity->velocity[1], entity->velocity[2]) ||
        !finite3(entity->size[0], entity->size[1], entity->size[2])) return 0;
    if (entity->shape == 1 && !(entity->size[0] > 0.0)) return 0;
    if (entity->shape == 2 && (!(entity->size[0] > 0.0) || !(entity->size[1] > 0.0) ||
                              !(entity->size[2] > 0.0))) return 0;
    return 1;
}

static int validate_observations(const PhySimulation *input) {
    if (input->observation_metric_count < 0 || input->observation_metric_count > 16 ||
        input->observation_nbody < 0 || input->observation_nbody > 1) return 0;
    if (input->observation_metric_count == 0 && !input->observation_nbody) return 1;
    if (input->observation_capacity < 1 || input->observation_capacity > 250002 ||
        !input->observation_times) return 0;
    double required_samples = ceil(input->duration / input->dt) + 1.0;
    if (!isfinite(required_samples) || required_samples > (double)input->observation_capacity) return 0;
    if (input->observation_nbody && (input->body_count < 2 || !input->observation_nbody_values)) return 0;
    if (input->observation_metric_count > 0 && (!input->observation_metrics || !input->observation_values)) return 0;
    for (int index = 0; index < input->observation_metric_count; ++index) {
        const PhyObservationMetric *metric = &input->observation_metrics[index];
        if (metric->type < 0 || metric->type > 4 ||
            metric->axes[0] < 0 || metric->axes[0] > 2 || metric->axes[1] < 0 || metric->axes[1] > 2 ||
            !finite3(metric->origin[0], metric->origin[1], metric->origin[2]) ||
            !validate_observation_entity(input, &metric->a) ||
            !validate_observation_entity(input, &metric->b)) return 0;
        if (metric->type == 0 && (metric->a.kind != 0 || metric->axes[0] == metric->axes[1])) return 0;
        if (metric->type == 4 && (metric->a.kind != 2 || metric->b.kind != 2 ||
            metric->a.shape == 0 || metric->a.shape != metric->b.shape)) return 0;
    }
    return 1;
}

static int validate_input(const PhySimulation *input) {
    if (!input || input->abi_version != PHY_ABI_VERSION) return 0;
    if (input->particle_count < 0 || input->body_count < 0 || input->collider_count < 0 ||
        input->field_count < 0 || input->field_count > 32 ||
        input->render_count < 0 || input->frame_capacity <= 0 || input->render_count > input->particle_count) return 0;
    if (!(input->spacing > 0.0) || !(input->dt > 0.0) || !(input->duration > 0.0) ||
        !(input->output_fps > 0.0) || input->density_iterations < 1 ||
        input->divergence_iterations < 1 || input->thread_count < 1) return 0;
    if (!isfinite(input->spacing) || !isfinite(input->dt) || !isfinite(input->duration) ||
        !isfinite(input->output_fps) || !isfinite(input->gravity_G) ||
        !isfinite(input->softening) || !isfinite(input->water_sand_drag) ||
        !isfinite(input->wetting_rate) || !isfinite(input->deadline_seconds)) return 0;
    if (!isfinite(input->particle_gravity_density) || !isfinite(input->gravity_theta) ||
        input->gravity_theta < 0 || input->gravity_theta > 0.7) return 0;
    if (input->particle_count > 0 && input->gravity_G > 0 &&
        (input->body_count > 0 || input->particle_gravity_density <= 0 ||
         input->particle_gravity_density > 30000 || input->softening < 1e-6)) return 0;
    if (floor(input->output_fps) != input->output_fps) return 0;
    double required_intervals_value = ceil(input->duration * input->output_fps - 1.0e-10);
    if (!isfinite(required_intervals_value) || required_intervals_value > (double)INT32_MAX - 1.0) return 0;
    int required_intervals = (int)required_intervals_value;
    if (required_intervals < 1) required_intervals = 1;
    if (input->frame_capacity < required_intervals + 1) return 0;
    if (!finite3(input->gravity[0], input->gravity[1], input->gravity[2]) ||
        !finite3(input->bounds_min[0], input->bounds_min[1], input->bounds_min[2]) ||
        !finite3(input->bounds_max[0], input->bounds_max[1], input->bounds_max[2])) return 0;
    for (int axis = 0; axis < 3; ++axis) {
        if (!(input->bounds_min[axis] < input->bounds_max[axis])) return 0;
    }
    if (!input->frame_times || (input->render_count > 0 && (!input->render_indices || !input->frame_particles)) ||
        (input->body_count > 0 && !input->frame_bodies)) return 0;
    if (input->particle_count > 0 && (!input->material || !input->x || !input->y || !input->z ||
        !input->vx || !input->vy || !input->vz || !input->anchor_x || !input->anchor_y ||
        !input->anchor_z || !input->viscosity || !input->surface_tension || !input->friction ||
        !input->cohesion || !input->wetness)) return 0;
    if (input->body_count > 0 && (!input->body_fixed || !input->body_mass || !input->body_x ||
        !input->body_y || !input->body_z || !input->body_vx || !input->body_vy || !input->body_vz)) return 0;
    if (input->collider_count > 0 && !input->colliders) return 0;
    if (input->field_count > 0 && (!input->fields ||
        (input->particle_count > 0 && !input->particle_field_mask) ||
        (input->body_count > 0 && !input->body_field_mask))) return 0;
    for (int index = 0; index < input->collider_count; ++index) {
        const PhyCollider *collider = &input->colliders[index];
        if (collider->type < PHY_COLLIDER_PLANE || collider->type > PHY_COLLIDER_CAPSULE ||
            !finite3(collider->a[0], collider->a[1], collider->a[2]) ||
            !finite3(collider->b[0], collider->b[1], collider->b[2]) ||
            !finite3(collider->c[0], collider->c[1], collider->c[2]) ||
            !isfinite(collider->friction) || collider->friction < 0.0 ||
            collider->friction > 5.0) return 0;
        if (collider->type == PHY_COLLIDER_PLANE &&
            (collider->c[0] < 0.0 || collider->c[0] > PHY_MAX_ACCELERATION)) return 0;
    }
    double combined_plane_adhesion = 0.0;
    for (int index = 0; index < input->collider_count; ++index) {
        if (input->colliders[index].type == PHY_COLLIDER_PLANE) {
            combined_plane_adhesion += input->colliders[index].c[0];
        }
    }
    if (combined_plane_adhesion > PHY_MAX_ACCELERATION) return 0;
    for (int index = 0; index < input->field_count; ++index) {
        const PhyForceField *field = &input->fields[index];
        if (field->type < PHY_FIELD_UNIFORM || field->type > PHY_FIELD_VORTEX ||
            !finite3(field->origin[0], field->origin[1], field->origin[2]) ||
            !finite3(field->vector[0], field->vector[1], field->vector[2]) ||
            !isfinite(field->strength) || !isfinite(field->radius) ||
            !isfinite(field->secondary_strength) || !isfinite(field->start_time) ||
            !isfinite(field->end_time) || !(field->start_time < field->end_time)) return 0;
        if (field->type != PHY_FIELD_UNIFORM && !(field->radius > 0.0)) return 0;
    }
    for (int index = 0; index < input->render_count; ++index) {
        if (input->render_indices[index] < 0 || input->render_indices[index] >= input->particle_count) return 0;
    }
    return validate_observations(input);
}

uint32_t phy_abi_version(void) {
    return PHY_ABI_VERSION;
}

const char *phy_build_string(void) {
#if defined(__clang__)
    return "c11-dfsph-v6-particle-gravity-soa-pthreads/clang-" __clang_version__;
#elif defined(__GNUC__)
    return "c11-dfsph-v6-particle-gravity-soa-pthreads/gcc-" __VERSION__;
#else
    return "c11-dfsph-v6-particle-gravity-soa-pthreads/unknown-compiler";
#endif
}

int phy_simulate(PhySimulation *input, PhyDiagnostics *diagnostics) {
    if (!diagnostics) return PHY_STATUS_INVALID_ARGUMENT;
    memset(diagnostics, 0, sizeof(*diagnostics));
    diagnostics->finite = 1;
    if (!validate_input(input)) {
        diagnostics->status = PHY_STATUS_INVALID_ARGUMENT;
        diagnostics->finite = 0;
        return diagnostics->status;
    }
    double started = monotonic_seconds();
    double deadline = started + fmax(input->deadline_seconds, 0.0);
    Solver solver;
    if (!solver_initialize(&solver, input, diagnostics)) {
        diagnostics->status = PHY_STATUS_OUT_OF_MEMORY;
        solver_destroy(&solver);
        return diagnostics->status;
    }
    solver.deadline = deadline;
    atomic_store_explicit(&solver.deadline_exceeded, 0, memory_order_relaxed);
    diagnostics->threads_used = solver.pool.threads;
    if (!state_is_finite(input) || !store_frame(input, diagnostics, 0.0) ||
        !store_observations(input, diagnostics, 0.0)) {
        diagnostics->status = PHY_STATUS_INVALID_ARGUMENT;
        diagnostics->finite = 0;
        diagnostics->runtime_s = monotonic_seconds() - started;
        solver_destroy(&solver);
        return diagnostics->status;
    }

    double simulation_time = 0.0;
    double output_interval = 1.0 / input->output_fps;
    int target_frame_count = (int)ceil(input->duration * input->output_fps - 1.0e-10);
    if (target_frame_count < 1) target_frame_count = 1;
    target_frame_count += 1;
    int next_frame_index = 1;
    int failed = 0;
    int timed_out = 0;
    while (simulation_time < input->duration - 1.0e-12) {
        double remaining_time = input->duration - simulation_time;
        /*
         * Decimal scene timesteps such as 0.0083333333 can leave a terminal
         * remainder of only a few nanoseconds.  Advancing that remainder does
         * not change the sampled state, but would make minimum_substep_s look
         * like a severe CFL event.  Snap only when the remainder is below
         * 1e-5 of the requested macro step.
         */
        if (remaining_time <= fmax(1.0e-12, input->dt * 1.0e-5)) {
            simulation_time = input->duration;
            break;
        }
        if (monotonic_seconds() >= deadline) {
            timed_out = 1;
            break;
        }
        double macro_dt = fmin(input->dt, remaining_time);
        double macro_start_time = simulation_time;
        capture_frame_start(&solver);
        int substep_count = adaptive_substeps(&solver, macro_dt);
        if (substep_count > 1) diagnostics->cfl_limited_steps += 1;
        double nominal_substep_dt = macro_dt / (double)substep_count;
        int actual_substeps = 0;
        for (int substep = 0; substep < substep_count; ++substep) {
            double nominal_end = substep + 1 == substep_count ?
                macro_start_time + macro_dt :
                macro_start_time + (double)(substep + 1) * nominal_substep_dt;
            while (simulation_time < nominal_end - 1.0e-12) {
                if (monotonic_seconds() >= deadline) {
                    timed_out = 1;
                    break;
                }
                double segment_end = next_force_boundary(input, simulation_time, nominal_end);
                double segment_dt = segment_end - simulation_time;
                if (!prepare_particle_gravity(&solver)) {
                    timed_out = atomic_load_explicit(&solver.deadline_exceeded, memory_order_relaxed);
                    failed = !timed_out;
                    break;
                }
                if (solver.gravity_ax) {
                    /* Re-evaluate on every shared-clock substep; never clamp a
                     * stiff gravitational event to the ordinary CFL work cap. */
                    double safe_dt = 0.2 / sqrt(input->gravity_G * input->particle_gravity_density);
                    if (solver.particle_gravity_maximum > 0)
                        safe_dt = fmin(safe_dt, 0.2 * sqrt(input->spacing / solver.particle_gravity_maximum));
                    for (int i = 0; i < input->particle_count; ++i) {
                        double speed = sqrt(input->vx[i]*input->vx[i] + input->vy[i]*input->vy[i] + input->vz[i]*input->vz[i]);
                        if (speed > 0) safe_dt = fmin(safe_dt, 0.25 * input->spacing / speed);
                    }
                    segment_dt = fmin(segment_dt, safe_dt);
                    segment_end = simulation_time + segment_dt;
                    if (segment_end <= simulation_time || segment_dt < 1e-12) { failed = 1; break; }
                }
                solver.current_dt = segment_dt;
                solver.current_time = simulation_time;
                if (!gravity_substep(&solver) || !particle_substep(&solver)) {
                    if (atomic_load_explicit(&solver.deadline_exceeded, memory_order_relaxed)) {
                        timed_out = 1;
                    } else {
                        failed = 1;
                    }
                    break;
                }
                update_maximum_particle_speed(input, diagnostics);
                simulation_time = segment_end;
                diagnostics->substeps += 1;
                actual_substeps += 1;
                if (diagnostics->minimum_substep_s == 0.0 ||
                    segment_dt < diagnostics->minimum_substep_s) {
                    diagnostics->minimum_substep_s = segment_dt;
                }
                if (!state_is_finite(input)) {
                    diagnostics->finite = 0;
                    failed = 1;
                    break;
                }
            }
            if (failed || timed_out) break;
            if (simulation_time < nominal_end) {
                simulation_time = nominal_end;
            }
        }
        if (actual_substeps > diagnostics->max_substeps_used) {
            diagnostics->max_substeps_used = actual_substeps;
        }
        diagnostics->macro_steps += 1;
        if (failed || timed_out) break;
        if (!store_observations(input, diagnostics, simulation_time)) {
            failed = 1;
            break;
        }
        double macro_elapsed = simulation_time - macro_start_time;
        while (next_frame_index < target_frame_count - 1) {
            double output_time = (double)next_frame_index * output_interval;
            if (output_time > simulation_time + 1.0e-10) break;
            double alpha = macro_elapsed > 0.0 ?
                (output_time - macro_start_time) / macro_elapsed : 1.0;
            if (!store_interpolated_frame(&solver, output_time, alpha)) {
                failed = 1;
                break;
            }
            next_frame_index += 1;
        }
        if (failed) break;
    }

    if (!failed && !timed_out && simulation_time >= input->duration - 1.0e-9) {
        if (diagnostics->frames_written != target_frame_count - 1 ||
            !store_frame(input, diagnostics, input->duration)) {
            failed = 1;
        }
    }

    double sand_displacement = 0.0;
    double sand_wetness = 0.0;
    int sand_count = 0;
    for (int index = 0; index < input->particle_count; ++index) {
        if (input->material[index] != PHY_MATERIAL_SAND) continue;
        double dx = input->x[index] - input->anchor_x[index];
        double dy = input->y[index] - input->anchor_y[index];
        double dz = input->z[index] - input->anchor_z[index];
        sand_displacement += sqrt(dx * dx + dy * dy + dz * dz);
        sand_wetness += input->wetness[index];
        sand_count += 1;
    }
    diagnostics->mean_sand_displacement_m = sand_displacement / (double)(sand_count > 0 ? sand_count : 1);
    diagnostics->mean_sand_wetness = sand_wetness / (double)(sand_count > 0 ? sand_count : 1);
    if (diagnostics->finite && !timed_out && monotonic_seconds() < deadline) {
        finalize_water_diagnostics(&solver);
        if (atomic_load_explicit(&solver.deadline_exceeded, memory_order_relaxed)) timed_out = 1;
    }
    diagnostics->simulated_time_s = simulation_time;
    diagnostics->completed = !failed && !timed_out && simulation_time >= input->duration - 1.0e-9;
    diagnostics->runtime_s = monotonic_seconds() - started;
    diagnostics->status = timed_out ? PHY_STATUS_TIMED_OUT :
                          (!diagnostics->finite ? PHY_STATUS_NONFINITE :
                          (failed ? PHY_STATUS_INTERNAL_ERROR : PHY_STATUS_OK));
    int status = diagnostics->status;
    solver_destroy(&solver);
    return status;
}

/* A context owns scratch/pool/grid only; the caller retains physical state.
 * Snapshotting the descriptor also prevents count or pointer replacement from
 * making the persistent scratch arrays smaller than the next job's range. */
struct PhyContext {
    Solver solver;
    PhySimulation input;
    double started;
};

static int validate_context_input(const PhySimulation *input) {
    /* Bound counts before validate_input follows any caller-owned array. These
     * caps cover the public particle quality presets and coupled-world limits. */
    /* Persistent contact contexts have an external shared clock. Full
     * particle self-gravity uses phy_simulate and its own adaptive scheduler. */
    if (!input || input->gravity_G != 0 || input->particle_count < 0 || input->particle_count > 24000 ||
        input->body_count < 0 || input->body_count > 64 ||
        input->collider_count < 0 || input->collider_count > 64 ||
        input->field_count < 0 || input->field_count > 32 ||
        input->render_count < 0 || input->render_count > input->particle_count ||
        input->frame_capacity < 2 || input->frame_capacity > 2401 ||
        input->density_iterations < 1 || input->density_iterations > 128 ||
        input->divergence_iterations < 1 || input->divergence_iterations > 128 ||
        input->thread_count < 1 || input->thread_count > 32 ||
        input->max_substeps < 1 || input->max_substeps > 32 ||
        !(input->spacing >= 1e-6 && input->spacing <= 1e6) ||
        !(input->duration / input->dt <= 250000) ||
        !validate_input(input) || !state_is_finite(input)) return 0;
    for (int i = 0; i < input->particle_count; ++i) {
        if ((input->material[i] != PHY_MATERIAL_WATER && input->material[i] != PHY_MATERIAL_SAND) ||
            !finite3(input->anchor_x[i], input->anchor_y[i], input->anchor_z[i]) ||
            !isfinite(input->viscosity[i]) || input->viscosity[i] < 0 ||
            !isfinite(input->surface_tension[i]) || input->surface_tension[i] < 0 ||
            !isfinite(input->friction[i]) || input->friction[i] < 0 ||
            !isfinite(input->cohesion[i]) || input->cohesion[i] < 0 ||
            !isfinite(input->wetness[i]) || input->wetness[i] < 0 || input->wetness[i] > 1) return 0;
    }
    return input->water_sand_drag >= 0 && input->wetting_rate >= 0;
}

PhyContext *phy_context_create(PhySimulation *input, PhyDiagnostics *diagnostics) {
    if (!diagnostics) return NULL;
    memset(diagnostics, 0, sizeof(*diagnostics));
    diagnostics->status = PHY_STATUS_INVALID_ARGUMENT;
    if (!validate_context_input(input)) return NULL;
    diagnostics->finite = 1;
    if (input->deadline_seconds <= 0) {
        diagnostics->status = PHY_STATUS_TIMED_OUT;
        return NULL;
    }
    PhyContext *context = calloc(1, sizeof(*context));
    if (!context) {
        diagnostics->status = PHY_STATUS_OUT_OF_MEMORY;
        return NULL;
    }
    context->input = *input;
    context->started = monotonic_seconds();
    if (!solver_initialize(&context->solver, &context->input, diagnostics)) {
        diagnostics->status = PHY_STATUS_OUT_OF_MEMORY;
        solver_destroy(&context->solver);
        free(context);
        return NULL;
    }
    context->solver.deadline = context->started + input->deadline_seconds;
    atomic_store_explicit(&context->solver.deadline_exceeded, 0, memory_order_relaxed);
    diagnostics->threads_used = context->solver.pool.threads;
    diagnostics->status = PHY_STATUS_OK;
    return context;
}

int phy_context_step(PhyContext *context, double absolute_time, double dt) {
    if (!context) return PHY_STATUS_INVALID_ARGUMENT;
    Solver *solver = &context->solver;
    PhySimulation *input = solver->input;
    PhyDiagnostics *diagnostics = solver->diagnostics;
    double end = absolute_time + dt;
    if (!isfinite(absolute_time) || !isfinite(dt) || !(absolute_time >= 0) ||
        !(dt > 0) || !isfinite(1.0 / (dt * dt)) || !isfinite(end) || !(end > absolute_time) ||
        end > input->duration + fmax(1e-12, input->duration * 1e-12))
        return diagnostics->status = PHY_STATUS_INVALID_ARGUMENT;
    if (solver_deadline_reached(solver)) return diagnostics->status = PHY_STATUS_TIMED_OUT;
    if (!state_is_finite(input)) {
        diagnostics->finite = 0;
        return diagnostics->status = PHY_STATUS_NONFINITE;
    }
    diagnostics->finite = 1;
    solver->current_time = absolute_time;
    solver->current_dt = dt;
    if (!particle_substep(solver)) {
        diagnostics->runtime_s = monotonic_seconds() - context->started;
        if (!state_is_finite(input)) {
            diagnostics->finite = 0;
            return diagnostics->status = PHY_STATUS_NONFINITE;
        }
        return diagnostics->status = atomic_load_explicit(&solver->deadline_exceeded, memory_order_relaxed)
            ? PHY_STATUS_TIMED_OUT : PHY_STATUS_INTERNAL_ERROR;
    }
    diagnostics->substeps += 1;
    diagnostics->max_substeps_used = 1;
    diagnostics->simulated_time_s = fmin(end, input->duration);
    if (diagnostics->minimum_substep_s == 0 || dt < diagnostics->minimum_substep_s)
        diagnostics->minimum_substep_s = dt;
    if (!state_is_finite(input)) {
        diagnostics->finite = 0;
        diagnostics->runtime_s = monotonic_seconds() - context->started;
        return diagnostics->status = PHY_STATUS_NONFINITE;
    }
    return phy_context_refresh(context);
}

int phy_context_refresh(PhyContext *context) {
    if (!context) return PHY_STATUS_INVALID_ARGUMENT;
    Solver *solver = &context->solver;
    PhySimulation *input = solver->input;
    PhyDiagnostics *diagnostics = solver->diagnostics;
    if (solver_deadline_reached(solver)) return diagnostics->status = PHY_STATUS_TIMED_OUT;
    if (!state_is_finite(input)) {
        diagnostics->finite = 0;
        return diagnostics->status = PHY_STATUS_NONFINITE;
    }
    update_maximum_particle_speed(input, diagnostics);
    double displacement = 0, wetness = 0;
    int sand_count = 0;
    for (int i = 0; i < input->particle_count; ++i) {
        if (input->material[i] != PHY_MATERIAL_SAND) continue;
        double dx = input->x[i] - input->anchor_x[i];
        double dy = input->y[i] - input->anchor_y[i];
        double dz = input->z[i] - input->anchor_z[i];
        displacement += sqrt(dx * dx + dy * dy + dz * dz);
        wetness += input->wetness[i];
        sand_count += 1;
    }
    diagnostics->mean_sand_displacement_m = displacement / (sand_count ? sand_count : 1);
    diagnostics->mean_sand_wetness = wetness / (sand_count ? sand_count : 1);
    finalize_water_diagnostics(solver);
    diagnostics->runtime_s = monotonic_seconds() - context->started;
    if (atomic_load_explicit(&solver->deadline_exceeded, memory_order_relaxed))
        return diagnostics->status = PHY_STATUS_TIMED_OUT;
    if (diagnostics->minimum_water_separation_ratio < 0)
        return diagnostics->status = PHY_STATUS_INTERNAL_ERROR;
    return diagnostics->status = PHY_STATUS_OK;
}

void phy_context_destroy(PhyContext *context) {
    if (!context) return;
    solver_destroy(&context->solver);
    free(context);
}

int phy_context_set_velocity_decay(PhyContext *context, double rate_s_inverse) {
    if (!context || !isfinite(rate_s_inverse) || rate_s_inverse < 0) return PHY_STATUS_INVALID_ARGUMENT;
    context->solver.velocity_decay_rate = rate_s_inverse;
    return PHY_STATUS_OK;
}
