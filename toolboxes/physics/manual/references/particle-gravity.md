# Self-gravitating liquid and granular matter

Liquid particles can attract one another. Use the ordinary native particle route,
not a substitute point-mass orbit or a fixed radial field. Start with
`physics_example(name="self_gravitating_liquid")` or author any collection of
fluid/granular volumes and static colliders.

```json
"interactions": {
  "mutual_gravity": true,
  "gravity_G": 0.02,
  "softening": 0.035,
  "particle_gravity_density": 800,
  "gravity_theta": 0.5
}
```

- `gravity_G` is the gravitational constant in the scene's SI units. Earth-scale
  water barely self-attracts at real Newtonian G; a larger value is an explicit
  stylized or scaled-system assumption, never a hidden visual correction.
- Every particle carries equal mass `particle_gravity_density * effective_spacing^3`.
  Density defaults to 1000 kg/m3, range `(0, 30000]`, and is shared by all matter.
  Water/honey/glue/molten-lead appearance presets do not change gravitational mass.
  `agent_report.particle_gravity` reports particle and total represented mass.
  Changing spacing changes the sampled volume slightly; report plan adjustments.
- `softening` is the explicit Plummer length in metres, at least `1e-6`. Never
  enlarge it silently. This regularizes close pairs; it is not a collision radius.
- `gravity_theta` is the Barnes-Hut opening angle, range `[0, 0.7]`. Visual requests
  default to 0.5; strict requests default to 0 (direct pairs). Smaller values are
  more accurate and slower. An explicit value is preserved in either mode.
- `world.gravity` remains an independent uniform external acceleration. Set it
  to zero for freely moving liquid masses, and choose generous world bounds:
  those bounds are still contact walls.

The C11 solver rebuilds a bounded octree on every gravitational substep. Nearby
sources are evaluated individually, distant cells by their aggregate mass; a cell
containing the target is always opened. The approximation's mean acceleration is
removed so it adds no net force to this equal-mass population. Fluid pressure,
surface tension and contacts still act. Adaptive substeps resolve gravitational
acceleration and transport, with the original wall deadline and full duration.
`gravity_theta=0` computes direct softened forces for convergence checks.

This is an incompressible visual matter model, not compressible stellar gas or
calibrated astrophysical hydrodynamics. Tree forces do not exactly conserve angular
momentum; pressure/contact/damping also affect total mechanical energy. A passed
visual video cannot establish orbital stability. Quantitative requests need strict
acceptance, predeclared queries and resolution/timestep/softening convergence.

Current scope is native/auto fluid and granular particles with static colliders.
Mixing their mutual gravity with point masses, moving solids, meshes or attachments
is explicitly rejected; these routes must not silently omit a force. There is no
Python fallback for particle self-gravity. Strong attraction can take more than a
minute; disclose the estimate and continue within the host's granted deadline.

Algorithm reference: Barnes & Hut, [A hierarchical O(N log N) force-calculation
algorithm](https://doi.org/10.1038/324446a0), Nature 324, 446–449 (1986).
