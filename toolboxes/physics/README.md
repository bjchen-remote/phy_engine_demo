# Physics toolbox

独立物理模块，接收文本任务、工作目录与资源限制；不读取 QQ、账号或聊天配置。
`toolbox.json.agent_api` 向宿主 Agent 暴露现有物理建模 API。Agent 可按用户要求直接构造
scene-v1、创建系统/网格、设置液体、组合约束、声明观测量，校验并修正后运行。
样例只是可选起点，不能因没有相同样例就拒绝。能力边界以 capabilities 与 prepare 为准。

`modeling.py` 管理结构化调用和当前任务的规范化模型；`toolbox_adapter.py` 实现 api/probe/run；
`run_simulation.py` 管理验证与视频，并保留不使用 Agent 的水滴/双摆/三体快速示例入口。
Agent 任务必须先 prepare，通过前不允许退回固定示例。保存的场景决定实际运行参数。
模型不执行用户代码，不能指定主机输出路径。结构化错误可在每次有依据地修改模型后继续修正；不得原样重试失败模型或捏造成功。

## PCB 热仿真

首版 PCB 领域在同一活动包内提供 `pcb_example`、`pcb_validate`、`pcb_prepare`、
`pcb_inspect`、`pcb_query`。`pcb_prepare` 保存带 `domain=pcb_thermal` 的已准备模型；
宿主继续用现有 `physics_simulate` 操作启动，适配器按领域分流到独立的
`pcb_thermal.py` 和 `pcb_video.py`，不进入机械求解内核。

输入是等效二维薄板、上下表面对流和元件矩形功耗，支持稳态与瞬态。输出包括
温度与功耗双面板视频、最高/平均温度、功率和能量平衡。实例见
[`pcb_thermal_demo.json`](pcb_thermal_demo.json)，
模型假设和验收见 [设计文档](../../docs/pcb-thermal-design.md)。功耗必须由用户提供；
实际板材参数需校准，不解析走线、过孔、封装内部和电路电流。
视频编码额外要求本机存在 `ffmpeg` 与 `ffprobe`；适配器在成功前执行完整解码和
大小校验。`toolboxes/tests/test_pcb_integration.py` 在宿主同款 macOS sandbox 中验收完整链路。

构建：在 demo 目录执行 `python3 toolboxes/build_physics.py`，随后 `--check`。
构建包包含原有按需物理手册、样例、预编译求解器及视频工具；无凭据、账号数据或 QQ 依赖。
通过 registry 发布新版本；运行中的任务继续使用原先固定版本。

续改通过 `context` 获取宿主提供的上一轮已验证模型；该数据只在当前任务内，不访问聊天历史。
延长物理时长时保持初态，并同步延长持续至上一轮终点的力场，避免重力提前停止。
一分钟是通常响应目标。独立调用可显式设置 1–300 秒预算；本机 OneBot 宿主不设固定建模、求解或视频超时，仍执行资源、数值和投递校验。`budget.unlimited_runtime` 只能由宿主授权，场景输入不能自行启用。

## Visual and strict acceptance

Set `budget.validation` to `visual` for ordinary videos or `strict` for requested numerical analysis.
The standalone toolbox defaults to visual; omitted fields in the raw engine API retain strict behavior for saved-run compatibility.
`quality_gate.passed` describes delivery; `numerical_passed` describes the original numerical checks.
Visual mode moves only listed precision checks (energy drift, fine constraint residuals, volume drift, water density and iteration budget) into `precision_warnings`.
The diagnostic values and original check booleans remain unchanged. Query answers are unavailable when numerical checks fail.
Complete duration, finite states, valid geometry/contact, consistent diagnostics and media integrity remain mandatory. Finite-budget callers must also meet their execution deadline.
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
