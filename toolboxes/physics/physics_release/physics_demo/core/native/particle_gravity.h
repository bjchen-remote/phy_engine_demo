/* Equal-mass Plummer gravity. Rebuilt tight octree, bounded bucket leaves.
 * Barnes & Hut (1986), doi:10.1038/324446a0. No borrowed implementation.
 * theta=0 is the direct reference; callers own scheduling and deadlines. */
#ifndef PHY_PARTICLE_GRAVITY_H
#define PHY_PARTICLE_GRAVITY_H
#include <math.h>
#include <stdlib.h>

typedef struct {
    double center[3], width2;
    int start, count, children[8];
} PGNode;

typedef struct {
    int count, used;
    const double *position[3];
    int *order, *scratch, *rank;
    PGNode *nodes;
} PGTree;

static int pg_init(PGTree *t, int count) {
    t->count = count;
    t->order = calloc((size_t)count, sizeof(int));
    t->scratch = calloc((size_t)count, sizeof(int));
    t->rank = calloc((size_t)count, sizeof(int));
    /* Every internal node has >=2 children, so at most 2N-1 nodes. */
    t->nodes = calloc((size_t)count * 2, sizeof(PGNode));
    return t->order && t->scratch && t->rank && t->nodes;
}

static void pg_destroy(PGTree *t) {
    free(t->order); free(t->scratch); free(t->rank); free(t->nodes);
}

static void pg_bind_positions(PGTree *t, int count,
                              const double *x, const double *y, const double *z) {
    t->count = count;
    t->used = 0;
    t->position[0] = x; t->position[1] = y; t->position[2] = z;
}

static int pg_octant(const PGTree *t, int i, const double mid[3]) {
    return (t->position[0][i] >= mid[0]) |
        ((t->position[1][i] >= mid[1]) << 1) |
        ((t->position[2][i] >= mid[2]) << 2);
}

static int pg_node(PGTree *t, int start, int count, int depth) {
    int id = t->used++;
    PGNode *node = &t->nodes[id];
    node->start = start; node->count = count;
    double low[3], high[3], mid[3], width = 0;
    for (int a = 0; a < 3; ++a) {
        low[a] = high[a] = t->position[a][t->order[start]];
        node->center[a] = 0;
        for (int k = start; k < start + count; ++k) {
            double v = t->position[a][t->order[k]];
            low[a] = fmin(low[a], v); high[a] = fmax(high[a], v);
            node->center[a] += v / count;
        }
        mid[a] = low[a] + 0.5 * (high[a] - low[a]);
        width = fmax(width, high[a] - low[a]);
    }
    node->width2 = width * width;
    for (int o = 0; o < 8; ++o) node->children[o] = -1;
    if (count <= 8 || depth >= 32 || width == 0) return id;
    int sizes[8] = {0}, offsets[8], write[8], occupied = 0;
    for (int k = start; k < start + count; ++k)
        sizes[pg_octant(t, t->order[k], mid)]++;
    int cursor = start;
    for (int o = 0; o < 8; ++o) {
        offsets[o] = write[o] = cursor; cursor += sizes[o];
        occupied += sizes[o] != 0;
    }
    /* Coincident positions or rounding cannot cause unbounded subdivision. */
    if (occupied < 2) return id;
    for (int k = start; k < start + count; ++k) {
        int i = t->order[k]; t->scratch[write[pg_octant(t, i, mid)]++] = i;
    }
    for (int k = start; k < start + count; ++k) t->order[k] = t->scratch[k];
    for (int o = 0; o < 8; ++o)
        if (sizes[o]) node->children[o] = pg_node(t, offsets[o], sizes[o], depth + 1);
    return id;
}

static void pg_build(PGTree *t, const double *x, const double *y, const double *z) {
    pg_bind_positions(t, t->count, x, y, z);
    for (int i = 0; i < t->count; ++i) t->order[i] = i;
    pg_node(t, 0, t->count, 0);
    for (int i = 0; i < t->count; ++i) t->rank[t->order[i]] = i;
}

static void pg_add(double dx, double dy, double dz, double eps2,
                   double mass, double out[3]) {
    double r2 = dx * dx + dy * dy + dz * dz + eps2;
    double f = mass / (r2 * sqrt(r2));
    out[0] += f * dx; out[1] += f * dy; out[2] += f * dz;
}

static void pg_visit(const PGTree *t, int id, int i, double theta2,
                     double eps2, double out[3]) {
    const PGNode *n = &t->nodes[id];
    double dx = n->center[0] - t->position[0][i];
    double dy = n->center[1] - t->position[1][i];
    double dz = n->center[2] - t->position[2][i];
    int contains = t->rank[i] >= n->start && t->rank[i] < n->start + n->count;
    /* Never approximate a cell containing the target: no self attraction. */
    if (!contains && n->width2 < theta2 * (dx*dx + dy*dy + dz*dz)) {
        pg_add(dx, dy, dz, eps2, n->count, out);
        return;
    }
    int branch = 0;
    for (int o = 0; o < 8; ++o) if (n->children[o] >= 0) {
        branch = 1; pg_visit(t, n->children[o], i, theta2, eps2, out);
    }
    if (branch) return;
    for (int k = n->start; k < n->start + n->count; ++k) {
        int j = t->order[k];
        if (j != i) pg_add(t->position[0][j]-t->position[0][i],
            t->position[1][j]-t->position[1][i],
            t->position[2][j]-t->position[2][i], eps2, 1, out);
    }
}

static void pg_acceleration(const PGTree *t, int i, double theta,
                            double eps, double gm, double out[3]) {
    out[0] = out[1] = out[2] = 0;
    if (theta > 0) pg_visit(t, 0, i, theta * theta, eps * eps, out);
    else for (int j = 0; j < t->count; ++j) {
        if (j != i) pg_add(t->position[0][j]-t->position[0][i],
            t->position[1][j]-t->position[1][i],
            t->position[2][j]-t->position[2][i], eps * eps, 1, out);
    }
    for (int a = 0; a < 3; ++a) out[a] *= gm;
}
#endif
