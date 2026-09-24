# Fast CFD development path

The current fast path is the native C11 DFSPH solver, not the Python PBF
reference. It uses structure-of-arrays storage, a fixed-radius neighbor
structure, a persistent pthread worker pool, adaptive transport **and
capillary** substeps, bounded density/divergence iterations and explicit
deadline checks. The release can ship prebuilt arm64 solver and video binaries
so a deployed job does not invoke a compiler.

This choice follows the published method boundaries: DFSPH targets both density
and velocity-divergence error with larger practical steps, while Position Based
Fluids is a useful robust alternative rather than an automatic upgrade for an
already working DFSPH path. The implemented pairwise cohesion/curvature term is
the Akinci et al. surface-tension model. Sources:

- Bender and Koschier, [Divergence-Free Smoothed Particle
  Hydrodynamics](https://animation.rwth-aachen.de/media/papers/2015-SCA-DFSPH.pdf).
- Akinci et al., [Versatile Surface Tension and Adhesion for SPH
  Fluids](https://cg.informatik.uni-freiburg.de/publications/2013_SIGGRAPHASIA_surfaceTensionAdhesion.pdf).
- Macklin and Müller, [Position Based Fluids](https://mmacklin.com/pbf_sig_preprint.pdf).

## Measured profiles

`benchmarks/benchmark_fast_cfd.py` runs the same millimetre falling-water-drop
model with three bounded profiles and accepts timings only when the normal
finite-state and water-stability quality gates pass. The two-repeat Apple M4
result is stored in
`benchmarks/results/fast-cfd-apple-m4-2026-09-20.json`.

These are offline benchmark variants. The QQ toolbox defaults to a macroscopic
ground-splash scene for an unscaled visible splash, and separately ships 3 mm
dry-floor and pre-wetted examples. `fast` and `rapid` are not selectable
release scenes.

- `balanced`: 1647 particles, 0.20 s physical window, median solver time
  7.52 s, 60 fps.
- `fast`: 840 particles, the same 0.20 s physical window, median solver time
  4.23 s (1.78x); this is the preferred latency-oriented profile.
- `rapid`: 480 particles, 0.15 s physical window, median solver time 1.92 s
  (3.92x); use only when the shorter physical window is disclosed.

The engine retains `balanced` when the prompt asks for high detail. This is
explicit profile selection, not an unreported accuracy change.

## Water-drop corrections

An earlier 0.32 m radius prototype used a three-metre-wide collision box.
Its fluid reached the walls and the rendered puddle became square. The
millimetre dry-floor example instead uses a 6 mm diameter drop (about 0.11 mL),
sampled at 0.4 mm and falling 12 mm before first contact. A separate 0.32 m
radius visual splash was later added with horizontal walls at ±4 m and a 0.8 s
early-impact window ending before side-wall contact. It is an illustrative
macroscopic scene, not a millimetre droplet prediction.

Velocity CFL alone is insufficient at the millimetre scale because a nearly
stationary interface still supports capillary waves. The native solver therefore also
limits the substep with the capillary scale
`0.4 * sqrt(rho * h^3 / sigma)`, bounded by the selected quality tier. The
3 mm benchmark case uses eight substeps per 1 ms macro step and passes the existing
density and separation gates.

The renderer fits recorded geometry at its own scale and reconstructs a
continuous liquid surface. It applies deterministic, neighbourhood-weighted
kernel-centre smoothing only to projected render samples; it never writes
smoothed positions back to the trajectory. This is the conservative subset of
the reconstruction direction described by Yu and Turk,
[Reconstructing Surfaces of Particle-Based Fluids Using Anisotropic
Kernels](https://faculty.cc.gatech.edu/~turk/my_papers/sph_surfaces.pdf).
Water opacity is view-dependent for readability, but the renderer does not
claim refraction or a second air phase.

Very short impacts also need a presentation timescale. The benchmark's 0.2 s
trajectory remains the auditable solver result. `encode_watchable_mp4` can
create a separate, smooth display-only retiming with piecewise physical-time
segments and endpoint holds. It linearly interpolates recorded state, does not
write into the solver result, and reports physical duration, playback duration
and their ratio. That millimetre benchmark showcase uses 204 frames at 30 fps:
6.8 s of playback for 0.2 s of physics, with extra time around impact and recoil.

## What was taken from the IPM reference

The IPM project is a different two-dimensional porous-media PDE, so its
quadrant reduction, WENO transport, Poisson solve and level-set remeshing are
not transplanted into this three-dimensional free-surface particle model.
The reusable design ideas are narrower: one validated configuration path,
adaptive time control, finite-state checks, diagnostics that gate delivery,
and transactional fallback when a candidate adaptation fails.

The plane collider now has a bounded `water_adhesion` control.  It applies a
smooth near-wall attraction and works with contact friction to retain a wider
footprint after impact.  This is a visual wetting/pinning approximation, not a
calibrated wetting law: dynamic contact angle, hysteresis, entrained air and
breakup remain absent. Spatial adaptivity with conservative split/merge or a sparse
background pressure grid remains a separate research track; it should be
introduced behind a solver mode and compared at matched physical times against
DFSPH. Neither change should silently claim engineering accuracy before
particle/time-step refinement tests exist.

## Claim boundary

These profiles demonstrate faster delivery for the current visual,
single-phase water model.  They do not establish particle convergence,
calibrated density/viscosity, air coupling, turbulence accuracy, phase change,
or engineering-grade CFD validity.
