# Example and customer scene catalog

Call `physics_example` with one stable name below, without `.json`. It returns the scene, `common_patches` with actual IDs, use cases and limitations. Source files live in [examples](../../../examples/); `catalog.py` owns machine-readable customer prompts, inherited assumptions and historical timing anchors. Re-prepare after every patch; historical Apple M4 timing is not a promise for the edited scene.

| Example name | Event / useful controls | Interpretation boundary |
|---|---|---|
| `three_body` | Softened orbit; body positions, velocities, duration | Point masses do not couple to water/sand |
| `three_body_queries` | Orbit criteria; `orbit-bounds` query, dt/duration | Finite sampled window, not permanent stability |
| `droplet_ground` | 3 mm radius drop on dry floor; `drop` centre Y/radius | Radial ground beads, no demonstrated airborne droplets |
| `droplet_ground_micro_wet` | 3 mm radius drop over a 1.2 mm pre-wetted film | Upward droplets depend on the film; not a dry-floor result |
| `droplet_ground_splash` | Visible dry-floor splash from a 0.32 m radius drop | Macroscopic scale; 0.8 s early impact ends before horizontal ±4 m wall contact |
| `droplet_ground_macro_wet` | 0.32 m radius drop onto a pre-existing 4 cm water film | Upward spray depends on the film; `surface_tension=0.055` is an uncalibrated model coefficient, not a molecular-force measurement |
| `droplet_cone` | Blue water drop onto copper cone; drop and cone geometry | Qualitative mesh contact, no fluid wetting adhesion or cone friction |
| `liquid_preset_showcase` | Equal water/honey/glue/molten-lead drops | Single shared pressure phase; no real density or thermal effects |
| `droplet_pool` | Drop enters water; `drop` and `pool` geometry | One liquid phase, no resolved air |
| `droplet_sphere` | 0.09 m water drop over a static sphere; `drop` height, `ball` radius | Use only when the requested obstacle is a sphere; not a floor-splash preset |
| `droplet_radius_query` | Radius threshold; `radius-1m` value/origin/plane | Particle-centre radius includes spray, not wetted footprint |
| `water_blob` | Free moving water ball; `water-blob` velocity/radius | No rubber membrane |
| `sandcastle_wash` | Water destroys cohesive sand; drag/wetting | Qualitative erosion |
| `high_detail_droplet_sphere` | Detailed water impact on a static sphere; drop geometry, budget | Sphere requests only; high tier and 120 s budget |
| `geyser` | Timed upward jet; `upward-burst` acceleration/window | Finite charge, no boiling or pump |
| `sand_blast` | Sand dispersal; `blast-pulse` centre/strength/radius | Prescribed radial acceleration, no explosive gas/fracture |
| `whirlpool` | Visible tank spins then coasts; `tank-vortex` strength/inward strength | Finite pulse, no impeller or turbulence model |
| `water_obstacle_course` | Flow through rounded posts; `water-charge` velocity, post radii | Static capsules with rounded ends |
| `zero_g_droplet_collision` | Two approaching drops; `left-drop`/`right-drop` velocities | No surrounding gas film |
| `product_pour_tumbler` | Beverage pour; `product-liquid` velocity/size/viscosity | Preloaded finite charge, static glass-style walls |
| `fountain_hoop` | Fountain through ring; `fountain-pulse` and hoop geometry | Eight static capsules approximate a hoop |
| `flood_bridge_piers` | Water splits around piers; `flood-water` speed, `release-push`, pier radius | Flow-path concept, no hydraulic/structural design |
| `sand_sculpture_wave` | Wave topples twin towers; `wave` speed, drag/wetting | Visual destruction, no coastal erosion prediction |
| `microgravity_three_drops` | Three drops merge; `dose-left/right/top` velocities | Single phase, no physical micrometre scaling/chemistry |
| `cosmetic_hero_splash` | Liquid crown around product; `hero-drop` height/tension, `hero-orb` radius | Static proxy sphere, no rheology/packaging loads |
| `vehicle_wading_splash` | Water hits wheels/chassis; `road-water` speed, `vehicle-body` height | Water moves, vehicle proxies remain static |
| `sliders_separate` | Sliding blocks; `left/right` velocity/drive/friction, positive gap query | Standalone common-X-rail model |
| `spring_oscillator` | Fixed-anchor Hooke oscillator; mass, stiffness, initial extension | Massless spring, no collision |
| `damped_spring` | Decaying oscillation; axial damping and stiffness | Dashpot acts along the spring only |
| `coupled_springs` | Coupled masses exchange motion; masses and links | Independent point-mass network |
| `rod_pendulum` | Fixed-length pendulum; length, initial angle, explicit gravity field | Ideal rod constraint, no bearing/contact |
| `rope_catch` | Slack rope becomes taut; initial velocity and rope length | Tension only; catch is nonelastic |
| `spring_chain` | Multi-mass spring chain; link stiffness and damping | Links can cross, no collision |
| `spring_pendulum` | Swinging and stretching mass; gravity, stiffness, rest length | Point mass and massless spring |
| `mesh_soft_drop` | Soft closed shape falls and deforms; position, compliance, volume query | XPBD surface proxy, no calibrated bulk material |
| `mesh_soft_collision` | Two elastic surfaces collide; velocities and compliance | Bounded vertex–triangle contacts, no general collision-free guarantee |
| `mesh_ring_insertion` | Prescribed plug passes through a mesh opening | Existing hole and contact deformation; no puncture/cutting or insertion-force output |
| `mesh_cloth_drape` | Open cloth drapes onto a mesh obstacle; pins and bending | No guaranteed self-collision or textile calibration |
| `mesh_image_extrusion` | Image-style silhouette with declared extrusion depth | Example outline/assumed depth, not automatic image reconstruction |
| `mesh_imagined_shape` | Explicit imagined depth/surface geometry | Provenance records assumptions; visual model only |
| `gyroscope_precession` | Fixed-pivot spinning cylinder; tilt, spin and gravity | Physical pivot torque, local-Y rotor, no bearing/friction calibration |
| `torque_free_tumble` | Asymmetric box free rotation; quaternion and world angular momentum | Free rigid-body invariants, no applied torque |
| `spring_rigid_pendulum` | Point support attached to an off-centre rigid point | Spring force supplies COM motion and torque |
| `water_spring_reaction` | Water impacts a spring-attached collision body | Paired reaction, no calibrated hydrodynamic load |
| `solid_spring_water` | Water meets a finite-radius straight spring capsule | Capsule thickness and optional endpoint-lumped mass; not helical wire |
| `ghost_spring_water` | Matching illustrative spring without a collision solid | Control scene: drawn coils cannot block water; world walls may exchange momentum |
| `water_soft_sheet` | Water deforms a soft triangle sheet | Partitioned point–triangle contact; no membrane pressure FSI |
| `water_sheet_spring_rigid` | Water, soft sheet, local attachment and rotating body | Full shared clock; inspect each domain's diagnostics and refinement |

The original connection examples are independent point-mass networks; parameter units, queries and numerical limits are maintained in [connections](connections.md).

The original mesh examples remain standalone. New [mixed scenes](coupling.md) opt into shared contact; N-body and sliders retain their separate boundaries. To replace their geometry or build from an actual image, follow [mesh-modeling](mesh-modeling.md) and preserve provenance.

## Customer request cards

Use the catalog's concrete assumptions with the user's requested edits. These cards supply phrasing and composition; exact geometry/material values come from the returned scene.

| Request | Starting scene and inherited setup | Disclose |
|---|---|---|
| “Pour a premium drink into a low tumbler; splash and settle.” | `product_pour_tumbler`: finite off-screen charge, static open tumbler; wall boxes use glass appearance | No moving bottle, continuous source, bubbles, foam, air, fill-rate or glass optics |
| “Spin a clear tank, then let the whirlpool coast.” | `whirlpool`: shallow volume, four visible glass-style walls, vortex ends at 1.1 s | No pump power, torque, resolved turbulence, free-surface mesh or calibrated mixing time |
| “Keep the cosmetic product readable through a crown splash.” | `cosmetic_hero_splash`: static spherical proxy above a shallow tray, glass-style side walls | No label/bottle shape, shear-thinning material, package motion/load or optics |
| “Send water around a vehicle's wheels and underbody.” | `vehicle_wading_splash`: finite slug, four capsule wheels and box chassis | No tyre rotation, suspension, vehicle translation, aerosol, drag, ingress or safety result |
| “Launch a fountain through a decorative ring.” | `fountain_hoop`: eight capsules and finite upward acceleration pulse | No continuous nozzle, pressure, mist or power prediction |
| “Show a flood release splitting around bridge piers.” | `flood_bridge_piers`: finite slug and half-second push replacing reservoir head | No discharge, calibrated water level, scour, debris, structure response or safety conclusion |
| “One wave knocks down a resort's twin-tower sand sculpture.” | `sand_sculpture_wave`: three touching sand boxes and a finite water slug | Wetting/cohesion/drag proxies, no erosion rate or coastal-design result |
| “Show medication drops merging in microgravity.” | `microgravity_three_drops`: one visual liquid phase, zero gravity, initial approach velocities | No gas film, surfactants, chemistry, validated coalescence or flight-hardware prediction |

## Compositions

- Add a short vortex to the sand tower for a stylized grain spiral; it still represents cohesive grains, not airborne dust.
- Add an upward pulse after a radial pulse for a fountain burst. Overlapping windows add accelerations and must satisfy the combined limit.
- Add staggered capsules for a slalom, grate or flow splitter. More colliders increase contact work; capsules are static, not articulated or rotating parts.
- Place a liquid sphere over a shallow liquid box for pool impact. Separate IDs track groups, but all presets share the same liquid pressure phase and native spacing.
- Vary three drop radii and converging velocities for a collision sequence; keep sources separated initially.

Keep field target IDs synchronized after renaming. Leave at least one effective spacing between sources and solids; touching sand blocks must not occupy the same volume. Read [prompt-routing](prompt-routing.md) for combining scenes and [capability-boundaries](capability-boundaries.md) for budget/fidelity limits.
