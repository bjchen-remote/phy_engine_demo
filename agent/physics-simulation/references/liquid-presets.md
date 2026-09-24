# Liquid presets and continuous-surface rendering

First load, build, or author the complete scene and identify its target fluid entity ID. Use `physics_liquid` when adding a named liquid or changing an entity to water, honey, glue, lava, or molten lead, and apply its `patch_arguments.operations` with `physics_patch`. If an example already has the requested preset and explicit `properties`, preserve those properties: reapplying the generic preset would erase deliberate visual controls. Keep the explicit water material in the `droplet_ground`, `droplet_ground_micro_wet`, `droplet_ground_splash` and `droplet_cone` examples unless the user asks for a material change. The liquid response contains no `scene_json`; if no scene exists yet, obtain one and call `physics_liquid` again before patching. Do not guess coefficients.

| User wording | Canonical preset | Default model controls | Appearance |
|---|---|---|---|
| water, 水 | `water` | viscosity `0.03`, surface tension `0.05` | clear blue, translucent |
| honey, 蜂蜜 | `honey` | viscosity `0.35`, surface tension `0.08` | amber and depth-darkened |
| glue, 胶水, 黏胶, 白胶 | `glue` | viscosity `0.70`, surface tension `0.12` | milky and mostly opaque |
| lava, magma, 熔岩, 岩浆 | `lava` | viscosity `0.50`, surface tension `0.08` | opaque orange-red, luminous display tint |
| molten lead, liquid lead, 铅水, 熔融铅 | `molten_lead` | viscosity `0.05`, surface tension `0.18` | opaque silver-grey metal |

Example:

```json
{"tool":"physics_liquid","arguments":{"preset":"honey","entity_id":"pour"}}
```

The response supplies complete `entity_fields` plus two atomic patch operations for `/entities/@pour/preset` and `/entities/@pour/properties`. A scene may also set these fields directly:

```json
{
  "id":"pour", "type":"fluid", "preset":"honey",
  "shape":{"type":"sphere","center":[0,1.6,0],"radius":0.35},
  "spacing":0.05, "velocity":[0,-0.2,0]
}
```

If `properties` is omitted, normalization expands the preset defaults. Explicit `properties` are intentional overrides and must be reported with the final effective values. Non-water presets require `auto` or `native`; they never silently fall back to the Python reference liquid.

All presets remain one fluid pressure phase with the engine's shared reference density. They do not provide real density contrast, immiscibility, diffusion, thermal transport, cooling, freezing, boiling, oxidation, toxicity, curing, adhesion, viscoelastic strings, or calibrated non-Newtonian rheology. `molten_lead` assumes an already-liquid visual material; it is not a heat or safety model.

The renderer reconstructs a smooth screen-space surface from recorded particles and applies one material palette. This hides dense simulation samples while retaining separated spray. It does not create a 3D free-surface mesh, add particles, alter collisions, or supply measurement geometry. Quantitative queries continue to read full solver state; never measure spread, volume, or contact from pixels.

Use `liquid_preset_showcase` to compare the original four options under equal drop geometry. Because the four volumes share one numerical phase, use it as a visual/behavior demonstration rather than evidence of real multi-material mixing.

Use `lava` for orange-red visual ejecta. It adds no heat, cooling, gas, crust or eruption pressure. A luminous tint is a display cue only. For a finite burst, use the [ballistic-burst](ballistic-burst.md) factory and its volume/flight controls.
