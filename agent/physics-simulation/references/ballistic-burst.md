# Finite ballistic liquid bursts

Use `physics_system` with this `spec_json` for a finite spray, fountain pulse or
stylized ejecta. The factory builds ordinary fluid spheres with initial velocities;
the existing DFSPH/contact solver computes every frame. It does not prescribe paths.

```json
{"type":"ballistic_burst", "source_height":0.9, "source_radius":0.35,
 "apex_height":0.9, "spread_radius":1.2, "volume":0.05,
 "parcel_count":8, "spacing":0.03, "preset":"lava",
 "gravity":9.81, "duration":3.2, "dt":0.002, "output_fps":24,
 "quality":"balanced", "validation":"visual", "wall_time_s":180}
```

All lengths are metres, volume is cubic metres, and time is seconds.
`source_height` is the centre height above the ground; `source_radius` is the
horizontal launch ring radius. One parcel requires `source_radius=0`.
`volume` is the total geometric volume, divided among `parcel_count` equal spheres;
it is not the number of rendered dots. The sampled volume is approximate and changes
with effective spacing. Preserve explicit volume and check planning adjustments.

`apex_height` is the maximum rise above launch. Alternating parcels use 100% and
75% of this rise to spread the burst. The initial vertical speed is `sqrt(2*g*h)`.
`spread_radius` is each centre's additional horizontal travel by its ideal ground
landing time `(vy + sqrt(vy² + 2*g*source_height))/g`. These are initial-condition
estimates: pressure, deformation, obstacles and ground contact can change the paths.
They are not a trajectory guarantee. Lower the apex to reduce ejection speed;
do not reverse gravity or apply an upward force to all airborne matter.

The factory rejects overlapping launch parcels or parcels too small for the chosen
spacing. Enlarge the launch ring or reduce parcel count before reducing volume.
It provides a ground plane and a conservative flight envelope, with horizontal
room for impact redirecting vertical kinetic energy. This is not a guarantee for
arbitrary added forces or contacts; check the resulting trajectory. Native
world bounds are physical walls; never shrink them to zoom the camera. The renderer
already fits the recorded geometry and trajectory. After adding terrain, verify
initial clearance and the full flight again. Use `physics_mesh` for static terrain;
a closed flat cone cap is not a crater opening.

Limits: 1–24 parcels; source height/apex `(0,100]`, source/spread radius `[0,100]`,
volume `[1e-9,100]`, spacing `[0.0001,0.5]`, gravity `[1e-9,250]`, duration
`[0.0001,30]`, dt `[0.0001,0.05]`, integer FPS `[1,60]`, wall time `[1,300]`.
Normal scene validation and resource planning still apply. Unknown fields, nonfinite
numbers and executable text are rejected. Defaults are shown above except the factory
wall-time default is 60 s and material default is water.

For lava choose `preset= lava`: opaque orange-red with a display glow. Silver
`molten_lead` is a different material. This models one finite, already ejected,
viscous liquid burst. It has no continuous emitter, gas pressure, smoke, heat,
cooling, crust or geophysical scale prediction. Disclose that approximation.
For a genuinely continuous or quantitative eruption request, explain the missing
physics instead of presenting a finite burst as equivalent.
