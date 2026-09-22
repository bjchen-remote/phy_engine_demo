# Mixed contact and rotating rigid bodies

Read this reference for a gyroscope, tumbling box, spring attached off-centre to a rigid body, or contact between water/sand, soft meshes and attached solids. Use the standard scene/prepare/simulate/query workflow; the twelve-tool interface also provides named-system and liquid-preset helpers. Coupling itself requires no new scene format version, user callback or executable force expression.

## Select the route

Set `scene.coupling: {}` to opt into the shared native route. An entity with `type: "rigid_body"` also selects it automatically. Accepted entities are `fluid`, `granular`, `point_mass`, `mesh` and `rigid_body`; static analytic geometry belongs in `colliders`. Use `budget.backend: "auto"` or `"native"`, with `interactions.mutual_gravity: false`. This route has no Python fallback. It does not combine N-body mutual gravity or sliders with contact.

Without this opt-in, legacy particles, meshes, connection networks, sliders and `type: "rigid"` retain their existing routes and meanings. A bare legacy connection still has no thickness or collision. Use `rigid_body` for rotating spheres/boxes/cylinders; changing the old `rigid` type's mass does not enable rotation.

Particles, soft meshes and `rigid_body` receive `world.gravity`. **Point masses still require explicit acceleration fields**; use a targeted `uniform` field for a point-mass pendulum. Existing uniform/radial/vortex fields target fluid/granular/point_mass only. There is no public arbitrary rigid torque field: off-centre springs, contact and fixed-pivot gravity provide torque.

## Rigid body contract

```json
{"id":"rotor", "type":"rigid_body",
 "shape":{"type":"cylinder","radius":0.35,"height":0.12},
 "mass":1.0, "position":[0,1,0], "velocity":[0,0,0],
 "orientation":[1,0,0,0], "angular_velocity":[0,30,0],
 "fixed":false, "friction":0.2, "restitution":0.0}
```

`position` is world COM; `velocity` and `angular_velocity` are world vectors, in m/s and rad/s. `orientation` is a unit `[w,x,y,z]` quaternion taking body vectors to world vectors. It defaults to identity; angular velocity defaults to zero. Coordinates are SI and Y-up.

Geometry is centred at the COM. Sphere uses `radius`; box uses full `size:[sx,sy,sz]`; cylinder uses `radius` and full `height`, with its axis along **local Y**. Every dimension lies in `[0.0001,100]` m; mass lies in `[1e-6,1e6]` kg. Uniform-solid principal moments are derived from these dimensions and mass, rather than supplied as arbitrary tensors. `fixed:true` requires zero linear/angular velocity. Friction is `[0,5]`, default 0.2; restitution is `[0,1]`, default zero.

Optional `pivot:{"point":[world xyz],"local_point":[body xyz]}` fixes a body point. `local_point` is measured **from COM to pivot**, is at most 100 m long and lies on one principal axis. This restriction keeps the parallel-axis inertia diagonal. A pivot cannot be combined with `fixed:true`. Author a consistent initial pose:

- `pivot.point = position + R(orientation) * pivot.local_point`.
- `velocity = angular_velocity × (position - pivot.point)`.

The service rejects inconsistent initial geometry/velocity rather than snapping it. COM then follows `pivot.point - R * pivot.local_point`. Gravity torque about the pivot is `(COM-pivot) × (mass*gravity)`, so tilt, spin and precession arise from the integrated state. For pivot bodies, the stored angular momentum and rotational energy are about the anchor and include COM motion about it.

## Attachments and physical links

A connection keeps its existing `spring`, `rod` or `rope` parameters. Choose exactly one endpoint representation:

```json
{"id":"tether", "type":"spring", "rest_length":1.0,
 "stiffness":20.0, "damping":0.3,
 "endpoints":[{"entity":"support"},
              {"entity":"rotor","local_point":[0.2,0,0]}]}
```

| Target | Endpoint |
|---|---|
| Point-mass centre | `{"entity":"id"}` |
| Mesh vertex | `{"entity":"id","vertex":3}`; local integer vertex index |
| Rigid body attachment | `{"entity":"id","local_point":[x,y,z]}`; COM-relative body coordinates; omitted point means COM |

The older `entities:["a","b"]` form remains available for centre attachments. A spring force acts on both endpoints; a rigid endpoint also receives the lever-arm torque. Rod/rope constraints use translational and rotational effective inverse mass. Rods start at their target length; ropes may start slack, never overlong. A non-spring link is an ideal distance constraint, not a joint with a resolved bearing or bending law.

Add `solid:{"radius":0.04,"mass":0.2}` for a **straight capsule collision body along the actual endpoints**. Radius is `[0.0001,1]` m; optional mass defaults to zero and is bounded by 1000 kg. Positive link mass is supported only when both endpoints are point masses: half is added to each endpoint's effective mass. With a rigid or mesh endpoint, `solid.mass` must be zero. The capsule has no independent distributed inertia, bending, coil geometry or coil self-contact. The solver responds to its actual thickness and the renderer draws that same straight rounded body. A non-solid spring's thin helix is an annotation, and cannot block water.

Coupled point mass lies in `[1e-9,1e12]` kg. It may supply `collision_radius` equal to zero (disabled), or in `[1e-4,10]` m. Positive-radius points exchange contact impulses with particles, rigid bodies, meshes, other positive-radius points and solid links; there is no resolved point spin. Point/link contact does not model spin friction.

## Shared solver and limits

`coupling` accepts `substeps` (1–64), `iterations` (1–16) and `friction` (0–5, default 0.2). Quality defaults for `(substeps,iterations)` are preview `(4,3)`, balanced `(8,5)`, high `(12,8)`. Meshes retain `mesh_settings` for internal XPBD resolution. Do not supply standalone `connection_settings` on this route.

One C clock advances persistent DFSPH particles, XPBD surfaces, spring/constraint attachments and rigid poses. Particle pressure/neighbour scratch and mesh rest/initial geometry survive between steps. Cross-domain contacts use equal/opposite finite-mass reaction impulses; point–triangle contact distributes reaction to all three vertices using barycentric weights. Rigid effective inverse mass includes the contact lever arm and world inverse inertia. Free rotation uses body-momentum implicit midpoint with a quaternion Cayley increment; world angular momentum is state, not reconstructed from a speed clamp. The method is second order for free rotation, preserves quaternion norm, and preserves free-body angular momentum and rotational energy to solve tolerance. This does not make the entire damped/contact system energy-conserving or second order.

Limits are 4096 particles, 2048 mesh vertices, 16 rotating rigid bodies, 128 connections, 64 point masses and 100,000 macro steps, subject to the existing mesh/topology, observation and wall-budget limits. Prepare can increase shared substeps to resolve spring/mesh stiffness and initial contact CFL. `plan.coupling_initial_cfl_substeps` records the initial CFL requirement; `plan.coupling_event_headroom` reserves extra substeps for force-field start/end times inside a macro step. Preparation rejects `coupling_substeps + coupling_event_headroom > 64` and excessive work. Runtime contact CFL checks can subdivide further within the same 64-substep cap, including event splits, or fail explicitly. They never silently clamp all velocities to hide an unstable state.

Cross-domain contact is discrete. Mesh–rigid contact combines mesh vertices against analytic rigid shapes with sampled rigid support points against mesh faces; face reactions use barycentric vertex weights and the rigid body’s full rotational effective mass. Rigid support and rigid–rigid contact use one sphere centre/radius sample, eight box corners or 24 cylinder rim samples. Solid link capsules use at most 65 axial samples against static/rigid geometry, approximately two radii apart until that cap is reached. Very long/thin or rapidly stretched links can have sampling gaps. These are not exact triangle–rigid manifolds or general continuous collision detection: small features, edge-only crossings and fast motion need timestep/refinement checks. Existing mesh–mesh contact retains its own bounded swept vertex–triangle handling. Supported reaction contact **does not add dynamic rigid/mesh boundary-volume pressure terms to DFSPH**. Only existing explicit planes supply that pressure support. Do not claim monolithic fluid–structure interaction, calibrated buoyancy, hydrodynamic force, watertightness or engineering stress.

Report `diagnostics.backend: "native-c11-coupled"`, shared substeps, contacts, quaternion error and the nested particle/mesh diagnostics. `max_penetration_m` is the shared pair-contact kernel’s peak overlap before projection. It excludes the mesh context contact kernel’s peak and is neither a full-domain penetration bound nor a final unresolved residual. Delivery checks require quaternion error below `1e-8`, at most 64 shared substeps, and peak link constraint error at most `0.002` m, plus the existing native water/mesh quality gates where applicable. `contact_impulse_norm` and `attachment_impulse_norm` accumulate impulse magnitudes, not signed force histories. `support_impulse` includes only fixed/pivot support reactions from the shared pair-contact and attachment kernels. It excludes particle-internal static bounds/pressure and mesh-context fixed/pinned supports, and is not a full-domain conservation ledger. The ordinary completion, finite-state, result-identity, query and verified-video gates still apply; compare `dt/2` and particle spacing/mesh resolution before quantitative interpretation.

## Angular measurements and recorded geometry

Declare these as `series` or `threshold` metrics before prepare; every target must be `rigid_body`:

| Metric | Fields / definition | Unit |
|---|---|---|
| `angular_speed` | `entity`; magnitude of world angular velocity | rad/s |
| `angular_momentum` | `entity`, `axis:"x"|"y"|"z"`; world component, about COM or fixed anchor as above | kg·m²/s |
| `rotational_energy` | `entity`; `0.5 * L · omega`, about the integration origin | J |
| `axis_tilt` | `entity`; `acos((R * body_unit_Y) · world_unit_Y)`, in `[0,pi]` | rad |

`axis_tilt` always compares the body's local +Y to world +Y; it is not a selectable-axis Euler angle. Existing connection and supported entity/mesh measurements still use complete float64 macro-step state, independent of video FPS. `surface_gap` for arbitrary rotating bodies or meshes is not provided.

Mixed trajectory frames contain `p` particles, `g` point centres, `r` rigid COM positions, `q` rigid wxyz orientations and `m` mesh vertices. `gravity_body_ids`, `rigid_ids`/`rigid_shapes`, and `mesh_objects` define stable indices. Rendering transforms actual shape vertices by q and shares a pixel depth buffer across meshes, rotating solids, particles and solid capsules. Cylinder cap sectors and a distinct meridian show spin and precession without inventing geometry or feeding visual poses back into the solver.

The renderer shows an ideal rigid pivot as a small fixed marker at `pivot.point` and a thin line to the recorded center of mass. This is a constraint annotation; it adds no support mass, collision shaft or contact surface.
