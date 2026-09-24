"""Central resource and quality limits shared by validation and planning.

Keep hard safety limits here so an agent-facing contract, the validator, and
the execution planner cannot silently drift apart.
"""

from __future__ import annotations


MAX_WALL_TIME_S = 300
# A finite representation of an unbounded runtime for the native C ABI. It is
# used only for host-authorized tasks; the ordinary scene budget stays at 300.
NO_DEADLINE_WALL_TIME_S = 1_000_000_000_000.0
MAX_PHYSICAL_DURATION_S = 60.0


QUALITY_LIMITS = {
    "preview": {
        "particles": 2_000,
        "iterations": 5,
        "divergence_iterations": 2,
        "render_particles": 2_000,
        "threads": 4,
        "max_substeps": 6,
    },
    "balanced": {
        "particles": 8_000,
        "iterations": 12,
        "divergence_iterations": 4,
        "render_particles": 6_000,
        "threads": 8,
        "max_substeps": 8,
    },
    "high": {
        "particles": 24_000,
        "iterations": 16,
        "divergence_iterations": 6,
        "render_particles": 10_000,
        "threads": 12,
        "max_substeps": 12,
    },
}

MAX_ENTITIES = 128
MAX_COLLIDERS = 64
MAX_FORCE_FIELDS = 8
MAX_SCENE_NODES = 20_000
MAX_SCENE_DEPTH = 32
MAX_ABS_COORDINATE = 10_000.0
MAX_WORLD_SPAN = 1_000.0
MAX_SHAPE_EXTENT = 1_000.0
MAX_SPEED = 500.0
MAX_ACCELERATION = 250.0
MAX_INITIAL_PARTICLE_VOLUME_OVERLAP = 8
MAX_FRAME_PARTICLE_SAMPLES = 600_000
EXACT_SPHERE_COUNT_CELLS = 250_000
MIN_PARTICLE_SPACING = 1.0e-4
MAX_PARTICLE_SPACING = 0.5

# Closed point-mass systems are advertised as the quantitative path.  These
# block strict delivery and quantitative answers. Visual videos retain the
# diagnostics as precision warnings instead of claiming scientific accuracy.
NBODY_MAX_RELATIVE_ENERGY_DRIFT = 0.02
NBODY_MAX_INITIAL_STEP_RATIO = 0.10
NBODY_MOMENTUM_RELATIVE_TOLERANCE = 0.02
NBODY_MOMENTUM_MASS_SCALED_FLOOR = 1.0e-10
NBODY_MOMENTUM_ABSOLUTE_FLOOR = 1.0e-9

MAX_SCENE_FILE_BYTES = 1_000_000
MAX_RESULT_FILE_BYTES = 128_000_000
MAX_PATCH_OPERATIONS = 64
