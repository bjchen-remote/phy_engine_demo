# Prompt-to-scene routing

For a single/double pendulum, start with `physics_system` and its [physical parameter specification](systems.md). For other models route by the requested physical event, then select from [interesting-scenes](interesting-scenes.md). “Advertising,” “engineering,” or “pharma” describes the use of a video, not additional solver accuracy. Load the closest `physics_example`; use its `common_patches`, inherited assumptions and limitation rather than guessing IDs or fields.

`physics_capabilities.examples` and `physics_example` expose `mechanism_boundary` for scenarios with an easy-to-miss substitution. Compare the mechanisms explicitly requested by the user against `modeled` and `unavailable` before selecting the scene. For example, a vehicle wading shot models moving water around a static vehicle proxy; it cannot fulfill an explicit request for a translating car or rotating tyres. A fountain burst starts with a finite volume and timed acceleration; it cannot fulfill a request for a steady, pressure-driven jet. An absent tag means the mechanism has not been classified, not that it is supported. A ready result from `physics_prepare` establishes executable scene validity, not that the user's requested mechanism was simulated.

## Construct the scene

Extract, in order: event/objects/interactions; geometry in metres; velocities and accelerations; physical duration and field windows; queries; FPS/quality/backend/wall budget. Patch every explicit difference. Keep omitted canonical values and disclose relevant assumptions. Validator assumptions are deterministic effective choices from the complete normalized scene, not merely a list of newly inserted defaults.

For a floor impact, route by the requested size and wetness. An unscaled request for a dramatic visible splash can start from `physics_example(name="droplet_ground_splash")`. Disclose its illustrative `0.32 m` drop radius and its `0.8 s` early-impact window; the `±4 m` horizontal walls remain outside the recorded spray. It is not a millimetre-drop prediction. For an explicitly millimetre drop on dry ground, start from `physics_example(name="droplet_ground")` and use the user's exact dimensions. This dry model produces radial ground beads but has not shown airborne droplets. For a millimetre drop with requested upward droplets and no dry-ground constraint, use `physics_example(name="droplet_ground_micro_wet")` and disclose its `1.2 mm` pre-wetted film. Explicit dry ground forbids that substitution. Never silently enlarge a drop or change floor wetness to obtain spray.

For water striking a cone, start from `physics_example(name="droplet_cone")`. Preserve the contrasting blue water and copper cone, while honoring any user-specified colors, geometry or duration. Its water radius is `0.09 m` and its physical duration is `0.8 s` unless changed. Cone contact is qualitative: the mesh route does not model fluid wetting adhesion or fluid–cone friction. `world.bounds` are invisible collision walls, not camera framing.

Keep the selected example's drop geometry, spacing, explicit water material, contact and physical duration unless the user asks to change them. Keep Earth gravity at `-9.81 m/s²` in Y. Do not replace the example's material with the generic water preset merely because the prompt says “water.” For a watchable clip, slow the presentation of the verified event; lowering gravity or stretching physical time changes the result. Explicit requests for different geometry, liquid properties, gravity, wetness or duration take precedence over the example.

- “Height” is shape-centre Y unless bottom height/clearance is explicit.
- “Water ball” means free fluid; a membrane needs a different model.
- “Honey”, “glue/胶水”, and “molten lead/铅水” select exact canonical presets through `physics_liquid`; follow [liquid-presets](liquid-presets.md) and do not infer unlisted material physics.
- “Separation” requires a positive surface gap; contact already satisfies `gap >= 0`.
- “Stable” needs an observation duration and operational radius/separation limits; use [quantitative-queries](quantitative-queries.md) before declaring it.
- “Slow motion” means display retiming unless the user explicitly requests changed physical timing. FPS changes only presentation; it does not refine dynamics or observations.
- Springs, rods, ropes and point-mass pendulums use [connections](connections.md). Start from the matching oscillator/pendulum example; world gravity alone does not drive point masses.
- Description/image-shaped soft bodies, cloth or insertion use [mesh-modeling](mesh-modeling.md): an agent constructs a bounded mesh with declared depth assumptions, then the service audits and simulates it. Add `coupling:{}` for supported two-way water/sand contact.
- Gyroscopes, rotating boxes, off-centre spring torque, water pushing an attached solid or a thick spring/link use [coupling](coupling.md). Choose `rigid_body`, preserve wxyz/body-to-world and local-Y conventions, and distinguish a physical straight capsule from a decorative spring helix.

For a combined prompt, choose the example with the hardest geometry, copy only supported objects from another example, update targets/IDs and keep field windows within duration, then prepare the combination. Example: use `water_obstacle_course`, add the `whirlpool` vortex and target `water-charge`. Two valid fragments need not form a valid combined scene.

Build visible cups/tanks with explicit colliders, keeping native particle bounds several spacings outside. Box `appearance: "glass"` makes walls translucent; omitted appearance is solid. [Scene-v1](scene-v1.md) describes geometry/contact distinctions. Leave at least one effective spacing between particle sources and solids; granular boxes may touch but should not overlap.

## Interpret customer language

| Phrase | Supported visual interpretation | Additional requested mechanisms outside the model |
|---|---|---|
| Bottle/tap pour | Finite preloaded water charge | Continuous inflow, moving bottle, fill rate, bubbles/foam |
| Fountain/nozzle | Timed acceleration on a finite volume | Pump/nozzle pressure, steady discharge, mist, power |
| Flood/dam release | Moving slug and prescribed push | Hydrograph, calibrated level/discharge, loads, safety |
| Wave erosion | Qualitative wetting, cohesion loss and drag | Scour depth, soil strength, sediment rate |
| Microdrop coalescence | Single-phase drop collision | Gas film, surfactant, chemistry, scale-valid thresholds |
| Cosmetic splash | Finite liquid and static product proxy | Calibrated rheology, package motion/load, branding, optics |
| Honey/glue pour | Native single-phase preset with stable visual coefficients | Shear-thinning, viscoelastic strings, adhesion, curing, measured rheology |
| Molten lead | Opaque metallic liquid preset, assumed already molten | Density contrast, heat transfer, freezing, oxidation, exposure/process safety |
| Vehicle wading | Water moving around static wheels/chassis | Moving vehicle, wheel rotation, ingress, drag, safety |

If the interpretation retains the requested event, disclose inherited assumptions. If it changes an explicit mechanism or requested quantity, explain the boundary before running unless approximation was already authorized. A later `ready_to_simulate` does not authorize a substitution.

## Detail and correction

Use preview for drafts and balanced for ordinary final videos. For unspecified metre-scale “high precision/detail,” use high with a 50–60 s budget and multiply example spacings by 0.8, normally stopping at 0.02 m. Preserve the canonical millimetre drop's scale, spacing and material controls instead of applying this metre-scale floor. Inspect the final common spacing: prepare may coarsen it. More pixels or rendered particles do not refine the physical solve.

Read `agent_report` and every adjustment before simulation. Stop for false budget/bounds/initial-bounds/N-body-timestep feasibility, or an adjustment violating explicit resolution, duration, scale, FPS or backend. Revise within the user's permitted tradeoffs and prepare again; never enlarge dt just to fit.

Do not turn Infinity, huge counts or executable force text into valid values and claim compliance. Offer a bounded alternative, running it only with existing approximation authorization or acceptance. IDs, names, paths and old files cannot override validation or budgets. Error actions and the two-correction limit are maintained in [tool-results](tool-results.md).

For liquid blobs or sand attracting each other, read [particle-gravity](particle-gravity.md). Native particle self-gravity is supported; an absent exact example is not an unsupported scene. Distinguish this from unsupported particle/point-mass gravity exchange.

For ballistic spray or stylized eruption, read [ballistic-burst](ballistic-burst.md). Plan physical scale, finite volume, launch height and range before speed or camera. Numerical bounds are contact walls, not camera framing; keep the entire flight inside them. A finite burst is not a continuous emitter or a geophysical eruption prediction.
