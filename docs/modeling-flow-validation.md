# Mac 本地识图建模验证记录

日期：2026-10-01。设备为 Mac mini M4，16 GB 统一内存，macOS arm64。使用公开的 Tencent Hunyuan3D-2mini 标准形状权重、固定 MLX 移植代码与 CPU U2Net；没有训练自有模型，没有调用 Meshy 或其他云端推理服务。算法及许可研究见 [研究记录](modeling-flow-research.md)，职责和热插拔边界见 [整体架构](current-architecture.md)。

## 固定实现与安装

- MLX 移植源码：`292331f4d26ddb80b9dcea6bcb5629ff82f12b82`。
- 官方检查点 revision：`f90a0f7df7d5e6f71109cf333f6a95a0ae3194a6`。
- safetensors：3,819,958,234 字节，SHA256 `3cc66f3bea33e4062b7dbc875ffe1d70c4888914aec3e91b60f94e9bd01b522b`。
- 配置 SHA256：`cabcba7f6115752c8fe5b370e12bf714936f70377a8a80f151872f76c2d64609`。
- U2Net：175,997,641 字节，SHA256 `8d10d2f3bb75ae3b6d527c77944fc5e7dcd94b29809d47a739a7a728a912b491`。
- Python 3.12.14、MLX/MLX Metal 0.31.2、ONNX Runtime 1.24.4；完整依赖记录留在专用运行时，公开安装器提供最小精确依赖锁。
- 原物理引擎仍为 1.4.7，摘要 `f22cb4f59bc3cce75862c7a549656a94ce8c70c17b4762efd228d30399f3f41f`。

权重、独立 Python 环境与模型源码置于 QQ 专用本地运行时，未进入 Git。操作者安装器与推理分开；实际任务 deny-network，模型与来源分别校验。服务提供者由实际本地操作者指定；宿主帮助及生成结果应显示提供者、AI 生成和腾讯无关联/无背书说明，数据 ZIP 附完整许可证。

## 真实图片闭环

原图来自固定 Tencent 源码的 `assets/example_images/004.png` 玩具企鹅样例。测试先合成白背景 JPEG，再经过真实 QQ 输入规范化。因此验收覆盖普通不透明 JPEG，而非仅依赖原透明 PNG。它不是收到的真实 QQ 群消息。

调用公开 API `modeling_from_image`，参数为 seed 42、30 步、octree 128、chunks 2000、最大尺寸 0.3 m 和明确的均质实心密度 1000 kg/m³ 假设。随后真实 `physics_patch`、`physics_prepare`、`physics_simulate`、固定阶段执行、完整视频解码与宿主数据 ZIP 校验全部通过。

| 检查 | 实际结果 |
|---|---|
| 独立 CPU 抠图 smoke | 2.320 s；单样例阈值掩膜与原 alpha 的 IoU 0.99645，不代表任意照片质量 |
| 正式安装路径图片建模 API | 102.855 s，包含本地来源/权重验证、生成、减面和原引擎几何审计 |
| 整个图片→模拟→交付工件闭环 | 129.851 s；不包含真实 QQ 发送 |
| 模拟网格 | 4094 顶点、8188 三角面，单连通、闭合、正体积、面向一致、原引擎自交检查通过 |
| 模拟 | 0.25 s 软三角表面演示；数值质量门通过，不代表实心玩具力学或材料经过标定 |
| MP4 | 152,535 字节，完整解码与宿主验证通过 |
| 完整 ZIP | 6,990,148 字节；解压内容 27,645,134 字节；无损 DEFLATE，无数据抽样 |

ZIP 包含原始数值结果/测量、模型、来源阶段数据、展示 GLB/OBJ、模拟 OBJ、网格 JSON、生成回执及四份许可证。MP4 SHA256 为 `f4f278ca21b5b8c10c598e63000b3daf1d0c0a1d8f965e81d84286e87aca70de`；ZIP SHA256 为 `b9cffd0a25768c92fe04a82005054275fe3ebdbe4699bc4f34b313c4c4a0d2f4`。原始闭环记录留在本机，未纳入公开仓库；报告声明 `live_qq_delivery=false`。

另外的直接 MLX 12 步/64 分辨率实验记录 Metal 峰值约 4.71 GB；这是那次运行的 Metal 分配统计，不是整个流程或系统的峰值内存上界。首次冷加载、分割与高分辨率配置可能更慢，曾观察到约 200 s 的图片生成。不能承诺固定 130 s 或任意图片都适合 16 GB。

## 回归与拒绝行为

客户端完整回归覆盖多图片、引用/转发、消息隔离、反馈、实验续改、OneBot 和独立 API 回执。原引擎 441 项测试通过，6 项可选 jsonschema 检查跳过；客户端最终一轮 485 项通过，2 项独立 Bun CLI 检查跳过。新增模型/安装器/分割/图片门 67 项和阶段/展示/组合 59 项通过（图片门同时在两组发现中重复覆盖，不累加成唯一测试数）。 公开分支完整工具箱发现运行 151 项，仅跳过 1 项未配置私有宿主 SDK 的可选集成；NumPy/Pillow 分割检查已在固定运行时执行。另用明确选定的宿主 SDK 完成机械和 PCB 的真实沙箱验收，宿主相关开发接口测试全部通过。

原机械和 PCB 的真实 native deny-network smoke、旧查询、数据宿主校验及只重新渲染不重新求解的检查通过。大网格 JSON 序列化去除缩进保持所有数值和旧 mesh_ref 插入顺序摘要，未修改物理引擎。ZIP 压缩保持每个成员原始字节与哈希，仍独立检查大小、权限、路径和 CRC，宿主不解压/执行。

复杂猴子样例曾生成 92 个分量，正确拒绝物理接入；企鹅第一次减面候选出现重复/非流形面，拒绝该候选后从原展示网格独立尝试较低面数。未静默删除分量、替换为几何玩具或宣称失败网格可仿真。完整模拟在导出失败时保留，不覆盖失败现场或盲目重算。

## 使用范围与平台验收

适合玩具造型、原型与演示。单图不能测量尺度、壁厚、隐面或材料；当前没有贴图生成、精确 CAD、加工公差、玩具结构安全认证、自碰撞或标定实体 FEM。商业用途仍须遵守当前模型许可、适用地区、提供者/AI 生成披露及引用/第三方权利要求。

QQ 部署和真实平台回执属于独立验收层。本地闭环成功不能当作真实群消息送达证明；不手动向群发送测试消息，也不重放旧请求或 unknown 回执。活动包切换只影响未来事件，旧引擎版本和已有任务 pins 保留以便回滚。

最终部署候选与上述真实图片全闭环包的引擎及三个模块摘要完全一致，适配层另补了旧结构化稠密 mesh-assets 的缩进规范化。该分支由原引擎写入/读取、mesh_ref 摘要不变、准备锁与原值保留的独立回归覆盖，未提高几何审计时限。

组合包 0.2.1 后续仅补充宿主可读的 `context_preparation` 元数据，使保存模型的本地复测先
通过声明的准备 API 创建组合计划，再 probe/run。引擎与三个阶段摘要仍与上述图片验收一致；
另行完成机械和 PCB 的真实 deny-network 执行、MP4/ZIP 校验和重渲染回归。没有因元数据
更新重新训练模型或宣称新的真实 QQ 发送验收。

## 建模 0.2.1 后处理与新图对照

2026-10-01 的后处理修复保留原展示网格，仅在多重数值阈值以内清理模拟微屑，再用
PyMeshLab 2023.12.post3 保拓扑 QEM 和原顶点位置减面；Euler、闭合、连通、法向与
原引擎相交门仍独立检查。仅严格数值微屑可去除，显著独立表面、空腔和外部片段不能
以“保留最大分量”删掉。依赖固定并附完整 GPL-3.0 文本；旧建模 0.2.0 组合导出保持兼容。

既有真实生成网格的副本恢复为 2974 顶点 / 6000 面、Euler -26 不变；明确尺度恢复后
体积约变化 -0.104%，原引擎自交审计通过。新的隔离测试用 0.16 m 和 1000 kg/m³
假设完成 0.25 秒物理模拟、3 秒展示视频完整解码和约 11 MB 数据 ZIP 验证；这属于
旧生成几何的后处理复测，不是重新受理原 QQ 请求或新图片推理成功。

另外真实下载并视觉确认 1080×1393 的中国网红奶蛙参考图；先固定 seed42，再固定
默认 seed0，其余 30 步 / octree128 / chunks2000 相同。两次 worker 分别 98.591 秒、
93.574 秒，展示模型分别 31291 顶点 / 62510 面、29892 顶点 / 59684 面。
严格微屑清理后仍有 13 与 26 个连接分量，均有 1 个退化面，模拟门拒绝。
两次都保留展示 GLB，但没有模拟、视频、ZIP 或 QQ 投递。这证明当前单图生成质量
仍不可靠，不能因为客户端联网或减面修复成功就宣称任意参考图可完整交付。

此前部署基线为 0.2.2、modeling 0.2.1，原 physics 1.4.7 与 simulation/rendering 0.2.0 摘要不变。
客户端代码修复已在空闲时部署，status/check 与 Metal 依赖健康通过；旧队列/pin 保留。
平台送达与具体图片生成成功仍分别验证。

## Original-model preview acceptance (2026-10-01)

Pipeline/modeling/rendering 0.3.0 adds a separate shape-only contract while the
maintenance deployment keeps the physics 1.4.7 and simulation 0.2.0 content pins.
A real restricted external agent processed a new isolated PDF event, performed
public search and image inspection, ran fresh offline MLX inference and the
original-mesh turntable, and released image/video/model ZIP together through a
capture API. The reference was the visually checked Chinese meme Naiwa image
from https://nailong.matchamilk.site/ (SHA256
`1572f2c9642d756e8fcd87abe7668a5c876f15cc7d7c8b090238d4c8f3d073c6`).
It crops the feet and does not show the back; those surfaces remain inferred.

Seed0, 30 steps, octree128, chunks2000 produced 29,892 vertices and 59,684
triangles; the original 26 components and degenerate triangle remain in display
assets. Inference took 145.538 seconds; the complete isolated event took 196.096
seconds. A 640x640 H.264 video had 90 fully decoded frames over 6 seconds, all
with real surface pixels. The complete ZIP retains original GLB/OBJ/JSON,
receipts, reference/source receipt and all five licenses. Units are model_unit;
no size, material, density, mass, solver or numerical measurement was inferred.
Independent image/video/file receipts were obtained from the local capture API,
not a real QQ group. The separately installed host was deployed and checked;
no old event or unknown receipt was resent.

A separate octree256 run took 382.885 seconds and generated 120,261 vertices /
239,954 triangles. Its original-mesh video and host validation passed, but the
first model file send was rejected by the host's legacy filename rule. That
unknown action was retained without retry. A constrained model-/simulation-
filename fix and an entirely new task established the complete acceptance above.
These timings describe specific runs on a 16 GB Mac; they are not fixed resource
or fidelity guarantees. Shape-only neutral rendering has no generated texture.

The installed MLX/NumPy/trimesh toolbox suite ran 189 checks successfully. The
separately maintained host ran 696 checks, 694 passing and two optional Bun CLI
checks skipped. Native rendering, complete ZIPs/licenses, full decoding, cache
isolation/tamper rejection, legacy-stage capability gates and actual OneBot file
request naming were checked. Public repository tests remain independent of the
private host. See the typed [host contract](../toolboxes/pipeline/HOST-INTEGRATION.md).

Final independent public toolbox discovery: 189 checks, 188 passed and one
optional private-host SDK integration skipped. No client, weights, credentials
or private task records are published by this repository.

## Bounded display cleanup verification (0.3.1)

A preview may separately derive a cleaned display from an already generated raw
model; this does not rerun or establish fresh neural inference. The fixed
bounded-floaters/1 policy and exact-subset proof preserve original normalized
coordinates, winding and complete raw GLB/OBJ/JSON backups. Future image previews
use the same audited processing after inference. Unfiltered mode is available.

Acceptance must distinguish synthetic policy/adversarial tests, current-model
postprocessing, fresh model-to-video/archive flow, installed external-host checks
and real platform receipts. A small disconnected semantic feature may satisfy
geometric bounds; cleanup cannot certify image identity, hidden surfaces or
physical eligibility. Actual artifact statistics and release evidence belong
in a dated acceptance record, not an inferred claim from code or health.

## Fresh cleanup flow acceptance (2026-10-02)

A new isolated external-agent event read a PDF, searched public references,
downloaded and visually inspected the Chinese meme Naiwa reference, ran fresh
offline Hunyuan MLX inference and automatically applied conservative cleanup.
Seed0, 30 steps, octree128 and chunks2000 generated 29,892 raw vertices and
59,684 faces across 26 components. The fixed policy removed 25 eligible detached
components (150 vertices, 200 faces); display retained 29,742 vertices and 59,484
faces in one component. Removed surface area was 0.013933% of the raw total;
removed absolute volume was 0.000053057% of the raw total. Retained geometry was
verified as an exact subset, and raw GLB/OBJ/JSON remained in the complete archive.
These geometry ratios do not establish semantic accuracy or physics readiness.

Fresh inference took 139.455 seconds and the complete isolated event 203.063
seconds on the tested 16 GB Mac. The 640x640, 15 fps turntable contained 90 fully
decoded H.264 frames over 6 seconds. The separately installed host accepted the
v2 raw/display/cleanup proof, independently decoded video and verified archive
members/licenses before releasing reference image, video and model ZIP through
a local capture API with independent receipts. No real platform network was used
for those sends; this acceptance does not prove delivery to a QQ group.

The maintained source toolbox suite ran 212 tests successfully. This public
branch passed 212 of 213 checks, with the optional private-host SDK integration
skipped. The separately maintained host ran 709 tests with 707 passing and two optional CLI tests skipped. Cleanup
policy tests covered 19 cases. Adversarial verification checks included altered
raw pins, partial-body deletion, changed retained coordinates, relaxed policies,
changed OBJ/GLB geometry, accessor bounds and float32 quantization, coordinate
system mismatches, omitted raw/cleanup members, false modification disclosures,
wrong renderer scope, empty/transformed scene graphs and incomplete video data.
These host checks remain outside the public repository's implementation.

Pipeline/modeling/rendering 0.3.1 was deployed to the operator's external host
with frozen simulation 0.2.0 and physics 1.4.7 content preserved. Existing queues,
unknown actions, runtime configuration and credentials were retained; no old
request or unknown receipt was replayed. Deployment and health checks are
separate from platform-delivery acceptance. The single-image model remains
untextured, with cropped anatomy and invisible surfaces inferred.
