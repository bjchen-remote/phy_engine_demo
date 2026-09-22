# Agent 操作指南

目标：先把物理参数组织成 system spec 或 scene-v1，在约定预算内交付 MP4 和预声明的定量答案。本文件负责接入与操作；运行决策见 [Skill](../agent/physics-simulation/SKILL.md)，开发见 [架构](architecture.md)。它们对应本仓库的独立模拟器。

## 接入

在含 `physics_demo/` 的项目根目录运行。需要 Python 3.9+，native 需要 C11 编译器，MP4 需要 macOS clang/AVFoundation/ImageIO。普通使用无需第三方 Python 包或模型 SDK；首次运行编译，之后使用源码哈希缓存。

将包含十二个工具的 [tools.json](../agent/tools.json) 注册给外部 agent，提供 [Skill](../agent/physics-simulation/SKILL.md)。可经 Python `physics_demo.api.call_tool` 或 CLI `tool` 分发：

```sh
python3 -m physics_demo tool '{"tool":"physics_capabilities","arguments":{}}'
```

工具返回 `protocol_version`、`status`、`next_action`。后者只建议下一动作，不会替调用方执行。普通 CLI 子命令和直接 runner 调用不保证带这三个引导字段；统一协议使用 `call_tool`/`tool`。

| 工具 | 输入要点 |
|---|---|
| `physics_capabilities` | 无参数；每个新任务读取一次 |
| `physics_liquid` | 已有完整场景后传 `preset` 与目标 `entity_id`；返回精确液体字段、边界说明和可交给 patch 的操作，不返回 `scene_json` |
| `physics_system` | `spec_json`：系统类型与长度、质量、角度等物理参数；返回可 prepare 的场景，不积分 |
| `physics_example` | 稳定 `name`，不带 `.json` |
| `physics_mesh` | `spec_json`：有界 recipe 或原始顶点/三角面；返回 `mesh_json` 与 audit |
| `physics_patch` | `scene_json`、`operations`；操作值用 JSON 字符串 `value_json` |
| `physics_prepare` | `scene_json`、可选 `budget_seconds`；首选的合并校验/规划入口 |
| `physics_validate` | `scene_json`；低层校验/兼容入口 |
| `physics_estimate` | `scene_json`、可选 `budget_seconds`；`ok` 不等于预算可行 |
| `physics_simulate` | prepare 返回的精确 `scene_json`、专用 `output_dir`、相同预算 |
| `physics_inspect` | `result_path` 指向 run 目录或 `result.json`，用于恢复/复查 |
| `physics_query` | `result_path`、可选已声明的 `query_id`；省略/null 返回全部摘要 |

## 最小完整调用

下面示例保留每一步判断，直接使用公开 API；普通模拟会生成视频。

```python
import json
from physics_demo.api import call_tool

capabilities = call_tool("physics_capabilities", {})
system = call_tool("physics_system", {"spec_json": json.dumps({
    "type": "double_pendulum", "lengths": [1, 1], "masses": [1, 1],
    "angles": [2.0, 2.4], "angular_velocities": [0, 0], "duration": 8,
})})
if not system["ok"]:
    raise RuntimeError(system)
prepared = call_tool("physics_prepare", {
    "scene_json": system["scene_json"], "budget_seconds": 60,
})
if not (prepared["ok"] and prepared["ready_to_simulate"]):
    raise RuntimeError(prepared)
print(prepared["agent_report"])  # 核对调整没有违背用户明确要求
run = call_tool("physics_simulate", {
    "scene_json": prepared["scene_json"],
    "budget_seconds": 60, "output_dir": "runs/first-pendulum",
})
if not (run["ok"] and run["quality_gate"]["passed"]):
    raise RuntimeError(run)
answer = call_tool("physics_query", {
    "result_path": "runs/first-pendulum", "query_id": "bob2-x",
})
print(answer)
```

`example` CLI 输出是响应对象，不能把整份响应当 scene 输入。CLI simulate 成功退出码为 0，失败为 2；详细原因读 JSON。成功 simulate 已返回经过检查的摘要，无需再 inspect；恢复已有产物时才使用 inspect。`--no-video` 是诊断选项，不能代替正常交付。

## 修改和定量问题

单摆/双摆先用 [系统参数规范](../agent/physics-simulation/references/systems.md) 和 `physics_system`，改变配置中的 `lengths/masses/angles/angular_velocities` 后重新生成、prepare、simulate。角度从世界 −Y 向 +X 计量，第二杆采用绝对角而非关节相对角；不编辑示例文件来改变系统参数。Python 可用 `build_system / run_system / load_run`，已保存的 position/speed 宏步观测由 `analysis.pendulum_report / compare_pendulums` 分析。

其他类型可从 [场景目录](../agent/physics-simulation/references/interesting-scenes.md) 选择最接近的起点。只修改请求涉及的值，保留并披露有关的示例假设。字段见 [scene-v1](../agent/physics-simulation/references/scene-v1.md) 与 [公开 Schema](../agent/scene-v1.schema.json)。

用户说水、蜂蜜、胶水/黏胶/白胶、铅水/熔融铅时，先取得完整场景并确认目标流体实体 ID，再按 [液体预设](../agent/physics-simulation/references/liquid-presets.md) 调 `physics_liquid`，把返回的 `patch_arguments.operations` 与已有 `scene_json` 一起交给 `physics_patch`。材料工具本身不返回场景；没有场景时先加载示例、构建适用系统或编写 scene-v1。非水预设必须使用 `auto` 或 `native`；最终报告预设名、有效黏度/表面张力以及单相、无温度/相变/真实密度等边界。

其他弹簧、杆、绳网络先读 [连接网络](../agent/physics-simulation/references/connections.md)，从对应示例修改质量、自然长度、刚度、阻尼和显式加速度场；点质量不受 `world.gravity` 驱动。

描述/图片建模、软体碰撞或插入先读 [网格建模](../agent/physics-simulation/references/mesh-modeling.md)。视觉 agent 提取轮廓并明确纵深假设，`physics_mesh` 负责构建和审计；复杂隐藏面可由强 agent 直接给顶点/三角面并记录 imagined provenance。将返回 `mesh_json` 解析为实体的 `mesh`，或直接作为 patch 的 `value_json`。服务本身不读图；原网格示例独立运行。需要水/沙反作用、刚体姿态或偏心弹簧力矩时读 [混合耦合](../agent/physics-simulation/references/coupling.md)，显式设置 `coupling:{}` 或使用 `rigid_body`。实体 `solid` 连接是实际半径的直胶囊，只有点质量两端可将正质量各分一半；不宣称真实螺旋线材、完整压力 FSI 或定量浮力。

“高度加倍”先读取已有 `scene.normalized.json` 中的中心 Y，再 patch；默认高度指中心，明确底部高度/净空时才换算半径或半尺寸。工具操作示例：

```json
{"op":"replace","path":"/entities/@drop/shape/center/1","value_json":"2.0"}
```

CLI patch 文件将 `value_json` 换成实际 JSON `value`。`@drop` 按 ID 选择，比数组下标稳定；多项操作原子执行，任一失败都不修改输入。每次改动后重新 prepare，不能复用旧预算。

| 请求 | 运行前声明 | 回答范围 |
|---|---|---|
| 双摆对初值敏感吗 | 由系统工厂预声明 bob 的位置/速度系列；分别运行微扰初值与 dt/2 对照 | 有限窗口轨迹差异和数值敏感性；不直接宣称 Lyapunov 指数或混沌证明 |
| 三体稳定吗 | `nbody_stability`，给观察时长、最大质心半径、最小两体距离 | 本窗口的采样判据，不是永久稳定证明 |
| 水滴多久到半径 1 | `threshold` + `spread_radius`，给平面、原点和 1 m 阈值 | 全粒子中心投影半径，包含空中飞溅 |
| 两滑块多久分离 | 独立 slider 场景，`surface_gap` + 正间隙，可加 `hold_for` | 同一 X 轨道的理想平动；`gap >= 0` 包含初始接触 |
| 某个量随时间变化 | `series` + metric | 完整求解状态的宏步采样 |
| 弹簧伸长/力/能量或杆绳长度 | `series` / `threshold` + connection metric | 有符号轴向力、弹簧储能或端点长度；完整定义见连接网络手册 |
| 软体压缩/位移多少 | 网格 `volume_ratio` / `max_displacement` / `max_edge_strain` | 表面代理模型；无任意网格表面间隙、应力或插入力 |

先读 [定量查询手册](../agent/physics-simulation/references/quantitative-queries.md) 再写 `scene.queries`。已有运行缺少问题声明时，要加声明并重跑；不能从视频或渲染子集补测。事件答案保留单位、状态、窗口和时间区间，未观测到不能解释成永不发生。

## 交付和排错

运行前展示 `agent_report` 的物理时长、p50–p90 计算时间、硬预算、实际后端/质量/粒子数/spacing、内存和调整。`ready_to_simulate=false` 或调整违反明确要求时先修正。规划可能粗化分辨率，但不知道哪些值来自用户；允许取舍的授权决定是否能接受调整。

运行后要求 `status=completed`、`ok=true`、`quality_gate.passed=true`，查看 MP4 初态、首次碰撞和末帧。交付实际 MP4 路径、物理/播放时长及比例、实测墙钟时间、后端/粒子/spacing、假设与模型边界；有查询再附验证后的答案。质量门及错误修复表仅维护在 [tool-results](../agent/physics-simulation/references/tool-results.md)。自动纠错最多两次，不原样重试不支持的能力。

更高粒子细节优先减小 spacing，网格细节需要显式重建更密拓扑；提高 FPS 只增加展示采样。时间误差检查减半 `world.dt`，粒子问题另查 spacing；不能靠随意增大 dt、删除检查或静默降级达成预算。具体 [限制与估时](../agent/physics-simulation/references/capability-boundaries.md)、[算法差异](../agent/physics-simulation/references/solver-routing.md) 各有独立参考。

开发验证入口：

```sh
python3 -m unittest discover -s tests -v
PYTHONPATH=. python3 benchmarks/benchmark_queries.py --out runs/query-refinement-01
rg -n '^def |^class |^static ' physics_demo
```

按改动选择合同、独立数值/几何、边界输入与真实视频检查；扩展清单和 ABI 所有权统一见 [架构地图](architecture.md)。
