# 建模模块内部架构

建模模块维护两条相接但独立的路径：标准结构化物理模型准备，以及可选本地公开权重的单图到几何。它负责来源、尺度、材料假设、几何审计和完整场景准备，不负责 QQ 授权/发送、不自行积分，也不以语言模型描述替代真实图片生成。操作说明见 [Skill](SKILL.md)，组合 pin 与恢复见 [组合架构](../../ARCHITECTURE.md)。

```text
结构化 scene/spec → pinned engine prepare/validate → pipeline-model/1

当前事件图片 ID + 明确尺度/材料
  → 宿主图片/runtime pin → pipeline.image_modeling
  → modeling_flow dedicated Python 3.12 worker
     contracts/source/checkpoint → alpha 或 pinned U2Net CPU → MLX Metal shape
     → detailed display geometry → metre scale/orientation/audit
     → simulation-only bounded micro-component cleanup
     → independent topology-preserving decimation candidates → simulation topology gate
  → receipt/assets 哈希校验 → pinned engine physics_mesh 审计 → mesh_ref
  → authored full scene / physics_patch → physics_prepare → pipeline-model/1
```

## 实现与责任

| 实现 | 责任 |
|---|---|
| `pipeline_stages/modeling.py` | standard 结构化机械/PCB prepare；固定引擎、预算和 canonical model bundle，不调用生成模型 |
| `pipeline_stages/common.py` | 严格 JSON、canonical 摘要、不可覆盖工件、专用目录和 source_engine |
| `pipeline/image_modeling.py` | 当前事件 image ID、任务路径隔离、runtime/config pin、固定 argv 推理、工件复用、engine mesh audit 和 task mesh_ref |
| `modeling_flow/contracts.py` | 有界严格配置/请求、固定 source/checkpoint、权重和前景模型摘要、类型/数值范围 |
| `modeling_flow/runner.py` | 离线运行时与依赖加载、图片处理、MLX/PyTorch shape inference、双资产分流和回执 |
| `modeling_flow/segmentation.py` | 可选固定 U2Net ONNX 的 CPU 前景估计；保留提供的 alpha，记录掩膜假设 |
| `modeling_flow/meshes.py` | 三角网格导入、米制均匀缩放、面方向/拓扑/体积与模拟预算审计；不调用引擎 |
| `modeling_flow/reduction.py` | 明确阈值的模拟用数值微屑清理与 PyMeshLab 保拓扑 QEM；保留原展示几何，不承担语义识别 |
| QQ `modeling_runtime.py` | 本机操作者 runtime 选择、配置副本、独立锁、首次 disabled/enabled pin 及读取范围；无重建算法 |
| `install_modeling_flow.py` | 仅操作者安装代码/依赖/权重/许可；任务推理不会调用它 |

QQ 和结构化 pipeline 保持 Python 3.9+。可选 MLX worker 采用独立 Apple Silicon Python 3.12+ 和重型依赖；host 导入不加载模型。simulation 包只包含自己的实现与共享助手，不复制 modeling.py 或 modeling_flow，避免建模源码变化无意改变模拟 digest。改变共用 worker/helpers/metadata 会明确影响相关 stage。

## 标准结构化准备

`build_model` 先校验 quality/domain/model/预算，深复制输入，交给同一固定引擎：mechanical 使用 runner.prepare，PCB 使用 validate_pcb_spec 和原 conservative 时间估计。失败不发布模型 bundle；成功写 `pipeline-model/1`，固定 source_engine、normalized model、model_sha256、preparation、assumptions 和 execution_limits。

当前 structured stage 的 standard 不代表 tolerance-certified high。机械路由、材料/连接/耦合能力继续由 engine capabilities 和 prepare 声明；PCB 仍是二维等效热板。queries 必须在 prepare 之前声明，显示数据和导出不能补成遗漏的宏步测量。

## 当前事件隔离与运行时 pin

`pipeline_capabilities.image_modeling` 来自当前任务 runtime pin，区分是否 configured、提供者、generative fidelity 和明确尺度/材料要求。configured 只是安装配置已准入，不表示实际 GPU 推理或图像质量通过。

`modeling_from_image` 只接受当前事件唯一匹配的 image_id 与有界物理/采样参数；宿主验证附件 bytes/hash 并提供受限相对路径。适配器再检查图片与配置快照哈希，按 request/image/runtime/modeling-stage 摘要分配任务内尝试目录。来信不选解释器、模型目录、revision、shell 或网络。

这些 image ID 可来自 QQ 当前/同群引用附件、宿主提取的文档页面/Office 嵌入图片或已准入的公开参考图。
PDF/Office/文本阅读用于获取说明与约束，不能替代对实际图片的识别；识图用于确认参考对象，
流模型才执行单图到几何生成。公开页面的 title/OG/声明尺寸不证明图片与请求相关。
例如中国网红“奶蛙”是黄色、绿色突出圆眼的迷因形象，不能仅按英文 milk frog 建动物模型。
选择和完整解码发生在宿主，本模块不访问页面、下载图片或读取其他事件。
实际 Agent 盲搜仍可能选择无绿色突出圆眼的普通奶龙图；搜索/下载/本机 outbox 入队
技术链成功不等于语义验收。人工核验来源只是检索提示，不能跳过实际图片核对。

相同有效尝试可在重新核验全部 artifact_pins 后复用；存在未完成尝试目录返回 incomplete，保留证据而不重放。模型源/权重在独立可信 runtime 根内，配置和首次 disabled 状态按任务固定，任务重试不跟随新配置。权重不进入 QQ module digest 或 Git。

推理环境显式禁用 Hugging Face/Transformers 网络和遥测，缓存只写当前任务，MPS CPU fallback 关闭。首选 MLX Metal；PyTorch MPS/CPU 必须由操作者明确选择，dtype 也固定。CPU 图片处理、U2Net 和 marching cubes 是披露的预后处理，不是偷偷切换 shape backend。

## 前景与单图生成假设

已有部分透明 alpha 的图片保持原掩膜。opaque 图片默认 full_image，receipt 明确 not_performed；启用 u2net 时必须安装 CPU ONNX Runtime 和 `segmentation/u2net.onnx`，并在 config.foreground_model 固定 path/SHA-256。任务不下载 ONNX 图。

U2Net 将图片预处理为有界张量，在 CPU 推理后输出软显著性掩膜，再恢复原图片大小和 alpha。空掩膜/过小掩膜/依赖缺失/图不兼容均失败。estimated mask 不能证明精确轮廓、选中物体、背面或材料；fine edge 和遮挡仍可能影响生成。

shape inference 使用固定 Hunyuan3D-2mini flow matching 权重和 source commit；当前没有 texture generation、FlashVDM、quantized weights 或 compiled denoiser。seed 与采样设置记录在回执，但不保证跨 backend/环境 bitwise 相同。

单图不能确定物理尺度、密度、壁厚、隐面或材料系数。`physical_extent_m` 是用户指定的生成 mesh 最大 bounding-box extent（QQ 路径）；uniform scale 保持形状比例，原点移到 bbox center，保留生成坐标朝向。模型轴不自动成为摄影/重力轴。mass_estimate 只是在明确 homogeneous solid 假设下以 volume × declared density 得到，不是测量质量。

## 展示网格、减面与双重几何门

生成 geometry 先保留为 display.glb / display.obj / display_mesh.json，展示不等同于物理资格。OBJ 使用有界纯 v/f 三角解析，不读取 mtllib 或外部引用；对不支持多边形等明确拒绝。单独 simulation.obj / simulation_mesh.json 最多 4096 顶点、8192 面。

允许减面时，模拟分支先审查连接分量；只有存在占总面积至少 99% 的主表面，且微屑完全位于
主表面 bbox 内，才允许披露的数值清理。每个分量须同时满足 extent 比例 ≤0.001、面积比例
≤1e-6、绝对体积比例 ≤1e-9；合计面积/绝对体积比例须分别 ≤1e-5/1e-8。
extent 相对主表面最大 extent，面积和绝对体积相对各分量对应量的总和。
逐个删除分量的顶点/面数、bbox、三种比例、阈值和剩余分量数写入 receipt。
这是数值策略，没有识别“无关部件”；大薄片、洞腔、外部碎片、独立物体不会因体积小被移除。
明显多个表面仍会失败；不按“仅保留最大组件”偷偷丢掉物体。禁用减面也禁用此清理。
display 资产始终保持原生成几何，不被清理或低面数资产覆盖。

过密 mesh 使用固定 `pymeshlab==2023.12.post3` 的
`meshing_decimation_quadric_edge_collapse`，从**同一已审查模拟源网格**独立尝试
face budgets 6000、8188、4000。首选 6000 面为原引擎相交/工作量审计留出余量。
明确开启 preserve_topology/normal/boundary，关闭 optimal_placement 和 automatic cleaning，
使用原顶点位置；库参数只提供约束，不是几何安全证明。
每个候选重新解析、按显式物理 extent 均匀缩放、审计面方向并检查闭合、单连通与
源 Euler characteristic 保持，接受首个通过者。每次目标、拓扑结果、缩放和反向信息留在
receipt；没有认证几何误差上界。约束语义见 [PyMeshLab 官方过滤器文档](https://pymeshlab.readthedocs.io/en/latest/filter_list.html#meshing-decimation-quadric-edge-collapse)。

除上述独立披露且受全套阈值限制的微屑清理外，减面不能删除连接组件、填洞、替代基本体、
挤出图像轮廓或偷偷更改用户尺度来过关。闭合一致、单连通且 signed volume 为负时允许整体
翻转面方向并记录；不是局部修补。所有候选失败时保留展示资产和具体拒绝回执，不能自动模拟。

flow mesh gate 检查 finite 坐标、三角索引、重复/退化面、unused vertices、单连通、零 boundary/nonmanifold/inconsistent edges、非零 volume 与预算。它**不认证**自交、图像相似度、CAD tolerance 或真实材料。进入 pipeline 后，原引擎的 `physics_mesh` 再做自身几何审计，包括其支持的相交检查，合格结果存为任务 mesh_ref。

本轮真实网格离线复核：88636 顶点/177320 面的两个分量仅清理内部 6 顶点/8 面微屑，
主网格为 88630/177312；保拓扑减为 2974 顶点/6000 面，Euler characteristic 仍为 -26。
恢复明确 extent 后体积变化约 -0.104%，原引擎闭合/连通/一致方向/相交审计通过。
这只是该网格的几何验收，不证明不同图片都会成功，也没有重跑或发送真实 QQ 请求。

PyMeshLab 采用 [GPL-3.0](https://github.com/cnr-isti-vclab/PyMeshLab/blob/main/LICENSE)，
完整许可由安装器保留，随实际使用的建模数据导出保存，依赖版本进入 receipt。
其许可与 Tencent 权重、MLX 移植、前景模型和参考图的许可分别记录；不能把保存许可文本
理解为取消各组件的使用/分发义务。

## Readiness 层级与全场景准备

| 结果 | 实际含义 |
|---|---|
| 本机 flow worker `ready_to_simulate=true` | bounded simulation mesh 通过该 worker 的 topology/budget gate；没有完整 scene |
| QQ `modeling_from_image ok=true / geometry_verified=true` | mesh 经原引擎 geometry audit 并存 task mesh_ref；`ready_to_simulate=false` |
| `physics_prepare ok=true / ready_to_simulate=true` | mesh 已进入 authored full scene，边界/运动/质量/材料/查询/预算及固定 pipeline plan 准备通过 |
| 完成 simulation / numerical gate | 当前假设下数值验收结果；不认证图片重建的真实物理精度 |

Agent 必须通过 physics_patch 引用 mesh_ref，明确初态、dynamic/static 类型、质量/碰撞/其他物理参数，再 prepare。不能复制超长 vertex 数组到 tool 参数，也不能将几何成功、receipt 或清晰 GLB 当作完整场景已准备。

## 回执、导出与失败

receipt 固定 upstream commit、checkpoint revision/inventory、配置/request/image hashes、预处理、sampling、backend/device/dtype/依赖、尺度、材料、假设、display/simulation audits、所有减面尝试及输出工件哈希。receipt 自身摘要由外层结果单独记录，避免循环摘要。

公开参考图的宿主 receipt 可同时记录 HTTP 声明 `source_mime` 与真实容器
`decoded_source_mime`。例如 JPEG 声明、WebP 魔数的 1080×1393 奶蛙参考图，
宿主按固定 WebP 解码器完整解码并规范化 PNG 后才交本模块；四种支持图片间的声明差异
不等于任意二进制准入。旧 source receipt 可验证；生成模块始终只接收验证过的 JPEG/PNG。

原引擎 mesh provenance 的 notes 绑定 source image 与 receipt 哈希。`used_modeling_assets` 只收集当前 scene 确实引用 receipt 的模型数据/回执，重新校验 pin 后加入最终 data.zip；相关第三方许可作为使用证据保存。模型路径只保留本机，Agent 得到 model_ref/mesh_ref/receipt_ref/display_model_ref。

依赖/权重/source/GPU/图片/推理失败没有伪造 geometry；展示可生成而模拟门失败时，保留 display 和原因，禁止 downgrade 成随意 primitive。原有结构化工具不受 optional runtime 缺失影响。

## 验证与扩展

contracts/mesh/scale/receipt/foreground/decimation 测试不下载权重或初始化 GPU；fake inference 只验证桥接和失败边界。真实 Metal health、透明图推理、opaque U2Net 推理、physics handoff、完整视频/数据以及 QQ 平台回执分别需要独立证据。健康检查返回 inference_verified=false，不能替代真实生成。

新增质量、模型或 provider 要更新实际实现、pin/许可、能力发现、typed 参数、receipt 和对应验收。生成训练属于后续研究：当前 worker 调用公开预训练权重，没有训练/数据集流水线，也不宣称复刻商业服务的私有算法。

本轮源构建版本为 modeling 0.2.1、完整 pipeline 0.2.2；simulation/rendering 保持 0.2.0，
physics 引擎保持 1.4.7。当前文档属于 modeling 包摘要，修改后须重新构建和发布新组合，
旧任务 pin 不切换。本轮文档记录源码与隔离验收；本机部署尚待完成，未验证真实 QQ 交付。
