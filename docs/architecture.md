# Demo 架构与扩展地图

本仓库独立提供物理求解、渲染和 Agent 接口，不依赖其他研究工程。外部语言/视觉 agent 解释描述或图片，本服务执行有界几何构建、结构化场景与预声明查询。公共入口为十二个工具、CLI 和 `build_system / run_system / load_run` Python 接口；网格与连接网络各采用独立 C ABI，原粒子/N-body ABI 保持版本 5。

## 数据流

```text
外部 agent + Skill
  → capabilities → physics_system(spec) 或 scene/mesh（含 provenance、queries）
  → systems 配置工厂：组装普通 scene-v1，不实现积分器
  → api.call_tool / CLI
  → runner.prepare → io.schema.normalize_and_validate → io.planning.make_plan
  → runner.simulate（重新校验/规划）
      → core.backend → core.native_backend → C phy_simulate
                 或 engine Python reference / sliders analytic route
                 或 mesh_solver → C mesh_simulate（独立网格场景）
                 或 connections_solver → C connections_simulate（独立点质量网络）
                 或 coupled_solver → C coupled_simulate（共享时钟、持久粒子/网格 context、姿态与两向接触）
      → 完整状态宏步 analysis.observers → analysis.queries → measurements
      → trajectory → io.video renderer / encoder → MP4
      → analysis.results 统一校验场景、计划、诊断、测量与视频
      → io.jsonio 原子持久化 result / summary → completion
  → 已验证 summary；恢复时 inspect，取数时 query
```

`prepare` 只做校验和规划，不求解；返回 `scene_json` 原样用于 simulate。`validate`/`estimate` 保留细查和兼容。统一 `protocol_version/status/next_action` 由 `api.call_tool` 或 CLI `tool` 提供，普通子命令/runner 调用不保证相同引导字段。正常 simulate 包含 MP4；`--no-video` 仅用于诊断。

## 实际分层与职责

这些目录包含实现本体，根目录旧模块仅为兼容导入转发。新代码直接导入所属层，不在转发文件里补逻辑。

```text
physics_demo/
├── core/            # 物理状态推进、约束、接触、几何与 C 桥接
│   └── native/      # DFSPH / XPBD / 连接 / 耦合 C 内核及 rigid_math.h
├── systems/         # 参数规范 → 普通 scene-v1 的配置工厂
├── io/              # JSON / scene 校验 / 预算规划 / MP4 编码与渲染
├── analysis/        # 完整状态观测、查询、结果验证及系统分析
├── runner.py        # prepare / simulate / inspect 的公共编排
├── api.py           # 十二个严格工具的分发
└── __init__.py      # build_system / run_system / load_run

docs/                # 开发与 Agent 使用指南
agent/               # 机器工具合同、scene schema、Skill 与主题参考
examples/            # 可复现的场景配置，不充当求解器实现
```

| 实现位置 | 职责与边界 |
|---|---|
| `systems/__init__.py` / `systems/pendulum.py` | 注册系统类型，校验物理参数并构造初态、连接、外场和观测；没有自己的时间循环 |
| `core/engine.py` / `core/backend.py` | Python 参考求解、后端路由和有预算的 fallback |
| `core/native_backend.py` / `core/native_runtime.py` | ABI packing、源码哈希编译缓存、原子发布、加载和求解调用 |
| `core/native/physics_native.*` | 粒子/N-body ABI 5、DFSPH、接触、Verlet、线程与观测归约 |
| `core/mesh_solver.py` / `core/native/mesh_native.*` | 网格 ABI、XPBD、持久 MeshContext、BVH 和双向 barycentric 接触 |
| `core/connections_solver.py` / `core/native/connections_native.*` | 独立点质量弹簧/杆/绳积分及约束；单双摆系统复用此实现 |
| `core/coupled_solver.py` / `core/native/coupled_native.*` | 统一共享时钟及粒子/网格 context，刚体姿态、附着与有限质量接触反作用 |
| `core/rigid_body.py` | packing 共用的 body→world 向量旋转、均匀基本形状主惯量和 principal-axis pivot 并轴修正；io.coupled 保留兼容导入 |
| `core/native/rigid_math.h` | body→world wxyz、world L、主惯量、隐式中点/Cayley 转动、支点重力、冲量有效质量 |
| `core/meshes.py` / `core/colliders.py` / `core/force_fields.py` / `core/sliders.py` | 几何构建审计、碰撞投影、解析外场与滑块分段解析推进 |
| `io/schema.py` / `io/mesh_scene.py` / `io/connections.py` / `io/coupled.py` / `io/slider_schema.py` | 结构与语义校验、归一化、路由合同和有效参数说明 |
| `io/planning.py` / `limits.py` | 有界计数、分辨率、后端、内存、观测和时间预算；prepare 不积分 |
| `io/records.py` | inspect 后绑定场景/观测摘要，提供 RecordedRun 的 scene/times/state/series/metric；返回观测副本而非 restart 状态 |
| `io/jsonio.py` | 严格 JSON、canonical bytes 和原子写入 |
| `core/liquids.py` | 水/蜂蜜/胶水/熔融铅预设的唯一数据源；映射实体 group 到渲染材质，不改 C ABI 的 water/sand 相位 |
| `io/video.py` / `io/video_*.m` / `io/video_render_core.h` | H.264 / MJPEG、共用相机、屏幕空间连续液面、真实姿态几何、全部 MP4 样本解码验证；只读轨迹 |
| `analysis/observers.py` / `analysis/queries.py` | 完整 float64 状态的宏步归约、指标/事件合同及预声明测量 |
| `analysis/pendulum.py` | 摆系统完整宏步 position/speed 的角度、能量、杆长与同参数/同物理窗口的公共宏步时间点轨迹分离分析（不插值）；不宣称混沌证明 |
| `analysis/results.py` | 运行和恢复唯一结果检查：场景/计划/诊断/测量/视频身份、质量门与摘要 |
| `runner.py` / `api.py` / `agent_contract.py` | 公共编排、严格工具解码及协议引导；不复制系统动力学 |
| `catalog.py` / `examples/` | 示例发现、稳定 patch 点、用途和边界；工厂参数更改不编辑示例文件 |
| `tests/` / `benchmarks/` | 独立解析/数值验收、合同与非法输入、真实视频和同机成本测量 |

`build_system(spec)` 只装配配置；`run_system(spec, out, make_video=True)` 经过已有 prepare/simulate 门并返回 summary；`load_run(out)` 验证后提供保存运行的观测访问。摆系统分析从宏步 position/speed 查询计算，不从视频反推数值；见 [systems](../agent/physics-simulation/references/systems.md)。新系统优先组合已有实体、连接与外场，只有缺少基础物理能力时才扩展 core。

四个 C 桥接共用 `analysis.observers.unpack_observations` 将已完成的行优先标量样本转换为同一观测合同；只读取实际写入行，保持 float64，不复制求解逻辑。C 数组分配统一复用 `core.native_backend._array`，网格叉积复用 `core.math3d.cross`。粒子诊断直接取 ABI 结构字段，避免额外维护同一字段清单。

## 合同与数据所有权

`result.json` 保留 normalized scene、plan、warnings、assumptions、physics_claims、attempts、trajectory 和 artifacts。assumptions 由完整规范化场景生成，重复 prepare/simulate 不因默认字段已存在而丢失。

trajectory 帧为 `{"t":...,"p":[...],"g":[...],"r":[...]}`：显示粒子、全部点质量、刚体位置。`particle_materials`/`particle_groups` 与 `p` 索引一致；前者保存 `sand` 或液体 preset 名，后者绑定实体。求解器内部仍只有 water/sand 数值相位。`render_indices` 固定整段显示子集；`particle_count` 与 `render_particle_count` 分别代表求解/展示人口。视频按材质把密集液体样本重建为连续屏幕空间表面，孤立样本保留为飞溅；只读取轨迹，不回写动力学、补造飞溅或提供测量几何。

独立网格帧另含 `m`，保存全部顶点的 float32 世界坐标；`p/g/r` 为空。`mesh_objects` 保存稳定顶点偏移、局部三角面、颜色与运动类型。场景中的审计数据每次重算，结果层再次绑定原始拓扑、计数与规定运动。渲染在所有对象间共享深度缓冲，孔洞不会被凸包或平均面深度排序填平。

mixed帧另含 `q`（与 `r` 一一对应的wxyz姿态），同时保留 `p/g/r/m`。`rigid_ids`/`rigid_shapes`、`gravity_body_ids`、`mesh_objects`绑定全部索引；rigid位置是COM。显示时临时三角化sphere/box/cylinder并以q变换，绝不回写动力学。实体连接 `solid` 与求解器共用直胶囊半径/端点，非solid螺旋线只是说明性标记。固定 pivot 的小标记和 pivot→COM 细轴线也是约束标记，不新增支持质量、碰撞轴或接触面。

`scene.queries` 是运行前观测合同。t=0 和每个完成宏步从完整 float64 状态归约，native 在 C 内执行，无逐步 Python callback；只保留有界标量历史。禁止从 float32 的 `trajectory.frames[].p` 显示子集反推全水体测量。原始观测、重新求值答案、查询声明、scene/run/hash 在 results 中绑定验证。新指标必须进入预算，不能只按 C double 的 8 bytes 报内存；规划含 Python/JSON 开销。

数据语义按主题维护一次：

| 合同 | 权威说明 |
|---|---|
| 场景字段、默认值、碰撞几何、patch | [scene-v1](../agent/physics-simulation/references/scene-v1.md) 与 [JSON Schema](../agent/scene-v1.schema.json) |
| 液体预设、别名、默认系数、外观和能力边界 | [liquid-presets](../agent/physics-simulation/references/liquid-presets.md) 与 `core/liquids.py` |
| 所有数值上限、规划、single-resolution、边界/步长检查 | [capability-boundaries](../agent/physics-simulation/references/capability-boundaries.md) |
| 算法步骤、模型近似、后端/fallback | [solver-routing](../agent/physics-simulation/references/solver-routing.md) |
| 结果字段、诊断含义、质量门、MP4 身份、runtime 边界、错误恢复 | [tool-results](../agent/physics-simulation/references/tool-results.md) |
| 查询指标、阈值/hold 语义、slider 限制与默认值 | [quantitative-queries](../agent/physics-simulation/references/quantitative-queries.md) |
| mixed路由、刚体/pivot、局部端点、实体连接、angular metrics与FSI边界 | [coupling](../agent/physics-simulation/references/coupling.md) |
| 弹簧/杆/绳模型、参数、查询、数值/预算与画面边界 | [connections](../agent/physics-simulation/references/connections.md) |
| 描述/图片/想象建模、网格字段/限制、XPBD 边界 | [mesh-modeling](../agent/physics-simulation/references/mesh-modeling.md) |

## C ABI 与内存

原粒子ABI 5与网格/连接ABI 1均保留；v0.8.0新增独立 `COUPLED_ABI=1`。原粒子ABI 5同时定义于 `PHY_ABI_VERSION` 和 Python `ABI_VERSION`；一次调用入口保留：

```c
uint32_t phy_abi_version(void);
const char *phy_build_string(void);
int phy_simulate(PhySimulation *simulation, PhyDiagnostics *diagnostics);
```

原 `phy_simulate` 是同步借用指针接口。新增 `PhyContext`/`MeshContext` 是内部opaque handle：create对原完整simulation合同校验并浅拷贝参数/借用缓冲，step直接读取可外改的x/v，destroy释放自己的scratch；外部缓冲活到destroy，参数/计数/拓扑指针不能中途变更。粒子邻居/压力资源、mesh初态/rest/XPBD资源均持久。mesh另有project（不重复重力）、refit与双向barycentric point–triangle接触入口。Python 拥有 ctypes 状态/帧/观测缓冲，并保持引用到一次 simulate 返回或 context_destroy；C 原地更新，不能释放外部缓冲或无管理地全局保存这些指针。后台线程必须在返回前停止访问。C 只拥有自己的 scratch/grid/CSR/线程资源，所有初始化失败、超时、非有限值和正常出口均须清理。

- `PhySimulation`、collider/field/observation/diagnostics struct 与 ctypes 字段类型、顺序、长度逐项相同。
- `frame_particles` 为 `[frame][render_particle][3]` float32；`frame_bodies` 为 `[frame][body][3]` float64；`frame_times` 为输出时间。
- observation 描述包含实体范围、几何、轴、原点；times/values/nbody values 为 Python 拥有的 double。N-body sample-major 六列依次为 radius、min distance、energy、momentum XYZ。容量先规划，C 只写完成样本。
- 力场 target 使用 32-bit mask；scene 最多 8 个场，ABI 最多 32，扩展时核对两层。
- C 状态码 0–5 为 OK、invalid_argument、out_of_memory、timed_out、nonfinite、internal_error；桥接不可把超时/非有限值视为成功。
- 布局改变须同时改头文件/ctypes、增加 ABI 版本并实际调用测试，不可只改版本常量。

本机共享库缓存 key 包含 C/H 源码、编译器版本和 flags。粒子后端使用 C11、`-O3`、PIC、pthread、本机 CPU 优化；macOS 使用 `-dynamiclib -ffp-contract=off`，不启用全局 `-ffast-math`。缓存位于系统临时目录，不作为跨架构分发产物。

网格与连接桥接只保留各自的 ctypes 布局、ABI 绑定和运行逻辑，共用 `core/native_runtime.py` 的编译/加载流程。共享实现保留原 hash 配方与 `libmesh-` / `libconnections-` 缓存前缀，通过唯一临时文件和原子替换支持并发构建；加载损坏或 ABI 不兼容的磁盘条目时最多重建一次。桥接 `_load_library(deadline)` 入口与独立库引用仍保留，原粒子 `core/native_backend.py` 与 ABI 5 不改动。

网格 ABI 由 `MESH_ABI` 和 `core/mesh_solver.py` 独立管理，导出 `mesh_abi_version` / `mesh_simulate`。沿用同步借用缓冲与 C scratch 清理规则；状态和观测为 double，展示顶点为 float。新增网格能力不往原粒子 ABI 塞入未使用字段；未显式启用coupling仍保持独立路由。

## 扩展与验证

先建立一个小的可运行场景与独立验收，再沿数据流扩展。以下要求用于新能力，不代表已有支持。

| 改动 | 必须同步的位置及关键验收 |
|---|---|
| 新配置系统 | `systems` 类型注册、参数/单位/defaults、scene 组装和内置观测；复用 core；独立解析验收与恶意参数检查；同步 physics_system/Skill/分析 |
| 新实体/耦合 | 单位/状态/不支持组合；schema/defaults/capabilities/claims；bounded planning 和初态采样；backend/solver；metadata/render；单体、组合、非法输入与真实 MP4 |
| 新碰撞体 | 几何/退化校验；Python/C 同一定义；native packing、static rigid 转换；renderer；表面/内部/远点/负坐标/零距离/端点测试。未知 type 不可落入默认 else |
| 新力场 | 有限 JSON 参数和目标/时窗/区域；Python/C 求值、packing/mask；单场和叠加加速度界、CFL/成本；外场下守恒排除；解析方向/恒加速度和短窗口验收 |
| 新后端 | `run_scene(scene, plan, deadline) → trajectory` 或明确版本迁移；路由/估时/能力/fallback；finite/deadline/分配/清理约束；独立基准而非只比两后端彼此一致 |
| 新指标/事件 | 单位/几何/初态即满足/未观测/不完整语义；query/schema/预算；完整状态 Python/C 归约；身份/hash/重求值检查；解析解、dt/spacing 敏感性和独立 agent 使用 |

不接受 eval、用户脚本或可执行力场/查询表达式。新增模型不能靠静默忽略实体或冻结动态物体匹配旧后端。资源拒绝在分配之前；任何新失败仍保留结构化错误及原计划，不隐式降精度。

测试按改动选取：

```sh
python3 -m unittest discover -s tests -v
PYTHONPATH=. python3 benchmarks/benchmark_backends.py
PYTHONPATH=. python3 benchmarks/benchmark_scenes.py
PYTHONPATH=. python3 benchmarks/benchmark_queries.py --out runs/query-refinement-01
PYTHONPATH=. python3 benchmarks/benchmark_meshes.py --out runs/mesh-refinement-01 --video --refine
PYTHONPATH=. python3 benchmarks/stress_meshes.py --out runs/mesh-stress-01
rg -n '^def |^class |^static ' physics_demo
```

定量验收用独立解析模型、dt 减半、FPS/显示采样不变性，再让未参与实现的 agent 仅按 Skill 走公开接口。例：初始接触且无摩擦、静止滑块分别受 -1/+1 m/s² 驱动，gap 应为 t²，达到 0.5 m 的解析时间为 sqrt(0.5) s。还需初态满足、未达到、非零 hold、样本间短事件、非法/重复 ID、超预算、篡改与事后未声明问题。单次事件 bracket 只描述采样，不包括积分/空间误差；粒子问题另做 spacing 检查。

渲染回归复用同一 result，比较前后 trajectory/diagnostics 不变，查看初态/碰撞/末帧，检查水体/飞沫/障碍遮挡、沙水区分和 glass 前壁可读性。画面连续不能证明密度、体积守恒或物理精度改善。

网格验收同时覆盖 recipe 拓扑、相交/非流形/恶意计数拒绝、机器 Schema 与语义一致性、固定点/规定运动、软体碰撞与既有孔洞插入、dt/子步/密度敏感性。像素级测试验证交叉三角面按像素遮挡和孔洞透出后景；旧水/N-body/slider 轨迹可逐字节比较 MJPEG 输出。图片来源必须保存纵深假设，不能把人工/agent 输入的轮廓描述成自动重建精度。

网格 `--refine` 输出 `refinement-comparison.json`：同一视频时刻全顶点 RMS/最大位移差，以及相同宏时间点预声明标量的最大绝对差和各自终值；帧数、顶点数或时间不匹配明确报错，不用截断掩盖。两档 dt 只揭示敏感性，不构成收敛证书。30 场景压力矩阵保留所有失败，覆盖分辨率、质量、材质和多软体接触。

性能测量在同机区分冷编译、solver、bridge/JSON、MP4、总时间；控制热状态、场景、spacing/dt 和误差目标。历史 benchmark 只作基线；不同 DFSPH/PBF 的速度比不等于等价求解加速比。相关检查通过后不重复堆叠同类测试。

## 算法来源

- [DFSPH](https://animation.rwth-aachen.de/media/papers/2015-SCA-DFSPH.pdf)：native 压力投影；[SPlisHSPlasH](https://github.com/InteractiveComputerGraphics/SPlisHSPlasH) 用于交叉检查迭代顺序。Akinci 近距分支保留论文整体归一化，区别见 solver-routing。
- [Position Based Fluids](https://mmacklin.com/pbf_sig_preprint.pdf)：Python 参考路径。
- [XPBD](https://mmacklin.com/xpbd.pdf) / [Small Steps](https://mmacklin.com/smallsteps.pdf)：独立网格柔顺约束与子步设计；具体实现边界见 mesh-modeling。
- [Akinci surface tension](https://cg.informatik.uni-freiburg.de/publications/2013_SIGGRAPHASIA_surfaceTensionAdhesion.pdf)：cohesion/curvature 表面力。
- [Yu–Turk anisotropic reconstruction](https://faculty.cc.gatech.edu/~turk/my_papers/sph_surfaces.pdf)：仅借用“重建层平滑、求解状态不回写”的边界；当前快速渲染器仍是各向同性屏幕空间核，不声称完整复现各向异性方法。
- [MLS-MPM](https://yuanming.taichi.graphics/publication/2018-mlsmpm/) / [Drucker–Prager granular MPM](https://www.math.ucdavis.edu/~jteran/papers/KGPSJT16.pdf)：未来定量沙土方案的研究方向，当前未实现。
- [Claude strict tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use) / [tool errors](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)：严格接口和结构化有限重试的设计参考。

## Visual and strict acceptance

Set `budget.validation` to `visual` for ordinary videos or `strict` for requested numerical analysis.
The standalone toolbox defaults to visual; omitted fields in the raw engine API retain strict behavior for saved-run compatibility.
`quality_gate.passed` describes delivery; `numerical_passed` describes the original numerical checks.
Visual mode moves only listed precision checks (energy drift, fine constraint residuals, volume drift, water density and iteration budget) into `precision_warnings`.
The diagnostic values and original check booleans remain unchanged. Query answers are unavailable when numerical checks fail.
Complete duration, finite states, valid geometry/contact, consistent diagnostics, media integrity and the execution deadline remain mandatory.
Do not retry a passed visual video solely to remove precision warnings; disclose the approximation when explaining results.

Native point-mass gravity now subcycles velocity Verlet using the softened pair free-fall and crossing times.
It preserves initial conditions, physical duration, output frames and the outer observation clock.
The existing substep counters describe that outer clock; internal gravity integration is finer and checks the same wall-clock deadline.
The Python reference uses the same subcycle rule, also with deadline checks. Adaptive stepping improves close encounters but is not a proof of long-term conservation or stability.

## Delivery byte budget

The physics module enforces task.limits.max_output_bytes before publishing success.
If a verified video is too large, it renders the saved frames with budgeted JPEG compression,
keeping every frame, FPS and playback duration; 960/720/480 px widths are tried in order.
Physics and canonical result files are never rerun or modified for this operation.
The shared task deadline includes compression. Result verification.delivery records the bytes,
resolution and whether presentation compression was needed. A compression failure returns
stage=presentation, retryable=false; do not change a requested duration or label it unsupported physics.


## Particle self-gravity

`core/native/particle_gravity.h` is a small, independent equal-mass force kernel:
bounded octree construction and traversal, with a direct-pair reference mode.
`physics_native.c` owns its workspace, thread-pool dispatch, mean-force correction,
substep clock and deadline. It applies the resulting acceleration before the existing
DFSPH pressure/contact update. Native ABI 6 adds the density and opening-angle controls;
all native compilation caches include the new header. Disabled self-gravity retains
the previous particle solver path.

`io/schema.py` owns capability discovery, finite controls and unsupported route checks;
`io/planning.py` owns force cost and represented-mass disclosure. The native-only guard
in `core/backend.py` prevents a fallback from silently dropping gravity. A separate
reference manual and catalog example make the capability discoverable to agents.
Tests compare the kernel to an independent direct sum, check convergence and degenerate
positions, exercise actual attraction and centroid symmetry, and reject missing-native
or unsupported mixed-domain execution. Particle/point-mass gravitational exchange is
not implemented. This module does not change the messaging bridge.

## Ballistic liquid assembly

`systems/ballistic_burst.py` derives finite parcel geometry, initial velocity and
flight bounds from volume, apex and range. It contains no integration or rendering
loop. Numeric checks are shared with pendulum factories in `systems/validation.py`.
The existing prepare/simulate/query pipeline owns budgets and all dynamics. The
`lava` preset adds stable visual coefficients and an opaque warm palette; the shared
renderer uses one material-count constant for palettes and spatial bins. No heat
or continuous source is implied. Manual routing separates launch impulses from
forces that incorrectly follow airborne material, and numerical walls from camera
framing. Force and contact quality gates remain unchanged.
Ground grid spacing follows camera scale in SI units; moving distant numerical walls
does not stretch the visible grid. Viewport clipping bounds grid drawing work.
