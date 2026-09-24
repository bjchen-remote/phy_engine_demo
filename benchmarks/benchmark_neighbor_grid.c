/* Isolated compact-grid correctness check and sparse/dense timing fixture.
 * Build from this directory with clang -std=c11 -O3 -pthread
 * benchmark_neighbor_grid.c -lm -o /tmp/benchmark-neighbor-grid.
 * Override PHY_NATIVE_SOURCE at compile time to compare another checkout. */
#ifndef PHY_NATIVE_SOURCE
#define PHY_NATIVE_SOURCE "../physics_demo/core/native/physics_native.c"
#endif
#include PHY_NATIVE_SOURCE

typedef struct {
    Solver solver;
    PhySimulation simulation;
    PhyDiagnostics diagnostics;
    double *x, *y, *z;
    int count;
    int pool_ready;
} GridFixture;

static void fixture_destroy(GridFixture *f) {
    if (f->pool_ready) pool_destroy(&f->solver.pool);
    grid_destroy(&f->solver.grid);
    free(f->x); free(f->y); free(f->z);
}

static int fixture_init(GridFixture *f, int count, int threads) {
    memset(f, 0, sizeof(*f));
    f->count = count;
    f->x = calloc((size_t)count, sizeof(double));
    f->y = calloc((size_t)count, sizeof(double));
    f->z = calloc((size_t)count, sizeof(double));
    if (!f->x || !f->y || !f->z) return 0;
    f->simulation.particle_count = count;
    f->simulation.x = f->x;
    f->simulation.y = f->y;
    f->simulation.z = f->z;
    f->solver.input = &f->simulation;
    f->solver.diagnostics = &f->diagnostics;
    f->solver.h = 1.0;
    f->solver.h2 = 1.0;
    f->solver.deadline = monotonic_seconds() + 300.0;
    atomic_init(&f->solver.deadline_exceeded, 0);
    if (!grid_initialize(&f->solver.grid, count)) return 0;
    if (!pool_initialize(&f->solver.pool, threads)) return 0;
    f->pool_ready = 1;
    return 1;
}

static void place_particles(GridFixture *f, int dense) {
    int side = (int)ceil(cbrt((double)f->count));
    double spacing = dense ? 0.5 : 3.0;
    for (int i = 0; i < f->count; ++i) {
        f->x[i] = (i % side - side / 2) * spacing;
        f->y[i] = ((i / side) % side - side / 2) * spacing;
        f->z[i] = (i / (side * side) - side / 2) * spacing;
    }
}

/* Match the documented dz/dy/dx cell traversal and stable within-cell index
 * order, without calling the hash grid's lookup code. */
static int check_neighbors(const GridFixture *f) {
    const CompactGrid *g = &f->solver.grid;
    for (int i = 0; i < f->count; ++i) {
        int cursor = g->neighbor_offsets[i];
        int64_t cx = (int64_t)floor(f->x[i]);
        int64_t cy = (int64_t)floor(f->y[i]);
        int64_t cz = (int64_t)floor(f->z[i]);
        for (int dz = -1; dz <= 1; ++dz) {
            for (int dy = -1; dy <= 1; ++dy) {
                for (int dx = -1; dx <= 1; ++dx) {
                    for (int j = 0; j < f->count; ++j) {
                        if (j == i || (int64_t)floor(f->x[j]) != cx + dx ||
                            (int64_t)floor(f->y[j]) != cy + dy ||
                            (int64_t)floor(f->z[j]) != cz + dz) continue;
                        double rx = f->x[i] - f->x[j];
                        double ry = f->y[i] - f->y[j];
                        double rz = f->z[i] - f->z[j];
                        if (rx * rx + ry * ry + rz * rz >= 1.0) continue;
                        if (cursor >= g->neighbor_offsets[i + 1] || g->neighbors[cursor] != j) return 0;
                        ++cursor;
                    }
                }
            }
        }
        if (cursor != g->neighbor_offsets[i + 1]) return 0;
    }
    return 1;
}

static int check_case(int count, int dense, int threads) {
    GridFixture f;
    if (!fixture_init(&f, count, threads)) {
        fixture_destroy(&f);
        return 0;
    }
    place_particles(&f, dense);
    int okay = grid_rebuild(&f.solver) && check_neighbors(&f);
    if (okay) {
        f.x[0] += 100.25; /* Rebuild after an externally changed position. */
        okay = grid_rebuild(&f.solver) && check_neighbors(&f);
    }
    fixture_destroy(&f);
    return okay;
}

static int check_collision_and_guards(void) {
    GridFixture f;
    if (!fixture_init(&f, 2, 1)) {
        fixture_destroy(&f);
        return 0;
    }
    int first[64];
    for (int i = 0; i < 64; ++i) first[i] = -1;
    int found = 0;
    for (int x = 0; x < 1000 && !found; ++x) {
        int bucket = (int)(cell_hash(x, 0, 0) & 63u);
        if (first[bucket] >= 0 && x - first[bucket] > 1) {
            f.x[0] = first[bucket] + 0.25;
            f.x[1] = x + 0.25;
            found = 1;
        } else if (first[bucket] < 0) first[bucket] = x;
    }
    int okay = found && grid_rebuild(&f.solver) && check_neighbors(&f);
    f.solver.deadline = monotonic_seconds() - 1.0;
    atomic_store_explicit(&f.solver.deadline_exceeded, 0, memory_order_relaxed);
    okay = okay && !grid_rebuild(&f.solver);
    fixture_destroy(&f);
    if (!okay) return 0;

    GridFixture crowded;
    if (!fixture_init(&crowded, 513, 1)) {
        fixture_destroy(&crowded);
        return 0;
    }
    /* All positions coincide; the existing 512 particles/cell limit must hold. */
    okay = !grid_rebuild(&crowded.solver);
    fixture_destroy(&crowded);
    return okay;
}

static int benchmark_case(int count, int repeats, int threads, int dense) {
    GridFixture f;
    if (!fixture_init(&f, count, threads)) {
        fixture_destroy(&f);
        return 0;
    }
    place_particles(&f, dense);
    if (!grid_rebuild(&f.solver)) {
        fixture_destroy(&f);
        return 0;
    }
    double start = monotonic_seconds();
    for (int i = 0; i < repeats; ++i) {
        if (!grid_rebuild(&f.solver)) {
            fixture_destroy(&f);
            return 0;
        }
    }
    double elapsed = monotonic_seconds() - start;
    printf("%s,%d,%d,%d,%.6f,%d\n", dense ? "dense" : "sparse", count,
           repeats, threads, 1000.0 * elapsed / repeats,
           f.solver.grid.neighbor_offsets[count]);
    fixture_destroy(&f);
    return 1;
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--check") == 0) {
        for (int threads = 1; threads <= 2; ++threads) {
            if (!check_case(64, 0, threads) || !check_case(64, 1, threads)) return 1;
        }
        if (!check_collision_and_guards()) return 1;
        puts("neighbor grid checks passed");
        return 0;
    }
    int count = argc > 1 ? atoi(argv[1]) : 4096;
    int repeats = argc > 2 ? atoi(argv[2]) : 100;
    int threads = argc > 3 ? atoi(argv[3]) : 1;
    if (count < 1 || count > 24000 || repeats < 1 || repeats > 10000 ||
        threads < 1 || threads > 8) return 2;
    puts("pattern,particles,rebuilds,threads,ms_per_rebuild,neighbor_links");
    return benchmark_case(count, repeats, threads, 0) &&
           benchmark_case(count, repeats, threads, 1) ? 0 : 1;
}
