# Bounded surface cleanup validation

The 0.3.2 modeling candidate adds optional `display_cleanup=surface` under
`bounded-surface/1`. Image generation keeps its conservative default. A sealed
model in the same task can be cleaned through `modeling_preview_cleanup` without
loading neural weights, then rendered using its returned model reference.

## Algorithm and evidence boundary

The filter first removes only eligible complete detached micro-components under
area, extent, exact surface-distance and cumulative-loss budgets. It then applies
six Taubin pairs to eligible vertices. Boundaries, abnormal topology, very narrow
triangles and sharp creases protect their neighborhoods. Every proposal checks
triangle orientation and area; emitted coordinates are limited by both local edge
length and one percent of the main component extent. No retained connected face
is deleted and no hole is filled. The fixed policy is in the sealed cleanup
receipt, rather than supplied by a model prompt.

The positive/negative Taubin steps reduce the shrinkage of a simple Laplacian
filter; they do not guarantee exact volume preservation. See
[Taubin's paper](https://www.cs.jhu.edu/~misha/Fall07/Papers/Taubin95.pdf) and
[the author's IBM abstract](https://research.ibm.com/publications/signal-processing-approach-to-fair-surface-design).

The host's literal standard-library mirror independently reproduces geometry and
all report fields. A worker's hashes, vertex lists or claimed displacement are
insufficient. Result/data v3 disclose coordinate modification and unverified
semantic fidelity; v2 still requires unchanged coordinates of retained raw
triangles. A cleanup-capable pin cannot downgrade its result to v1 to bypass the
raw-to-display check. The raw JSON, OBJ and GLB remain in the delivery ZIP.

## Paired local observation, 2026-10-02

One previously generated single-reference mesh was tested without repeating neural
inference or modifying its original files. This is a paired local observation,
not a representative reconstruction-quality benchmark. Input has 59,491 vertices,
119,890 faces and 42 connected components; the main component accounts for
98.53% of its surface area.

| Measurement | Observed final bounded filter |
| --- | ---: |
| Whole detached components removed | 6; 56 faces; 40 vertices |
| Retained vertices moved | 27,497 |
| Largest displacement / main extent | 0.4651% |
| Main-component Laplacian RMS change | −20.82% |
| Main-component dihedral RMS change | about −1.45% |
| Volume change relative to retained raw geometry | −0.0122% |
| Surface-area change relative to retained raw geometry | −0.5669% |
| New orientation flips / zero-area faces | 0 / 0 |
| Retained face order, winding and topology | preserved |

The Laplacian and angle statistics are geometric proxies. Alone, they do not
establish visual improvement, anatomy, reference identity, printability or
collision-free surfaces. The final raw and cleaned geometry were actually
rendered with matching camera settings: 640 × 640 pixels, 12 fps, three seconds,
10-degree pitch and zero-degree starting yaw. Each native triangle-surface render
encoded a 36-frame H.264 MP4; all 36 frames decoded successfully and contained
visible surface pixels. The renderer retained each input's full triangle set.
No neural inference or QQ delivery occurred during this paired acceptance.

Visual inspection of the final front/back frames shows modest smoothing of small
bumps while the large comb-like back ridges remain prominent. This supports a
limited smoothing claim for this mesh; it does not establish that the back-side
defects are repaired. The final artifact has completed local paired render
acceptance, rather than relying on a nearby parameter experiment.

Python 3.12.14 produced the final mesh and receipt; independent host Python 3.14.7
recomputation matched both canonical objects exactly. The original source SHA-256
is `e0074ed3e120690b479838b312d98afd54a04f8bcc892092ec3192e68481c7c6`.
Private source pictures and meshes are retained only in local acceptance artifacts.

## Reproducible checks

The public standard-library geometry fixtures exercise noisy closed surfaces,
open patches, sharp/skinny features, detached open/flat fragments, component-loss
budgets, anomalous topology, scaling, finite coordinates and forged proofs. CPU
cleanup/export fixtures prohibit importing the neural runner, Torch or MLX and
check sealed source, request, runtime and module pins; incomplete work is kept
without replay. Host fixtures independently validate geometry/export/ZIP bindings
and reject modified coordinates, topology, reports and protocol downgrades.

Real native preview/package checks use explicit triangle fixtures and full MP4
encoding/decoding. They test pipeline packaging, not neural inference or QQ
transport. Real QQ delivery may be claimed only with separate API receipts.

## Stronger filtering experiment

A separate local voxel opening/closing and signed-distance smoothing experiment
changes topology and may erase thin detail or fill internal cavities. It was
tested at 192 cells across the main extent, with radius-one morphology and a
0.6-voxel Gaussian filter on the signed-distance field. A positive extraction
level shifts the surface inward. The original main mesh has one connected
component; the original 35 other retained components and raw files were preserved
throughout these experiments.

| Experimental profile | Extraction level / voxel | New main components | Main volume change |
| --- | ---: | ---: | ---: |
| Opening then closing | 0 | 8 | +8.98% |
| Opening then closing, inward offset | 0.5 | 27 | +4.82% |
| Closing only, inward offset | 0.5 | 8 | +6.21% |
| Closing only, larger inward offset | 1.0 | 75 | +0.51% |

All four profiles fail the guard requiring the main mesh to remain a single
connected component. The offset with the smallest volume error creates the most
new components, so volume agreement alone cannot establish fidelity. Closed
surfaces and consistent winding also do not establish topology preservation,
absence of self-intersections or preservation of fingers and hair. The failed
closing-only outputs were not rendered. Strong voxel remeshing was rejected from
production; it is neither part of `bounded-surface/1` nor an available default
modeling route. The operations are specified in
[SciPy's morphology documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.binary_closing.html)
and [Trimesh's voxel documentation](https://trimesh.org/trimesh.voxel.html).
